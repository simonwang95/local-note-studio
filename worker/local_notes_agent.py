#!/usr/bin/env python3
"""Restricted automation CLI for Local Note Studio."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import signal
import subprocess
import sys
from typing import Any

from automation_core import (
    AutomationError,
    HistoryStore,
    RESULT_SCHEMA_VERSION,
    classify_error,
    new_run_id,
    read_lock_status,
    redact_text,
    sanitize_mapping,
    stable_source_ref,
    app_data_root,
    state_dir,
    utc_now,
)
from automation_profiles import (
    AutomationProfile,
    load_profiles,
    require_profile,
    validate_allowed_path,
    validate_allowed_url,
)


WORKER = pathlib.Path(__file__).with_name("local_note_studio_worker.py")
MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".m4v",
    ".webm",
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
}
_ACTIVE_PROCESS: subprocess.Popen[str] | None = None


def base_request(profile: AutomationProfile, caller: str) -> dict[str, Any]:
    return {
        "caller": caller,
        "profile_id": profile.id,
        "run_id": new_run_id(),
        "output_dir": str(profile.output_dir),
        "runtime_backend": profile.runtime_backend,
        "python_bin": profile.python_bin or "python3",
        "conda_env": profile.conda_env,
        "conda_bin": profile.conda_bin,
        "model": profile.model,
        "asr_model": profile.asr_model,
        "subtitle_strategy": profile.subtitle_strategy,
        "favorite_limit": profile.limit,
        "content_types": list(profile.content_types),
        "stock_terms": profile.stock_terms,
        "overwrite_outputs": profile.overwrite_outputs,
        "cooldown_delay": profile.cooldown_delay,
        "extract_keyframes": profile.extract_keyframes,
        "dialogue_detection": profile.dialogue_detection,
        "keep_original_subtitles": profile.keep_original_subtitles,
        "timeout_seconds": profile.timeout_seconds,
        "retry_count": profile.retry_count,
        "chunk_chars": profile.chunk_chars,
        "opus_image_analysis": profile.opus_image_analysis,
        "lock_timeout_seconds": profile.lock_timeout_seconds,
        "execution_timeout_seconds": profile.execution_timeout_seconds,
    }


def build_request(
    action: str,
    profile: AutomationProfile,
    source: str = "",
    limit: int | None = None,
    dry_run: bool = False,
    caller: str = "agent",
) -> dict[str, Any]:
    request = base_request(profile, caller)
    if limit is not None:
        if limit < 0 or limit > profile.max_limit:
            raise AutomationError(f"limit must be between 0 and {profile.max_limit}", "INVALID_REQUEST")
        request["favorite_limit"] = limit
    if action == "sync-up":
        if not profile.up_mid:
            raise AutomationError("profile does not define up_mid", "PROFILE_INVALID")
        request.update(task="bilibili-up-sync", source=profile.up_mid)
    elif action == "retry-failed":
        if not profile.up_mid:
            raise AutomationError("profile does not define up_mid", "PROFILE_INVALID")
        request.update(task="bilibili-up-sync", source=profile.up_mid, retry_failed=True)
    elif action == "ingest-url":
        url = validate_allowed_url(source, profile.allowed_domains)
        path = pathlib.PurePosixPath(pathlib.PurePosixPath(url.split("?", 1)[0]).as_posix())
        if "bilibili.com" in url and "/video/" in path.as_posix():
            task = "bilibili-url"
        elif "bilibili.com" in url and "/opus/" in path.as_posix():
            task = "bilibili-opus"
        else:
            task = "web-url"
        request.update(task=task, source=url)
    elif action == "ingest-file":
        path = validate_allowed_path(source, profile.allowed_input_roots, "source", must_exist=True)
        task = "local-video" if path.is_dir() or path.suffix.lower() in MEDIA_EXTENSIONS else "source-file"
        request.update(task=task, source=str(path))
    elif action == "env-check":
        request.update(task="env-check", source="", output_dir="")
    else:
        raise AutomationError(f"unsupported agent action: {action}", "UNSUPPORTED_REQUEST")
    request["dry_run"] = bool(dry_run)
    return request


def _fallback_error(request: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    code, retryable = classify_error(exc)
    now = utc_now()
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": str(request.get("run_id") or new_run_id()),
        "caller": str(request.get("caller") or "agent"),
        "task": str(request.get("task") or "agent"),
        "status": "cancelled" if code == "TASK_CANCELLED" else "timeout" if code == "TASK_TIMEOUT" else "failed",
        "started_at": now,
        "finished_at": now,
        "source_ref": stable_source_ref(str(request.get("source") or "")),
        "output_dir": str(request.get("output_dir") or ""),
        "counts": {"discovered": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 1},
        "outputs": [],
        "manifest_path": "",
        "warnings": [],
        "details": {},
        "error": {"error_code": code, "message": redact_text(str(exc))},
        "retryable": retryable,
    }


def invoke_worker(request: dict[str, Any]) -> dict[str, Any]:
    global _ACTIVE_PROCESS
    command = [sys.executable, str(WORKER), "--request-stdin"]
    process = subprocess.Popen(
        command,
        cwd=str(WORKER.parent),
        env=worker_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _ACTIVE_PROCESS = process
    try:
        timeout = int(request.get("execution_timeout_seconds") or 0) or None
        try:
            stdout, stderr = process.communicate(json.dumps(request, ensure_ascii=False), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_active_worker()
            stdout, stderr = process.communicate()
            parsed = _last_result(stdout)
            if parsed:
                return parsed
            raise TimeoutError(f"worker exceeded execution timeout ({timeout}s)") from exc
        for line in (stdout + "\n" + stderr).splitlines():
            if line and not line.startswith("TASK_RESULT_JSON:"):
                print(redact_text(line), file=sys.stderr, flush=True)
        result = _last_result(stdout) or _last_result(stderr)
        if result is None:
            detail = redact_text(stderr.strip() or stdout.strip() or f"worker exited {process.returncode}")
            raise AutomationError(detail, "WORKER_CONTRACT_ERROR", True)
        return sanitize_mapping(result)
    finally:
        if _ACTIVE_PROCESS is process:
            _ACTIVE_PROCESS = None


def terminate_active_worker() -> None:
    global _ACTIVE_PROCESS
    process = _ACTIVE_PROCESS
    if process is None or process.poll() is not None:
        _ACTIVE_PROCESS = None
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
    _ACTIVE_PROCESS = None


def worker_environment() -> dict[str, str]:
    env = os.environ.copy()
    root = app_data_root()
    state = pathlib.Path(env.get("LOCAL_NOTE_STUDIO_STATE_DIR") or root / "state")
    index = pathlib.Path(env.get("INDEX_DIR") or state / "indexes")
    env.setdefault("LOCAL_NOTE_STUDIO_APP_DATA_DIR", str(root))
    env.setdefault("LOCAL_NOTE_STUDIO_STATE_DIR", str(state))
    env.setdefault("INDEX_DIR", str(index))
    env.setdefault("BILIBILI_STATE_DIR", str(index / "bilibili-state"))
    env.setdefault("OCR_CHECKPOINT_DIR", str(state / "ocr-checkpoints"))
    return env


def _last_result(text: str) -> dict[str, Any] | None:
    result = None
    for line in text.splitlines():
        if not line.startswith("TASK_RESULT_JSON:"):
            continue
        try:
            candidate = json.loads(line.split(":", 1)[1])
        except ValueError:
            continue
        if isinstance(candidate, dict):
            result = candidate
    return result


def status_payload(run_id: str = "", limit: int = 20, caller: str = "agent") -> dict[str, Any]:
    now = utc_now()
    return sanitize_mapping(
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": run_id,
            "caller": caller,
            "task": "status",
            "status": "completed",
            "started_at": now,
            "finished_at": now,
            "source_ref": "",
            "output_dir": "",
            "counts": {"discovered": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0},
            "outputs": [],
            "manifest_path": "",
            "warnings": [],
            "details": {},
            "error": None,
            "retryable": False,
            "lock": read_lock_status(),
            "runs": HistoryStore().list(limit=limit, run_id=run_id),
            "manifest_state": manifest_summary(),
        }
    )


def manifest_summary() -> dict[str, Any]:
    configured = os.environ.get("INDEX_DIR", "").strip()
    index_dir = pathlib.Path(configured).expanduser() if configured else state_dir() / "indexes"
    summaries: list[dict[str, Any]] = []
    if index_dir.exists():
        for path in sorted(index_dir.rglob("*manifest.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                summaries.append({"name": path.name, "status": "invalid", "counts": {"failed": 1}})
                continue
            counts = {"completed": 0, "skipped": 0, "failed": 0, "pending": 0, "missing_output": 0}
            for item in payload.get("items", []):
                if not isinstance(item, dict):
                    continue
                status = str(item.get("organized_status") or item.get("status") or "pending").lower()
                if status in {"failed", "error"}:
                    counts["failed"] += 1
                elif status in {"skipped", "skip"}:
                    counts["skipped"] += 1
                elif status in {"completed", "complete", "processed", "converted", "organized", "success"}:
                    counts["completed"] += 1
                else:
                    counts["pending"] += 1
                raw_output = str(
                    item.get("organized_output_path")
                    or item.get("organized_output")
                    or item.get("output_path")
                    or ""
                ).strip()
                if raw_output and not pathlib.Path(raw_output).expanduser().exists():
                    counts["missing_output"] += 1
            summaries.append({"name": path.name, "status": "ok", "counts": counts})
        processed_path = index_dir / "bilibili-state" / "processed_videos.txt"
        if processed_path.exists():
            count = sum(1 for line in processed_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
            summaries.append({"name": processed_path.name, "status": "ok", "counts": {"completed": count}})
    up_sync: list[dict[str, Any]] = []
    up_sync_dir = state_dir() / "up-sync"
    if up_sync_dir.exists():
        for path in sorted(up_sync_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                up_sync.append({"up_mid": path.stem, "status": "invalid", "counts": {"failed": 1}})
                continue
            counts = {"completed": 0, "failed": 0, "pending": 0}
            for item in payload.get("items", []):
                status = str(item.get("status") or "pending") if isinstance(item, dict) else "pending"
                bucket = status if status in {"completed", "failed"} else "pending"
                counts[bucket] += 1
            up_sync.append({"up_mid": str(payload.get("up_mid") or path.stem), "status": "ok", "counts": counts})
    return {"index_manifests": summaries, "up_sync": up_sync}


def profiles_payload(caller: str = "agent") -> dict[str, Any]:
    now = utc_now()
    profiles = [profile.public_dict() for profile in load_profiles(include_disabled=False).values()]
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": "",
        "caller": caller,
        "task": "profiles",
        "status": "completed",
        "started_at": now,
        "finished_at": now,
        "source_ref": "",
        "output_dir": "",
        "counts": {"discovered": len(profiles), "created": 0, "updated": 0, "skipped": 0, "failed": 0},
        "outputs": [],
        "manifest_path": "",
        "warnings": [],
        "details": {},
        "error": None,
        "retryable": False,
        "profiles": profiles,
    }


def run_agent_action(
    action: str,
    profile_id: str = "",
    source: str = "",
    limit: int | None = None,
    dry_run: bool = False,
    caller: str = "agent",
    run_id: str = "",
    status_limit: int = 20,
) -> dict[str, Any]:
    request: dict[str, Any] = {"caller": caller, "task": action, "run_id": new_run_id()}
    try:
        if action == "status":
            request["run_id"] = run_id or request["run_id"]
            return status_payload(run_id=run_id, limit=status_limit, caller=caller)
        if action == "profiles":
            return profiles_payload(caller=caller)
        profile = require_profile(profile_id)
        request = build_request(action, profile, source=source, limit=limit, dry_run=dry_run, caller=caller)
        if action == "retry-failed" and not dry_run:
            previous = next(
                (
                    item
                    for item in HistoryStore().list(limit=100)
                    if item.get("profile_id") == profile.id
                    and item.get("task") == "bilibili-up-sync"
                    and item.get("status") in {"failed", "partial_failed", "interrupted", "timeout", "cancelled"}
                ),
                None,
            )
            if previous:
                request["retry_of"] = previous["run_id"]
        return invoke_worker(request)
    except BaseException as exc:
        return _fallback_error(request, exc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("sync-up", "retry-failed", "env-check"):
        item = subparsers.add_parser(command)
        item.add_argument("--profile", required=True)
        if command != "env-check":
            item.add_argument("--limit", type=int)
        item.add_argument("--dry-run", action="store_true")
    for command, source_flag in (("ingest-url", "--url"), ("ingest-file", "--file")):
        item = subparsers.add_parser(command)
        item.add_argument("--profile", required=True)
        item.add_argument(source_flag, required=True, dest="source")
        item.add_argument("--dry-run", action="store_true")
    status = subparsers.add_parser("status")
    status.add_argument("--run-id", default="")
    status.add_argument("--limit", type=int, default=20)
    subparsers.add_parser("profiles")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_agent_action(
        args.command,
        profile_id=getattr(args, "profile", ""),
        source=getattr(args, "source", ""),
        limit=getattr(args, "limit", None),
        dry_run=getattr(args, "dry_run", False),
        caller="agent",
        run_id=getattr(args, "run_id", ""),
        status_limit=getattr(args, "limit", 20) or 20,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"completed", "no_changes", "partial_failed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
