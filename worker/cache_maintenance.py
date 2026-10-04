"""Conservative maintenance of explicitly managed local caches.

The Worker owns the global task lock for clean, policy writes, references and
auto. This module never deletes user outputs, inputs, models or journal files.
Preview snapshots bind the selection and each file's identity and contents;
cleanup rechecks both those fingerprints and current recovery references.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import sqlite3
import stat
import sys
import time
import urllib.parse
import uuid
from typing import Any

from automation_core import AutomationError, redact_text, stable_source_ref

CATEGORIES = ("source_evidence", "asr_diagnostics", "success_checkpoints",
              "failed_recovery", "disposable_cache")
PROTECTED_STATUSES = {"waiting", "queued", "running", "failed", "interrupted", "cancelled", "partial", "partial_failed", "timeout"}
STATUSES = PROTECTED_STATUSES | {"completed", "no_changes", "succeeded", "success"}
KIND_CATEGORIES = {
    "source": "source_evidence", "source-by-note": "source_evidence",
    "timing-by-note": "source_evidence", "asr": "asr_diagnostics",
    "review": "asr_diagnostics", "proofread-rejected": "failed_recovery",
    "proofread-checkpoints": "success_checkpoints",
}
DEFAULT_POLICY = {"auto_enabled": False, "retention_days": 30,
                  "categories": ["disposable_cache"]}
MAX_JSON_BYTES = 32 * 1024 * 1024
PREVIEW_LIFETIME = 1800


class CacheMaintenanceError(AutomationError):
    def __init__(self, message: str, error_code: str = "CACHE_REQUEST_INVALID", retryable: bool = False):
        super().__init__(message, error_code, retryable)


def _utc(timestamp: float | None = None) -> str:
    return dt.datetime.fromtimestamp(timestamp if timestamp is not None else time.time(), dt.timezone.utc).isoformat(timespec="seconds")


def _absolute(value: Any) -> pathlib.Path:
    absolute = os.path.abspath(os.path.expanduser(str(value)))
    # macOS exposes these system directories through fixed platform aliases.
    # Normalize those aliases only; user-created symlinks remain unsafe.
    if sys.platform == "darwin":
        for prefix in ("/var", "/tmp", "/etc"):
            if absolute == prefix or absolute.startswith(prefix + "/"):
                absolute = "/private" + absolute
                break
    return pathlib.Path(absolute)


def _roots(env: dict) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    default = pathlib.Path.home() / "Library/Application Support/Local Note Studio"
    app = _absolute(env.get("LOCAL_NOTE_STUDIO_APP_DATA_DIR") or
                    (pathlib.Path(str(env["LOCAL_NOTE_STUDIO_STATE_DIR"])).parent if env.get("LOCAL_NOTE_STUDIO_STATE_DIR") else default))
    state = _absolute(env.get("LOCAL_NOTE_STUDIO_STATE_DIR") or app / "state")
    return app, state, state / "cache-maintenance"


@contextlib.contextmanager
def _parent_fd(path: pathlib.Path, create: bool = False):
    """Pin every parent directory; refuse symlinks even during deletion races."""
    path = _absolute(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            try:
                new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=fd)
                new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new_fd
        yield fd
    finally:
        os.close(fd)


def _safe_location(path: pathlib.Path) -> bool:
    try:
        with _parent_fd(path) as fd:
            info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            return not stat.S_ISLNK(info.st_mode)
    except FileNotFoundError:
        return not any(part.is_symlink() for part in (path, *path.parents))
    except OSError:
        return False


def _read_bytes(path: pathlib.Path, limit: int = MAX_JSON_BYTES) -> bytes:
    with _parent_fd(path) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise OSError("not a bounded regular cache file")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                return handle.read(limit + 1)
        finally:
            os.close(fd)


def _read_json(path: pathlib.Path) -> dict | None:
    try:
        payload = json.loads(_read_bytes(path).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def _atomic_json(path: pathlib.Path, payload: dict) -> None:
    if not _safe_location(path.parent) or not _safe_location(path):
        raise CacheMaintenanceError("cache state path is unsafe", "CACHE_STATE_UNSAFE")
    with _parent_fd(path, create=True) as directory:
        # The temporary file is created in the pinned directory, not via a path
        # whose parents may have been replaced after validation.
        name = ".cache-" + uuid.uuid4().hex
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.rename(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(name, dir_fd=directory)


def _number(value: Any, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return int(value) if integer and int(value) == value else (None if integer else value)


def _identifier(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) and not value.lower().startswith("sk-"):
        return value
    return None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _source_ref(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if re.fullmatch(r"(?:BV[A-Za-z0-9]+|opus/\d+|space\.bilibili\.com/\d+|(?:file|url):[a-f0-9]{16,64})", value):
        return value
    return stable_source_ref(value)


def _clean_source(value: Any, redact: bool = True) -> str:
    value = str(value or "")[:16384]
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme in {"http", "https"}:
            host = parsed.hostname or ""
            if parsed.port:
                host += ":" + str(parsed.port)
            return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return ""
    return redact_text(value) if redact else value


def _sanitize_reference(entry: Any) -> dict:
    if not isinstance(entry, dict) or not isinstance(entry.get("request"), dict):
        raise CacheMaintenanceError("references require entries with a request object")
    status_value = entry.get("status")
    if not isinstance(status_value, str) or status_value not in STATUSES:
        raise CacheMaintenanceError("unknown reference status")
    request = entry["request"]
    identity_source = _clean_source(request.get("source"), redact=False)
    source = redact_text(identity_source)
    clean_request = {"task": _identifier(request.get("task")) or "unknown", "source": source}
    # Compute identities before redaction and preserve them on registry reads.
    # A local filename may itself resemble a credential; redacting it must not
    # break protection of a queued task that points at that source.
    clean_request["source_ref"] = _source_ref(request.get("source_ref")) or _source_ref(identity_source)
    prior_identity = request.get("source_identity")
    clean_request["source_identity"] = prior_identity if isinstance(prior_identity, str) and re.fullmatch(r"[a-f0-9]{64}", prior_identity) else (hashlib.sha256(identity_source.encode()).hexdigest() if identity_source else None)
    if request.get("source_is_directory") is True or (identity_source and pathlib.Path(identity_source).is_dir()):
        clean_request["source_is_directory"] = True
    for key in ("collection_id", "collection_type", "up_mid"):
        value = request.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and _identifier(str(value)):
            clean_request[key] = str(value)
    if _identifier(request.get("run_id")):
        clean_request["run_id"] = request["run_id"]
    for key in ("output_dir", "source_path", "note_path", "recovery_dir"):
        value = request.get(key)
        if isinstance(value, str) and value:
            clean_request[key] = str(_absolute(value))
    entry_id = str(entry.get("id") or "")
    if not _identifier(entry_id):
        entry_id = "ref:" + hashlib.sha256(entry_id.encode()).hexdigest()[:24]
    result = {"id": entry_id, "status": status_value, "request": clean_request}
    if isinstance(entry.get("outputs"), list):
        result["outputs"] = [str(_absolute(item)) for item in entry["outputs"][:1000] if isinstance(item, str)]
    return result


def _load_history(state: pathlib.Path) -> tuple[list[dict], bool]:
    path = state / "automation-history.sqlite3"
    if not path.exists() and not path.is_symlink():
        return [], False
    if not _safe_location(path):
        return [], True
    try:
        uri = "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
        records = []
        for row in rows:
            entry = dict(row)
            if entry.get("task") == "cache-manage":
                continue
            try:
                request = json.loads(entry.get("request_json") or "{}")
                result = json.loads(entry.get("result_json") or "{}")
            except (ValueError, TypeError):
                return records, True
            if not isinstance(request, dict) or not isinstance(result, dict):
                return records, True
            records.append({"id": str(entry.get("run_id") or ""), "status": entry.get("status"),
                            "request": request, "result": result, "task": entry.get("task"),
                            "started_at": entry.get("started_at"), "finished_at": entry.get("finished_at")})
        return records, False
    except (OSError, sqlite3.Error):
        return [], True


def _load_references(state: pathlib.Path, maintenance: pathlib.Path) -> tuple[list[dict], bool, list[dict]]:
    references = []
    path = maintenance / "references.json"
    broken = False
    if path.exists() or path.is_symlink():
        saved = _read_json(path)
        if not saved or not isinstance(saved.get("entries"), list):
            broken = True
        else:
            try:
                references = [_sanitize_reference(entry) for entry in saved["entries"]]
            except CacheMaintenanceError:
                broken = True
    history, history_broken = _load_history(state)
    references.extend(history)
    return references, broken or history_broken, history


def _walk(root: pathlib.Path):
    if not root.is_dir() or not _safe_location(root):
        return
    for directory, subdirs, files in os.walk(root, followlinks=False):
        parent = pathlib.Path(directory)
        symbolic_directories = sorted(name for name in subdirs if (parent / name).is_symlink())
        subdirs[:] = sorted(name for name in subdirs if not (parent / name).is_symlink())
        for name in sorted(files):
            yield parent / name
        # Never descend into a symlink directory. The inventory still exposes
        # it as a protected entry so the reason for excluded bytes is visible.
        for name in symbolic_directories:
            yield parent / name


def _effective_config(value: Any) -> dict | None:
    """Export only configuration fields with established nonsecret semantics."""
    if not isinstance(value, dict):
        return None
    result = {}
    safe_strings = {"model", "backend", "runtime_backend", "asr_engine", "asr_model", "subtitle_strategy",
                    "prompt_version", "rules_version", "video_output_mode", "thinking_mode"}
    safe_scalars = {"timeout", "timeout_seconds", "max_retries", "cooldown", "cooldown_delay", "max_tokens",
                    "chunk_chars", "min_chars", "max_split_depth", "call_budget", "enable_thinking",
                    "keep_original_subtitles", "extract_keyframes", "incognito_mode", "stock_terms",
                    "temperature", "top_p", "completion_tokens", "thinking", "context_chars_each_side",
                    "quality_retries", "retries", "cooldown_seconds", "max_output_tokens"}
    safe_groups = {"general", "proofread", "summary", "llm", "asr", "organization", "organize", "stages"}
    for key, item in value.items():
        lower = str(key).lower()
        if lower in safe_groups and isinstance(item, dict):
            result[key] = _effective_config(item)
        elif lower in safe_strings and isinstance(item, str) and len(item) <= 128 and re.fullmatch(r"[A-Za-z0-9_.:@+-]+", item) and "sk-" not in item.lower():
            result[key] = item
        elif lower in safe_scalars and isinstance(item, bool):
            result[key] = item
        elif lower in safe_scalars and _number(item) is not None:
            result["max_tokens" if lower == "max_output_tokens" else key] = item
    return result


def _diagnostic_record(history: dict | None, telemetry: dict | None) -> dict:
    history, telemetry = history or {}, telemetry or {}
    request = history.get("request") or {}
    metrics = telemetry.get("metrics") if isinstance(telemetry.get("metrics"), dict) else {}
    task = _identifier(telemetry.get("task") or history.get("task") or request.get("task"))
    status_value = history.get("status") if history else telemetry.get("status")
    status_value = status_value if isinstance(status_value, str) and status_value in STATUSES else "unknown"
    record = {"run_id": _identifier(telemetry.get("run_id") or history.get("id")), "task": task,
              "source_ref": _source_ref(telemetry.get("source_ref") or request.get("source")),
              "status": status_value,
              "started_at": _timestamp(telemetry.get("created_at") or history.get("started_at")),
              "finished_at": _timestamp(history.get("finished_at")), "stage_durations": None,
              "effective_config": _effective_config(telemetry.get("effective_config")), "retries": None}
    for key in ("model_calls", "asr_calls", "llm_calls", "prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cache_hits"):
        record[key] = _number(metrics.get(key), integer=True)
    record["duration_seconds"] = _number(metrics.get("duration_seconds"))
    if record["finished_at"] is None and record["status"] in STATUSES - {"waiting", "queued", "running"}:
        record["finished_at"] = _timestamp(telemetry.get("updated_at"))
    stages = metrics.get("stages")
    if isinstance(stages, list):
        record["stage_durations"] = {_identifier(row.get("stage")): _number(row.get("duration_seconds"))
                                     for row in stages if isinstance(row, dict) and _identifier(row.get("stage"))}
    retries = metrics.get("retries")
    if isinstance(retries, list):
        record["retries"] = [{"stage": _identifier(row.get("stage")), "reason": _identifier(row.get("reason")),
                               "count": _number(row.get("count"), integer=True)}
                              for row in retries if isinstance(row, dict) and _identifier(row.get("stage"))
                              and _identifier(row.get("reason")) and _number(row.get("count"), integer=True) is not None]
    return record


def _diagnostics(state: pathlib.Path, history: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    telemetry = {}
    root = state / "diagnostics"
    if _safe_location(root) and root.is_dir():
        for path in sorted(root.glob("*.json")):
            payload = _read_json(path)
            if payload and _identifier(payload.get("run_id")):
                telemetry[payload["run_id"]] = payload
    history_by_id = {item["id"]: item for item in history}
    records = [_diagnostic_record(history_by_id.get(run_id), telemetry.get(run_id))
               for run_id in sorted(set(history_by_id) | set(telemetry))]
    return records, telemetry


def _diagnostic_storage(state: pathlib.Path) -> dict:
    """Report audit storage separately; summaries/events remain recoverable.

    Audit events and their aggregate are one evidence set. File-by-file cache
    deletion would leave a partial event set that can silently change future
    aggregates, so this retention policy intentionally excludes them from
    maintenance candidates.
    """
    root = state / "diagnostics"
    if not root.exists() and not root.is_symlink():
        return {"diagnostics_bytes": 0, "diagnostics_count": 0}
    if not _safe_location(root):
        return {"diagnostics_bytes": None, "diagnostics_count": None}
    size, count = 0, 0
    for path in _walk(root):
        try:
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                size += info.st_size
                count += 1
        except OSError:
            return {"diagnostics_bytes": None, "diagnostics_count": None}
    return {"diagnostics_bytes": size, "diagnostics_count": count}


def _beneath(path: str | pathlib.Path, root: str | pathlib.Path) -> bool:
    try:
        _absolute(path).relative_to(_absolute(root))
        return True
    except ValueError:
        return False


def _output_roots(references: list[dict], env: dict) -> set[pathlib.Path]:
    roots = set()
    for reference in references:
        request = reference.get("request") or {}
        if request.get("output_dir"):
            roots.add(_absolute(request["output_dir"]))
    if env.get("BILIBILI_OUTPUT_DIR"):
        roots.add(_absolute(env["BILIBILI_OUTPUT_DIR"]))
    return roots


def _transactions(roots: set[pathlib.Path]) -> tuple[list[dict], bool]:
    journals = []
    broken = False
    for output in roots:
        journal_root = output / ".local-note-studio-transactions"
        if not journal_root.exists() and not journal_root.is_symlink():
            continue
        if not _safe_location(journal_root):
            broken = True
            continue
        for path in sorted(journal_root.glob("*.json")):
            journal = _read_json(path)
            if not journal:
                broken = True
            elif journal.get("state") != "complete":
                # A journal may name arbitrary paths. Its references protect
                # files but never expand the deletion whitelist.
                journals.append(journal)
    return journals, broken


def _paths_from(value: Any) -> set[str]:
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"note_path", "source", "source_path", "output_dir", "stage_dir", "staged", "target", "backup", "recovery_dir", "path"} and isinstance(item, str) and item.startswith("/"):
                result.add(item)
            if isinstance(item, (list, dict)):
                result.update(_paths_from(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_paths_from(item))
    return result


def _references_for(path: pathlib.Path, payload: dict, metadata: dict, references: list[dict], journals: list[dict]) -> list[dict]:
    run_id = str(metadata.get("run_id") or "")
    source = _source_ref(metadata.get("source_ref") or payload.get("source_ref") or payload.get("source"))
    raw_source = metadata.get("source_ref") or payload.get("source_ref") or payload.get("source")
    source_parent_refs = set()
    if isinstance(raw_source, str) and raw_source.startswith("/"):
        for local_source in {pathlib.Path(os.path.abspath(raw_source)), _absolute(raw_source)}:
            source_parent_refs.update(_source_ref(str(parent)) for parent in (local_source, *local_source.parents))
    note = metadata.get("note_path") or payload.get("note_path")
    identity = metadata.get("source_identity")
    metadata_refs = metadata.get("references") if isinstance(metadata.get("references"), list) else []
    run_ids = {run_id} if run_id else set()
    source_ids = {identity} if isinstance(identity, str) and identity else set()
    for item in metadata_refs:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        if item.get("type") == "run":
            run_ids.add(item["id"])
        elif item.get("type") == "source":
            source_ids.add(item["id"])
    linked = []
    for reference in references:
        request = reference.get("request") or {}
        ref_source = str(request.get("source") or "")
        output = request.get("output_dir")
        result = reference.get("result") if isinstance(reference.get("result"), dict) else {}
        ref_stable = _source_ref(request.get("source_ref") or result.get("source_ref") or ref_source)
        related = bool(run_ids & {reference.get("id"), request.get("run_id"), result.get("run_id")})
        related |= bool(source and source == ref_stable)
        related |= bool(ref_stable and ref_stable in source_parent_refs)
        if isinstance(raw_source, str) and raw_source.startswith("/") and ref_source.startswith("/"):
            related |= _beneath(raw_source, ref_source)
        ref_identities = {hashlib.sha256(ref_source.encode()).hexdigest(),
                          hashlib.sha256((ref_stable or ref_source).encode()).hexdigest()}
        if isinstance(request.get("source_identity"), str):
            ref_identities.add(request["source_identity"])
        related |= bool(source_ids & ref_identities)
        related |= bool(note and output and _beneath(note, output))
        related |= any(_beneath(path, location) or str(path) == str(location) for location in _paths_from(reference))
        if reference.get("status") in PROTECTED_STATUSES:
            collection = request.get("task") in {"bilibili-favorite", "bilibili-up-video", "bilibili-up-sync", "bilibili-up-opus"}
            managed_video = "transcripts" in path.parts or "opus-image-analysis-cache" in path.parts
            unknown_directory_membership = request.get("source_is_directory") is True and not (isinstance(raw_source, str) and raw_source.startswith("/"))
            # Collection membership is determined only during acquisition.
            # Until then retain potentially reusable video evidence even when
            # a queued batch points at a new output root.
            related |= bool(managed_video and (collection or unknown_directory_membership))
        if related:
            linked.append({"type": "task", "id": _identifier(reference.get("id")) or "unknown",
                           "run_id": _identifier(request.get("run_id") or result.get("run_id") or reference.get("id")),
                           "status": reference.get("status"), "protects": reference.get("status") in PROTECTED_STATUSES})
    for journal in journals:
        related = bool(journal.get("run_id") in run_ids)
        related |= any(_beneath(path, location) or (note and _beneath(note, location)) for location in _paths_from(journal))
        if related:
            linked.append({"type": "transaction", "id": _identifier(journal.get("run_id")) or "unknown",
                           "status": "recovery", "protects": True})
    return linked


def _policy(maintenance: pathlib.Path) -> dict:
    path = maintenance / "policy.json"
    if not path.exists() and not path.is_symlink():
        return {**DEFAULT_POLICY, "categories": list(DEFAULT_POLICY["categories"])}
    saved = _read_json(path)
    try:
        return _validate_policy(saved or {}) if saved else {**DEFAULT_POLICY, "categories": list(DEFAULT_POLICY["categories"])}
    except CacheMaintenanceError:
        return {**DEFAULT_POLICY, "categories": list(DEFAULT_POLICY["categories"])}


def _selection(payload: dict) -> dict:
    categories = payload.get("categories")
    days = payload.get("older_than_days")
    if not isinstance(categories, list) or not categories or any(item not in CATEGORIES for item in categories):
        raise CacheMaintenanceError("select at least one known cache category")
    if isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days) or not 0 <= days <= 3650:
        raise CacheMaintenanceError("older_than_days must be between 0 and 3650")
    return {"categories": sorted(set(categories)), "older_than_days": days}


def _validate_policy(payload: dict) -> dict:
    if not isinstance(payload.get("auto_enabled"), bool):
        raise CacheMaintenanceError("auto_enabled must be a boolean")
    selection = _selection({"categories": payload.get("categories"), "older_than_days": payload.get("retention_days")})
    return {"auto_enabled": payload["auto_enabled"], "retention_days": selection["older_than_days"], "categories": selection["categories"]}


def _inventory(env: dict) -> tuple[dict, dict[str, dict]]:
    app, state, maintenance = _roots(env)
    references, broken, history = _load_references(state, maintenance)
    diagnostics, telemetry = _diagnostics(state, history)
    output_roots = _output_roots(references, env)
    journals, journal_broken = _transactions(output_roots)
    broken |= journal_broken
    sources = []
    for kind, category in KIND_CATEGORIES.items():
        root = state / "transcripts" / kind
        sources.append((root, category, kind))
    sources.extend([(state / "ocr-checkpoints", "success_checkpoints", "ocr-checkpoints"),
                    (state / "opus-image-analysis-cache", "disposable_cache", "opus-image-analysis-cache"),
                    (app / "cache" / "audio", "disposable_cache", "audio"),
                    (state / "recovery", "failed_recovery", "recovery")])
    sources.extend((root / ".local-note-studio-staging", "failed_recovery", "staging") for root in output_roots)
    items, internal = [], {}
    authoritative = {run_id: data.get("status") for run_id, data in telemetry.items()}
    authoritative.update({item["id"]: item.get("status") for item in history})
    for root, category, kind in sources:
        for path in _walk(root):
            try:
                info = path.lstat()
            except OSError:
                continue
            is_regular = stat.S_ISREG(info.st_mode)
            payload = _read_json(path) if is_regular and path.suffix == ".json" else None
            payload = payload or {}
            metadata = payload.get("cache_metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            status_value = authoritative.get(metadata.get("run_id")) or metadata.get("status") or payload.get("status") or "unknown"
            if status_value not in STATUSES:
                status_value = "unknown"
            row_category = category
            if category == "success_checkpoints" and status_value in PROTECTED_STATUSES:
                row_category = "failed_recovery"
            linked = _references_for(path, payload, metadata, references, journals)
            reasons = []
            if not is_regular or not _safe_location(path):
                reasons.append("unsafe_or_symbolic_link")
            if info.st_nlink > 1:
                reasons.append("hard_link")
            if not metadata or metadata.get("schema_version") != 1:
                reasons.append("legacy_or_unknown_metadata")
            if status_value == "unknown":
                reasons.append("unknown_task_status")
            if status_value in PROTECTED_STATUSES:
                reasons.append("task_requires_recovery_or_is_active")
            if row_category == "failed_recovery":
                reasons.append("recovery_material")
            if any(item["protects"] for item in linked):
                reasons.append("referenced_by_task_or_transaction")
            if broken:
                reasons.append("reference_state_unavailable")
            # Only named formats are eligible; arbitrary files under a cache
            # root may have been placed there by the user.
            known_format = path.suffix == ".json" if kind != "audio" else bool(re.fullmatch(r"(?:bilibili_(?:audio|subtitle|ai_subtitle|web_subtitle).*|local_audio.*|\.qwen_transcript)\.(?:mp3|wav|m4a|srt|txt|json)", path.name))
            if not known_format and kind not in {"recovery", "staging"}:
                reasons.append("unknown_cache_format")
            row_id = hashlib.sha256(str(path).encode()).hexdigest()
            row = {"id": row_id, "category": row_category, "path": str(path), "size_bytes": info.st_size,
                   "created_at": _timestamp(metadata.get("created_at")) or _utc(getattr(info, "st_birthtime", info.st_ctime)),
                   "modified_at": _utc(info.st_mtime), "status": status_value,
                   "run_id": _identifier(metadata.get("run_id")),
                   "task": _identifier(metadata.get("task") or payload.get("task")),
                   "source_ref": _source_ref(metadata.get("source_ref") or payload.get("source_ref") or payload.get("source")),
                   "references": linked, "protected": bool(reasons), "protection_reasons": sorted(set(reasons))}
            items.append(row)
            internal[row_id] = {"path": path, "root": root, "mtime": info.st_mtime, "payload": payload}
    items.sort(key=lambda row: (row["category"], row["path"]))
    summary = {"total_count": len(items), "total_bytes": sum(item["size_bytes"] for item in items),
               "protected_count": sum(item["protected"] for item in items),
               "protected_bytes": sum(item["size_bytes"] for item in items if item["protected"]), "by_category": {}}
    summary.update(_diagnostic_storage(state))
    for category in CATEGORIES:
        rows = [row for row in items if row["category"] == category]
        summary["by_category"][category] = {"count": len(rows), "size_bytes": sum(row["size_bytes"] for row in rows), "protected_count": sum(row["protected"] for row in rows)}
    return {"schema_version": 1, "items": items, "summary": summary, "policy": _policy(maintenance),
            "diagnostics": diagnostics, "references_available": not broken}, internal


def _fingerprint(path: pathlib.Path, parent: int | None = None) -> dict:
    if parent is None:
        with _parent_fd(path) as directory:
            return _fingerprint(path, directory)
    descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("unsafe cache file")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        end = os.fstat(descriptor)
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (end.st_size, end.st_mtime_ns, end.st_ctime_ns):
            raise OSError("cache changed while fingerprinting")
        return {"device": info.st_dev, "inode": info.st_ino, "size_bytes": info.st_size,
                "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns, "sha256": digest.hexdigest()}
    finally:
        os.close(descriptor)


def _preview(payload: dict, env: dict) -> dict:
    selection = _selection(payload)
    inventory, internal = _inventory(env)
    cutoff = time.time() - selection["older_than_days"] * 86400
    candidates, skipped, fingerprints = [], [], {}
    for row in inventory["items"]:
        if row["category"] not in selection["categories"]:
            continue
        if row["protected"]:
            skipped.append(row)
            continue
        if internal[row["id"]]["mtime"] > cutoff:
            continue
        try:
            fingerprint = _fingerprint(internal[row["id"]]["path"])
        except OSError:
            skipped.append({**row, "protection_reasons": ["changed_or_unreadable"]})
            continue
        fingerprints[row["id"]] = fingerprint
        candidates.append(row)
    preview_id = uuid.uuid4().hex
    _, state, maintenance = _roots(env)
    expires_at = time.time() + PREVIEW_LIFETIME
    snapshot = {"schema_version": 1, "preview_id": preview_id, "state_root": str(state),
                "created_at": _utc(), "expires_timestamp": expires_at, "selection": selection,
                "candidates": [{"id": row["id"], "category": row["category"], "fingerprint": fingerprints[row["id"]]} for row in candidates]}
    _atomic_json(maintenance / "previews" / (preview_id + ".json"), snapshot)
    return {"preview_id": preview_id, "expires_at": _utc(expires_at), "selection": selection,
            "candidates": candidates, "skipped": skipped, "candidate_count": len(candidates),
            "total_bytes": sum(row["size_bytes"] for row in candidates)}


def _clean(payload: dict, env: dict) -> dict:
    if set(payload) != {"preview_id"} or not isinstance(payload.get("preview_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", payload["preview_id"]):
        raise CacheMaintenanceError("clean accepts only the preview_id from a fixed selection")
    _, state, maintenance = _roots(env)
    snapshot_path = maintenance / "previews" / (payload["preview_id"] + ".json")
    snapshot = _read_json(snapshot_path)
    if not snapshot or snapshot.get("state_root") != str(state) or _number(snapshot.get("expires_timestamp")) is None or snapshot["expires_timestamp"] < time.time():
        raise CacheMaintenanceError("cleanup preview is missing or expired; preview again", "CACHE_PREVIEW_EXPIRED")
    selection = _selection(snapshot.get("selection") or {})
    deleted, skipped = [], []
    deleted_bytes = 0
    for candidate in snapshot.get("candidates", []):
        # Refresh before every unlink. The Worker lock serializes task startup,
        # queue updates and maintenance, while this guards external changes.
        inventory, internal = _inventory(env)
        current = next((row for row in inventory["items"] if row["id"] == candidate.get("id")), None)
        reason = None
        if not current:
            reason = "missing_or_unsafe"
        elif current["protected"]:
            reason = ",".join(current["protection_reasons"])
        elif current["category"] not in selection["categories"] or current["category"] != candidate.get("category"):
            reason = "category_changed"
        elif internal[current["id"]]["mtime"] > time.time() - selection["older_than_days"] * 86400:
            reason = "age_changed"
        else:
            path = internal[current["id"]]["path"]
            try:
                with _parent_fd(path) as directory:
                    fingerprint = _fingerprint(path, directory)
                    leaf = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                    if fingerprint != candidate.get("fingerprint") or (leaf.st_dev, leaf.st_ino) != (fingerprint["device"], fingerprint["inode"]):
                        reason = "fingerprint_changed"
                    else:
                        os.unlink(path.name, dir_fd=directory)
                        os.fsync(directory)
                        deleted.append({"id": current["id"], "category": current["category"], "path": str(path), "size_bytes": fingerprint["size_bytes"]})
                        deleted_bytes += fingerprint["size_bytes"]
            except OSError:
                reason = "changed_or_unreadable"
        if reason:
            skipped.append({"id": candidate.get("id"), "reason": reason})
    # Single-use previews avoid accidental repeated cleanup after a UI retry.
    with _parent_fd(snapshot_path) as directory:
        os.unlink(snapshot_path.name, dir_fd=directory)
    return {"deleted": deleted, "skipped": skipped, "deleted_bytes": deleted_bytes,
            "deleted_count": len(deleted), "preview_id": payload["preview_id"]}


def _original_text(value: str, include_paths: bool) -> str:
    value = redact_text(value)
    if not include_paths:
        value = re.sub(r"(?<![\w:/])(?:file://)?/(?!/)[^\s\"'<>，。！？；,;]+", "<redacted-path>", value)
        value = re.sub(r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>，。！？；,;]+", "<redacted-path>", value)
    return value


def _original(payload: dict, include_paths: bool = False) -> dict:
    """Explicit opt-in reveals only supported source text/timing, never secrets."""
    result = {}
    for key in ("transcript", "text"):
        if isinstance(payload.get(key), str):
            result[key] = _original_text(payload[key], include_paths)
    for key in ("initial", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            result[key] = _original(value, include_paths)
    if isinstance(payload.get("segments"), list):
        result["segments"] = []
        for segment in payload["segments"]:
            if not isinstance(segment, dict):
                continue
            row = {key: _number(segment.get(key)) for key in ("start", "end")}
            for key in ("text", "result"):
                if isinstance(segment.get(key), str):
                    row[key] = _original_text(segment[key], include_paths)
            result["segments"].append(row)
    return result


def _export(payload: dict, env: dict) -> dict:
    for option in ("include_original", "include_paths"):
        if option in payload and not isinstance(payload[option], bool):
            raise CacheMaintenanceError(option + " must be a boolean")
    include_original = payload.get("include_original", False)
    include_paths = payload.get("include_paths", False)
    inventory, internal = _inventory(env)
    output = {"schema_version": 1, "exported_at": _utc(), "privacy": {"include_original": include_original, "include_paths": include_paths},
              "summary": inventory["summary"], "policy": inventory["policy"], "diagnostics": inventory["diagnostics"], "items": []}
    for item in inventory["items"]:
        row = {key: item[key] for key in ("id", "category", "size_bytes", "created_at", "modified_at", "status", "run_id", "task", "source_ref", "references", "protected", "protection_reasons")}
        if include_paths:
            row["path"] = item["path"]
        if include_original:
            row["original"] = _original(internal[item["id"]]["payload"], include_paths)
        output["items"].append(row)
    # There are no raw errors, URLs, requests, cookies, arbitrary metadata or
    # unrecognized configuration strings in this export allowlist.
    return {"filename": "local-note-studio-diagnostics-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + ".json",
            "content": json.dumps(output, ensure_ascii=False, indent=2) + "\n"}


def handle_cache_request(action: str, payload: dict, env: dict) -> dict:
    if not isinstance(payload, dict):
        raise CacheMaintenanceError("cache options must be an object")
    _, _, maintenance = _roots(env)
    if action == "inventory":
        return _inventory(env)[0]
    if action == "preview":
        return _preview(payload, env)
    if action == "clean":
        return _clean(payload, env)
    if action == "references":
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) > 10000:
            raise CacheMaintenanceError("references require an entries list with at most 10000 items")
        cleaned = [_sanitize_reference(entry) for entry in entries]
        _atomic_json(maintenance / "references.json", {"schema_version": 1, "updated_at": _utc(), "entries": cleaned})
        return {"saved_count": len(cleaned), "references_available": True}
    if action == "policy":
        if not payload:
            return _policy(maintenance)
        current = _policy(maintenance)
        policy = _validate_policy({**current, **payload})
        _atomic_json(maintenance / "policy.json", policy)
        return policy
    if action == "auto":
        policy = _policy(maintenance)
        if not policy["auto_enabled"]:
            return {"auto_enabled": False, "deleted": [], "skipped": [], "deleted_bytes": 0, "deleted_count": 0}
        preview = _preview({"categories": policy["categories"], "older_than_days": policy["retention_days"]}, env)
        return {"auto_enabled": True, **_clean({"preview_id": preview["preview_id"]}, env)}
    if action == "export":
        return _export(payload, env)
    raise CacheMaintenanceError("unknown cache action")
