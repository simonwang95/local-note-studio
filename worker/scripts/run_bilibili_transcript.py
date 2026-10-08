#!/usr/bin/env python3
"""Run migrated Bilibili transcription scripts with local defaults."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import tempfile
import contextlib
import shutil
import uuid
import urllib.parse
from collections.abc import Callable

from video_keyframes import add_keyframes_to_note
from transcript_quality import load_transcript_diagnostic, save_transcript_diagnostic
from video_contract import validate_video_note
from transcript_timing import load_note_timing


ROOT = pathlib.Path(__file__).resolve().parents[1]

DEFAULTS = {
    "NOTES_DIR": "notes",
    "INDEX_DIR": "indexes",
    "BILIBILI_OUTPUT_DIR": "notes/Net/BiliBili",
    "BILIBILI_DEDUPE_DIRS": "notes",
    "VIDEO_MANIFEST_ENABLED": "true",
    "BILIBILI_STATE_DIR": "indexes/bilibili-state",
    "BILIBILI_FAV_MEDIA_ID": "",
    "BILIBILI_COOKIES_FILE": "",
    "BILIBILI_PREFER_WEB_SUBTITLE": "false",
    "DEFAULT_LLM_API_BASE": "http://127.0.0.1:8000/v1",
    "DEFAULT_LLM_API_KEY": "mtplx-local",
    "DEFAULT_LLM_MODEL": "mtplx-qwen38-27b-optimized-speed",
    "CONDA_ENV": "course-whisper",
    "ASR_ENGINE": "whisper",
    "ASR_LOCAL_MODEL": "",
    "ASR_PROMPT": "以下是中文课程、AI、投资、摄影等学习材料音频，请尽量保留术语。",
    "FORCE_ASR": "false",
    "EXTRACT_KEYFRAMES": "false",
    "KEYFRAME_MAX_COUNT": "4",
    "ENABLE_DIALOGUE_DETECTION": "false",
    "KEEP_ORIGINAL_SUBTITLES": "false",
    "OVERWRITE_OUTPUT": "false",
    "BILIBILI_INCREMENTAL_STATE_ENABLED": "true",
    "COOLDOWN_DELAY": "30",
    "VIDEO_OUTPUT_MODE": "full",
    "SUMMARY_PROOFREAD_CHUNK_CHARS": "8000",
    "SUMMARY_PROOFREAD_TIMEOUT": "600",
    "SUMMARY_PROOFREAD_MAX_RETRIES": "1",
    "SUMMARY_PROOFREAD_COOLDOWN_DELAY": "0",
    "SUMMARY_PROOFREAD_ENABLE_THINKING": "false",
    "SUMMARY_CHUNK_CHARS": "60000",
    "SUMMARY_ENABLE_THINKING": "false",
    "LLM_TIMEOUT": "1800",
    "LLM_MAX_RETRIES": "2",
    "SUMMARY_CHUNK_COOLDOWN_DELAY": "0",
}


FIELD_PATTERNS = {
    "source_url": r"^>\s*\*\*链接\*\*：(.+)$",
    "author": r"^>\s*\*\*作者\*\*：(.+)$",
    "published": r"^>\s*\*\*发布时间\*\*：(.+)$",
    "duration": r"^>\s*\*\*视频时长\*\*：(.+)$",
    "transcript_source": r"^>\s*\*\*转录来源\*\*：(.+)$",
    "transcribed_at": r"^>\s*\*\*转录时间\*\*：(.+)$",
}


def load_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today() -> str:
    return dt.date.today().isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_duration_seconds(value: str | float | int) -> str:
    try:
        total = max(0, int(float(value) + 0.5))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{seconds}秒"
    return f"{minutes}分{seconds}秒"


def probe_media_duration(path: pathlib.Path) -> str:
    commands = [
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        ["ffprobe", "-v", "error", "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        values: list[float] = []
        for line in result.stdout.splitlines():
            try:
                values.append(float(line.strip()))
            except ValueError:
                continue
        if values:
            return format_duration_seconds(max(values))
    return ""


def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    if text == "":
        return ""
    if re.search(r"[:#\[\]\{\},&*!\|>'\"%@`]|^\s|\s$", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def frontmatter(data: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def parse_frontmatter(markdown: str) -> tuple[dict[str, object], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---", 4)
    if end == -1:
        return {}, markdown
    raw = markdown[4:end].strip().splitlines()
    body = markdown[end + len("\n---") :].lstrip("\n")
    data: dict[str, object] = {}
    current_key = ""
    for line in raw:
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, [])
            if isinstance(data[current_key], list):
                data[current_key].append(line[4:].strip().strip('"').strip("'"))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        data[current_key] = value.strip('"').strip("'") if value else []
    return data, body


def config() -> dict[str, str]:
    values = dict(DEFAULTS)
    values.update(load_env_file(ROOT / "env.local"))
    for key in DEFAULTS:
        if key in os.environ:
            values[key] = os.environ[key]
    values["BILIBILI_FAV_MEDIA_ID"] = values.get("BILIBILI_FAV_MEDIA_ID") or values.get("FAV_MEDIA_ID", "")
    values["BILIBILI_COOKIES_FILE"] = values.get("BILIBILI_COOKIES_FILE") or values.get("BILI_COOKIE_FILE", "")
    values["BILIBILI_OUTPUT_DIR"] = values.get("BILIBILI_OUTPUT_DIR") or values.get("OUTPUT_DIR", DEFAULTS["BILIBILI_OUTPUT_DIR"])
    values["BILIBILI_STATE_DIR"] = values.get("BILIBILI_STATE_DIR") or values.get("STATE_DIR", DEFAULTS["BILIBILI_STATE_DIR"])
    return values


def project_env(cfg: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    output_dir = pathlib.Path(cfg["BILIBILI_OUTPUT_DIR"])
    state_dir = pathlib.Path(cfg["BILIBILI_STATE_DIR"])
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    if not state_dir.is_absolute():
        state_dir = ROOT / state_dir
    mappings = {
        "LOCAL_NOTE_STUDIO_ENV_LOADED": "1",
        "OUTPUT_DIR": str(output_dir),
        "INDEX_DIR": cfg.get("INDEX_DIR", "indexes"),
        "VIDEO_TRANSACTION_STAGE_DIR": cfg.get("VIDEO_TRANSACTION_STAGE_DIR", ""),
        "VIDEO_TRANSACTION_FINAL_DIR": cfg.get("VIDEO_TRANSACTION_FINAL_DIR", ""),
        "NOTES_DIR": cfg["NOTES_DIR"],
        "BILIBILI_DEDUPE_DIRS": cfg["BILIBILI_DEDUPE_DIRS"],
        "STATE_DIR": str(state_dir),
        "FAV_MEDIA_ID": cfg["BILIBILI_FAV_MEDIA_ID"],
        "BILIBILI_FAV_MEDIA_ID": cfg["BILIBILI_FAV_MEDIA_ID"],
        "BILI_COOKIE_FILE": cfg["BILIBILI_COOKIES_FILE"],
        "BILIBILI_COOKIES_FILE": cfg["BILIBILI_COOKIES_FILE"],
        "BILIBILI_PREFER_WEB_SUBTITLE": cfg["BILIBILI_PREFER_WEB_SUBTITLE"],
        "SUMMARY_API_URL": cfg["DEFAULT_LLM_API_BASE"],
        "SUMMARY_API_KEY": cfg["DEFAULT_LLM_API_KEY"],
        "SUMMARY_MODEL": cfg["DEFAULT_LLM_MODEL"],
        "ASR_ENGINE": cfg["ASR_ENGINE"],
        "ASR_LOCAL_MODEL": cfg["ASR_LOCAL_MODEL"],
        "ASR_PROMPT": cfg["ASR_PROMPT"],
        "FORCE_ASR": cfg["FORCE_ASR"],
        "EXTRACT_KEYFRAMES": cfg["EXTRACT_KEYFRAMES"],
        "KEYFRAME_MAX_COUNT": cfg["KEYFRAME_MAX_COUNT"],
        "ENABLE_DIALOGUE_DETECTION": cfg["ENABLE_DIALOGUE_DETECTION"],
        "KEEP_ORIGINAL_SUBTITLES": cfg["KEEP_ORIGINAL_SUBTITLES"],
        "VIDEO_OUTPUT_MODE": cfg.get("VIDEO_OUTPUT_MODE", "full"),
        "SUMMARY_PROOFREAD_CHUNK_CHARS": cfg.get("SUMMARY_PROOFREAD_CHUNK_CHARS", "8000"),
        "SUMMARY_PROOFREAD_TIMEOUT": cfg.get("SUMMARY_PROOFREAD_TIMEOUT", "600"),
        "SUMMARY_PROOFREAD_MAX_RETRIES": cfg.get("SUMMARY_PROOFREAD_MAX_RETRIES", "1"),
        "SUMMARY_PROOFREAD_COOLDOWN_DELAY": cfg.get("SUMMARY_PROOFREAD_COOLDOWN_DELAY", "0"),
        "SUMMARY_PROOFREAD_ENABLE_THINKING": cfg.get("SUMMARY_PROOFREAD_ENABLE_THINKING", "false"),
        "SUMMARY_CHUNK_CHARS": cfg.get("SUMMARY_CHUNK_CHARS", "60000"),
        "SUMMARY_ENABLE_THINKING": cfg.get("SUMMARY_ENABLE_THINKING", "false"),
        "LLM_TIMEOUT": cfg.get("LLM_TIMEOUT", "1800"),
        "LLM_MAX_RETRIES": cfg.get("LLM_MAX_RETRIES", "2"),
        "SUMMARY_CHUNK_COOLDOWN_DELAY": cfg.get("SUMMARY_CHUNK_COOLDOWN_DELAY", "0"),
        "OVERWRITE_OUTPUT": cfg["OVERWRITE_OUTPUT"],
        "CONDA_ENV": cfg["CONDA_ENV"],
    }
    for key, value in mappings.items():
        if value or key == "CONDA_ENV":
            env[key] = value
    if not cfg.get("CONDA_ENV"):
        env["LOCAL_NOTE_STUDIO_PYTHON_BIN"] = sys.executable
    return env


def python_command(cfg: dict[str, str], script: pathlib.Path) -> list[str]:
    if cfg.get("CONDA_ENV"):
        return ["conda", "run", "--no-capture-output", "-n", cfg["CONDA_ENV"], "python3", "-u", str(script)]
    return [sys.executable, "-u", str(script)]


def bash_command(cfg: dict[str, str], script: pathlib.Path, *args: str) -> list[str]:
    if cfg.get("CONDA_ENV"):
        return ["conda", "run", "--no-capture-output", "-n", cfg["CONDA_ENV"], "bash", str(script), *args]
    return ["bash", str(script), *args]


def stream_command(command: list[str], cwd: pathlib.Path, env: dict[str, str], timeout: int | None = None, *, allow_failure: bool = False) -> tuple[int, str]:
    start = time.time()
    output: list[str] = []
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    try:
        for raw_line in iter(process.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="replace")
            output.append(line)
            print(line, end="", flush=True)
            if timeout and time.time() - start > timeout:
                process.kill()
                raise TimeoutError(f"command timed out after {timeout}s: {' '.join(command)}")
    finally:
        if process.stdout:
            process.stdout.close()
    returncode = process.wait()
    collected = "".join(output)
    if returncode != 0 and not allow_failure:
        raise RuntimeError(f"command failed ({returncode}): {' '.join(command)}\n\n{collected}")
    return returncode, collected


def mark_video_output_failed(path: str, message: str) -> None:
    output = pathlib.Path(path)
    marker = output.with_name(f".{output.name}.lns-failed.json")
    atomic_write_text(marker, json.dumps({"staged_path": str(output), "error": message}, ensure_ascii=False, indent=2) + "\n")


def parse_scanner_output(stdout: str) -> list[dict[str, str]]:
    videos: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in stdout.splitlines():
        if line.startswith("  - AVID:"):
            if current:
                videos.append(current)
            current = {"avid": line.split("AVID:", 1)[1].strip()}
        elif line.startswith("    BVID:") and current is not None:
            current["bvid"] = line.split("BVID:", 1)[1].strip()
        elif line.startswith("    TITLE:") and current is not None:
            current["title"] = line.split("TITLE:", 1)[1].strip()
        elif line.startswith("    DURATION:") and current is not None:
            current["duration"] = line.split("DURATION:", 1)[1].strip()
        elif line.startswith("    UPPER:") and current is not None:
            current["upper"] = line.split("UPPER:", 1)[1].strip()
        elif line.startswith("    PUBTIME:") and current is not None:
            current["pubtime"] = line.split("PUBTIME:", 1)[1].strip()
    if current:
        videos.append(current)
    return videos


def extract_markdown_paths(stdout: str) -> list[str]:
    """Return only Markdown files generated by the current transcription run."""
    paths: list[str] = []
    seen = set()
    for line in stdout.splitlines():
        marker = "GENERATED_MARKDOWN_PATH:"
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        path = stripped[len(marker):].strip()
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        paths.append(path)
    return paths


def extract_retryable_existing_markdown_paths(stdout: str, cfg: dict[str, str] | None = None) -> list[str]:
    """Return skipped notes that still contain placeholders and a retry transcript."""
    paths: list[str] = []
    seen: set[str] = set()
    marker = "SKIPPED_EXISTING_MARKDOWN_PATH:"
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        path = stripped[len(marker):].strip()
        if path in seen or not os.path.isfile(path):
            continue
        try:
            content = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        has_pending = "【AI待处理" in content
        has_transcript = (
            "<summary>📄 原始字幕</summary>" in content
            or "<summary>📄 完整原文</summary>" in content
            or re.search(r"(?m)^##\s+原始字幕\s*$", content) is not None
            or re.search(r"(?m)^##\s+完整原文\s*$", content) is not None
        )
        is_incomplete = has_pending or (cfg is not None and not video_validation(pathlib.Path(path), cfg).complete)
        cached = load_transcript_diagnostic("source-by-note", str(pathlib.Path(path).resolve())) or {}
        if not is_incomplete or not (has_transcript or cached.get("transcript")):
            continue
        seen.add(path)
        paths.append(path)
    return paths


def extract_skipped_existing_markdown_paths(stdout: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    marker = "SKIPPED_EXISTING_MARKDOWN_PATH:"
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        path = stripped[len(marker):].strip()
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def emit_local_batch_result(total: int, changed: int, skipped: int, failed: int) -> None:
    print(
        "LOCAL_BATCH_RESULT_JSON:"
        + json.dumps(
            {"total": total, "changed": changed, "skipped": skipped, "failed": failed},
            ensure_ascii=False,
        )
    )


def load_manifest(path: pathlib.Path) -> dict[str, object]:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: pathlib.Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".lns-write-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_directory(path: pathlib.Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _merge_processed_ids(path: pathlib.Path, ids: list[str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    merged = list(dict.fromkeys([*existing, *ids]))
    atomic_write_text(path, "\n".join(merged) + "\n")


def _commit_transaction_indexes(journal: dict[str, object]) -> None:
    # Cache promotion belongs to the roll-forward phase, never the rollback phase.
    for item in journal.get("diagnostic_copies", []):
        staged, target = pathlib.Path(item["staged"]), pathlib.Path(item["target"])
        cached = load_transcript_diagnostic("source-by-note", str(staged.resolve())) or {}
        save_transcript_diagnostic("source-by-note", str(target.resolve()), {**cached, "note_path": str(target.resolve())})
        save_transcript_diagnostic("timing-by-note", str(target.resolve()), {
            "schema_version": 1, "note_path": str(target.resolve()), "segments": load_note_timing(staged),
        })
    # Both index writes are idempotent, so a crash between them can roll forward.
    if journal.get("manifest_items"):
        _merge_manifest_items(pathlib.Path(str(journal["manifest_path"])), journal["manifest_items"])
    if journal.get("processed_ids"):
        _merge_processed_ids(pathlib.Path(str(journal["processed_path"])), journal["processed_ids"])


def video_validation(path: pathlib.Path, cfg: dict[str, str]):
    markdown = path.read_text(encoding="utf-8", errors="replace")
    cached = load_transcript_diagnostic("source-by-note", str(path.resolve())) or {}
    return validate_video_note(
        markdown,
        transcription_only=cfg.get("VIDEO_OUTPUT_MODE", "full") == "transcription-only",
        raw_transcript_override=str(cached.get("transcript") or ""),
    )


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _transaction_journal_path(output_root: pathlib.Path, run_id: str) -> pathlib.Path:
    return output_root / ".local-note-studio-transactions" / f"{run_id}.json"


def _save_transaction_journal(path: pathlib.Path, payload: dict[str, object]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _atomic_copy(source: pathlib.Path, destination: pathlib.Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".lns-copy-", suffix=destination.suffix, dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        _sync_directory(destination.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _merge_manifest_items(path: pathlib.Path, items: list[dict[str, object]]) -> None:
    if not items:
        return
    manifest = load_manifest(path)
    for item in items:
        upsert_manifest_item(manifest, item)
    save_manifest(path, manifest)


def recover_video_transactions(output_root: pathlib.Path) -> None:
    journal_dir = output_root / ".local-note-studio-transactions"
    if not journal_dir.exists():
        return
    for journal_path in sorted(journal_dir.glob("*.json")):
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        state = journal.get("state")
        if state in {"complete", "rolled_back"}:
            continue
        entries = [entry for entry in journal.get("entries", []) if isinstance(entry, dict)]
        conflicts = []
        if state == "notes_committed":
            for entry in entries:
                if not entry.get("committed"):
                    continue
                target = pathlib.Path(entry["target"])
                if not target.is_file() or file_sha256(target) != entry["new_sha256"]:
                    conflicts.append(str(target))
            if conflicts:
                journal["recovery_conflicts"] = conflicts
                _save_transaction_journal(journal_path, journal)
                raise RuntimeError("提交恢复发现正式笔记已变更，保留备份和暂存材料：" + "；".join(conflicts))
            _commit_transaction_indexes(journal)
            journal["state"] = "complete"
            journal["recovered_at"] = now_iso()
            _save_transaction_journal(journal_path, journal)
            print(f"[视频提交恢复] 已补交任务 {journal.get('run_id')} 的索引记录。", flush=True)
            continue
        for entry in reversed(entries):
            if not (entry.get("prepared") or entry.get("committed")):
                continue
            target = pathlib.Path(str(entry["target"]))
            backup = pathlib.Path(str(entry["backup"])) if entry.get("backup") else None
            current_hash = file_sha256(target) if target.is_file() else ""
            if current_hash == entry.get("old_sha256", ""):
                continue  # The replacement never happened, or was already rolled back.
            if current_hash != entry.get("new_sha256"):
                conflicts.append(str(target))
                continue
            if backup and backup.is_file():
                _atomic_copy(backup, target)
            elif entry.get("old_sha256"):
                conflicts.append(str(target))
            else:
                target.unlink(missing_ok=True)
                _sync_directory(target.parent)
        journal["state"] = "recovery_conflict" if conflicts else "rolled_back"
        journal["recovery_conflicts"] = conflicts
        journal["recovered_at"] = now_iso()
        _save_transaction_journal(journal_path, journal)
        if conflicts:
            raise RuntimeError("回滚发现用户修改或备份缺失，保留当前文件：" + "；".join(conflicts))
        print(f"[视频提交恢复] 已回滚未完成的 Markdown 提交 {journal.get('run_id')}；暂存材料仍保留。", flush=True)


def _staged_manifest_items(stage_index: pathlib.Path, stage_output: pathlib.Path, output_root: pathlib.Path) -> list[dict[str, object]]:
    manifest_path = stage_index / "video-manifest.json"
    if not manifest_path.is_file():
        return []
    result = []
    for raw in load_manifest(manifest_path).get("items", []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        output_value = pathlib.Path(str(item.get("output_path") or ""))
        if not output_value.is_absolute():
            output_value = ROOT / output_value
        try:
            relative = output_value.resolve().relative_to(stage_output.resolve())
        except ValueError:
            continue
        item["output_path"] = str((output_root / relative).resolve())
        result.append(item)
    return result


def _rewrite_and_commit_note(
    staged: pathlib.Path,
    stage_output: pathlib.Path,
    output_root: pathlib.Path,
    baseline: dict[str, str],
    overwrite: bool,
    backup_root: pathlib.Path,
    cfg: dict[str, str],
    prepare_commit: Callable[[dict[str, object]], None],
) -> tuple[pathlib.Path, bool, pathlib.Path | None]:
    relative = staged.resolve().relative_to(stage_output.resolve())
    target = output_root / relative
    current_hash = file_sha256(target) if target.is_file() else ""
    if target.exists() and not overwrite:
        if video_validation(target, cfg).complete:
            return target, False, None
        origin_path = staged.with_name(f".{staged.name}.lns-origin.json")
        origin = json.loads(origin_path.read_text(encoding="utf-8")) if origin_path.is_file() else {}
        if origin.get("target") != str(target) or origin.get("sha256") != current_hash:
            raise RuntimeError(f"同名目标已有未完成笔记且未授权覆盖：{target}")
    if current_hash != baseline.get(str(target.resolve()), ""):
        raise RuntimeError(f"目标笔记在任务运行期间被修改；保留原文件和暂存结果：{target}")

    markdown = staged.read_text(encoding="utf-8", errors="replace")
    image_pattern = re.compile(r"(!\[[^\]]*\]\()([^)]*)(\))")
    asset_manifests = {}
    for match in list(image_pattern.finditer(markdown)):
        raw_target = match.group(2).strip().strip("<>")
        if re.match(r"^(?:https?:|data:|//)", raw_target, re.I):
            continue
        decoded = pathlib.Path(urllib.parse.unquote(raw_target))
        if decoded.is_absolute():
            try:
                asset_relative = decoded.resolve().relative_to(stage_output.resolve())
            except ValueError:
                continue
        else:
            try:
                asset_relative = (staged.parent / decoded).resolve().relative_to(stage_output.resolve())
            except ValueError:
                continue
        source_asset = stage_output / asset_relative
        if not source_asset.is_file():
            continue
        destination = output_root / asset_relative
        if destination.exists() and file_sha256(destination) != file_sha256(source_asset):
            suffix = file_sha256(source_asset)[:10]
            destination = destination.with_name(f"{destination.stem}-{suffix}{destination.suffix}")
        if not destination.exists():
            _atomic_copy(source_asset, destination)
        provenance = source_asset.parent / "keyframes-manifest.json"
        if provenance.is_file():
            asset_manifests[provenance] = destination.parent / provenance.name
        new_relative = pathlib.Path(os.path.relpath(destination, target.parent)).as_posix()
        markdown = markdown.replace(match.group(0), f"{match.group(1)}{new_relative}{match.group(3)}", 1)

    for source_manifest, destination_manifest in asset_manifests.items():
        if not destination_manifest.exists():
            _atomic_copy(source_manifest, destination_manifest)

    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        backup = backup_root / relative
        _atomic_copy(target, backup)
    prepare_commit({
        "prepared": True, "target": str(target), "backup": str(backup) if backup else "",
        "old_sha256": current_hash,
        "new_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    })
    # Asset copying and backup preparation can take time; detect intervening edits.
    if (file_sha256(target) if target.is_file() else "") != current_hash:
        raise RuntimeError(f"目标笔记在提交前被修改，已保留两份结果：{target}")
    atomic_write_text(target, markdown)
    return target, True, backup


def record_transaction_batch(cfg: dict[str, str], result: dict[str, object]) -> None:
    stage = cfg.get("VIDEO_TRANSACTION_STAGE_DIR")
    if stage:
        atomic_write_text(pathlib.Path(stage).parent / "batch-result.json", json.dumps(result, ensure_ascii=False))


def stage_existing_note(path: pathlib.Path, cfg: dict[str, str]) -> pathlib.Path:
    """Copy a recoverable note into this transaction before any repair writes."""
    stage = cfg.get("VIDEO_TRANSACTION_STAGE_DIR")
    if not stage:
        return path
    stage_root = pathlib.Path(stage).resolve()
    path = path.resolve()
    if path.is_relative_to(stage_root):
        return path
    final_root = pathlib.Path(cfg["VIDEO_TRANSACTION_FINAL_DIR"]).resolve()
    try:
        relative = path.relative_to(final_root)
        # A previous recovery point may itself be under the final output directory.
        if relative.parts[0] == ".local-note-studio-staging":
            relative = pathlib.Path(*relative.parts[3:])
    except ValueError:
        raise RuntimeError(f"恢复笔记不属于当前输出目录：{path}")
    staged = stage_root / relative
    _atomic_copy(path, staged)
    final = final_root / relative
    if final.is_file():
        atomic_write_text(staged.with_name(f".{staged.name}.lns-origin.json"), json.dumps({
            "target": str(final), "sha256": file_sha256(final),
        }))
    # Preserve image references used by the recoverable note.
    for raw in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        asset = (path.parent / urllib.parse.unquote(raw.strip("<>"))).resolve()
        if asset.is_file() and asset.is_relative_to(path.parent):
            _atomic_copy(asset, staged.parent / asset.relative_to(path.parent))
    for kind in ("source-by-note", "timing-by-note"):
        cached = load_transcript_diagnostic(kind, str(path))
        if cached:
            save_transcript_diagnostic(kind, str(staged), {**cached, "note_path": str(staged)})
    timing = path.with_name(f".{path.name}.lns-timing.json")
    if timing.is_file():
        _atomic_copy(timing, staged.with_name(f".{staged.name}.lns-timing.json"))
    print(f"GENERATED_MARKDOWN_PATH:{staged}", flush=True)
    return staged


def run_video_transaction(output_dir: str, cfg: dict[str, str], runner: Callable[[dict[str, str]], int]) -> int:
    output_root = pathlib.Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    recover_video_transactions(output_root)
    run_id = uuid.uuid4().hex
    transaction_root = output_root / ".local-note-studio-staging" / run_id
    stage_output = transaction_root / "output"
    stage_index = transaction_root / "indexes"
    stage_output.mkdir(parents=True, exist_ok=True)

    baseline: dict[str, str] = {}
    for path in output_root.rglob("*.md"):
        if ".local-note-studio-" in path.as_posix():
            continue
        with contextlib.suppress(OSError):
            baseline[str(path.resolve())] = file_sha256(path)

    staged_cfg = dict(cfg)
    staged_cfg.update({
        "BILIBILI_OUTPUT_DIR": str(stage_output),
        "OUTPUT_DIR": str(stage_output),
        "INDEX_DIR": str(stage_index),
        "VIDEO_TRANSACTION_STAGE_DIR": str(stage_output),
        "VIDEO_TRANSACTION_FINAL_DIR": str(output_root),
    })
    journal_path = _transaction_journal_path(output_root, run_id)
    journal: dict[str, object] = {
        "schema_version": 1, "run_id": run_id, "state": "staging", "started_at": now_iso(),
        "stage_dir": str(transaction_root), "output_root": str(output_root), "entries": [],
    }
    _save_transaction_journal(journal_path, journal)
    env_keys = ("VIDEO_TRANSACTION_STAGE_DIR", "VIDEO_TRANSACTION_FINAL_DIR")
    previous_env = {key: os.environ.get(key) for key in env_keys}
    command_code = 1
    runner_error = ""
    for key in env_keys:
        os.environ[key] = staged_cfg[key]
    try:
        command_code = runner(staged_cfg)
    except Exception as exc:
        runner_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
        print(f"[视频事务] 处理未完成（{type(exc).__name__}）；已保留暂存结果供检查或重试。", file=sys.stderr, flush=True)
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    staged_notes = sorted(path for path in stage_output.rglob("*.md") if path.is_file())
    failed_staged_paths: set[pathlib.Path] = set()
    for marker in stage_output.rglob(".*.lns-failed.json"):
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            failed_staged_paths.add(pathlib.Path(str(payload.get("staged_path") or "")).resolve())
        except (OSError, ValueError):
            continue
    valid_notes = [path for path in staged_notes if path.resolve() not in failed_staged_paths and video_validation(path, staged_cfg).complete]
    if runner_error:
        # A task-level exception invalidates the transaction as a whole; keep every staged note recoverable.
        valid_notes = []
    invalid_notes = [path for path in staged_notes if path not in valid_notes]
    journal["state"] = "committing"
    if runner_error:
        journal["runner_error"] = runner_error
    _save_transaction_journal(journal_path, journal)
    backup_root = output_root / ".local-note-studio-backups" / run_id
    committed: list[pathlib.Path] = []
    try:
        for staged in valid_notes:
            relative = staged.resolve().relative_to(stage_output.resolve())
            target = output_root / relative
            entry = {"staged": str(staged), "target": str(target), "backup": "",
                     "new_sha256": file_sha256(staged), "prepared": False, "committed": False, "skipped": False}
            entries = journal["entries"]
            assert isinstance(entries, list)
            entries.append(entry)
            _save_transaction_journal(journal_path, journal)
            def prepare_commit(metadata):
                entry.update(metadata)
                _save_transaction_journal(journal_path, journal)

            actual_target, did_commit, backup = _rewrite_and_commit_note(
                staged, stage_output, output_root, baseline,
                parse_bool(cfg.get("OVERWRITE_OUTPUT", "false")), backup_root, staged_cfg, prepare_commit,
            )
            if did_commit:
                entry["target"] = str(actual_target)
                entry["new_sha256"] = file_sha256(actual_target)
                entry["committed"] = True
                entry["backup"] = str(backup) if backup else ""
                committed.append(actual_target)
                if (parse_bool(cfg.get("LOCAL_NOTE_STUDIO_INCOGNITO", "false"))
                        and os.environ.get("LOCAL_NOTE_STUDIO_EPHEMERAL_SOURCE_DIR")):
                    # Share source only until the outer Worker's final contract
                    # check. Its TemporaryDirectory owns cleanup on every exit.
                    cached = load_transcript_diagnostic("source-by-note", str(staged.resolve()))
                    if cached:
                        save_transcript_diagnostic("source-by-note", str(actual_target.resolve()),
                                                   {**cached, "note_path": str(actual_target.resolve())})
            else:
                entry["skipped"] = True
            _save_transaction_journal(journal_path, journal)

        manifest_enabled = parse_bool(cfg.get("VIDEO_MANIFEST_ENABLED", "true"))
        committed_paths = {str(path.resolve()) for path in committed}
        manifest_items = [
            item for item in (_staged_manifest_items(stage_index, stage_output, output_root) if manifest_enabled else [])
            if str(pathlib.Path(str(item.get("output_path") or "")).resolve()) in committed_paths
        ]
        manifest_root = pathlib.Path(cfg.get("INDEX_DIR", "indexes"))
        if not manifest_root.is_absolute():
            manifest_root = ROOT / manifest_root
        manifest_path = manifest_root / "video-manifest.json"
        journal["manifest_path"] = str(manifest_path)
        journal["manifest_items"] = manifest_items
        pending_path = stage_index / "pending-processed.json"
        pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.is_file() else []
        published_paths = committed_paths | {
            str(pathlib.Path(entry["target"]).resolve()) for entry in journal["entries"] if entry.get("skipped")
        }
        journal["processed_ids"] = [row["avid"] for row in pending if row["target"] in published_paths or (
            row.get("existing") and pathlib.Path(row["target"]).is_file()
            and video_validation(pathlib.Path(row["target"]), cfg).complete
        )]
        state_root = pathlib.Path(cfg.get("BILIBILI_STATE_DIR", str(ROOT / "indexes/bilibili-state"))).expanduser()
        if not state_root.is_absolute():
            state_root = ROOT / state_root
        journal["processed_path"] = str(state_root / "processed_videos.txt")
        journal["diagnostic_copies"] = [
            {"staged": entry["staged"], "target": entry["target"]}
            for entry in journal["entries"] if entry.get("committed")
        ] if not parse_bool(cfg.get("LOCAL_NOTE_STUDIO_INCOGNITO", os.environ.get("LOCAL_NOTE_STUDIO_INCOGNITO", "false"))) else []
        journal["state"] = "notes_committed"
        journal["finished_at"] = now_iso()
        _save_transaction_journal(journal_path, journal)
        _commit_transaction_indexes(journal)
        journal["state"] = "complete"
        _save_transaction_journal(journal_path, journal)

        batch_path = transaction_root / "batch-result.json"
        batch = json.loads(batch_path.read_text(encoding="utf-8")) if batch_path.is_file() else {}
        entries = journal.get("entries") or []
        skipped_count = int(batch.get("skipped", 0)) + sum(1 for entry in entries if isinstance(entry, dict) and entry.get("skipped"))
        failed_count = max(int(batch.get("failed", 0)), len(invalid_notes), len(failed_staged_paths), 1 if command_code != 0 else 0)
        result = {
            "total": max(int(batch.get("total", 0)), len(staged_notes) + int(batch.get("skipped", 0))),
            "created": sum(1 for path in committed if str(path.resolve()) not in baseline),
            "updated": sum(1 for path in committed if str(path.resolve()) in baseline),
            "skipped": skipped_count,
            "failed": failed_count,
            "outputs": [str(path) for path in committed],
            "recovery_dir": str(transaction_root) if failed_count else "",
            "error": runner_error,
        }
        print("VIDEO_TRANSACTION_RESULT_JSON:" + json.dumps(result, ensure_ascii=False), flush=True)
        if not failed_count:
            shutil.rmtree(transaction_root, ignore_errors=True)
        return 1 if failed_count else 0
    except BaseException as exc:
        recovery_error = ""
        try:
            recover_video_transactions(output_root)
        except Exception as recovery_exc:
            recovery_error = f"recovery failed: {type(recovery_exc).__name__}"
        # Recovery persisted a newer state; never overwrite it with this stale object.
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["transaction_error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
        if recovery_error:
            journal["recovery_error"] = recovery_error
        _save_transaction_journal(journal_path, journal)
        failure_result = {
            "total": len(staged_notes), "created": 0, "updated": 0, "skipped": 0,
            "failed": max(1, len(staged_notes) - len(committed)), "outputs": [],
            "recovery_dir": str(transaction_root), "error": journal["transaction_error"],
            "recovery_error": recovery_error,
        }
        print("VIDEO_TRANSACTION_RESULT_JSON:" + json.dumps(failure_result, ensure_ascii=False), flush=True)
        raise


def parse_markdown_metadata(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    title_match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    if title_match:
        fields["title"] = title_match.group(1).strip()
    for key, pattern in FIELD_PATTERNS.items():
        match = re.search(pattern, body, flags=re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    source_url = fields.get("source_url", "")
    bvid_match = re.search(r"(BV[0-9A-Za-z]+)", source_url)
    if bvid_match:
        fields["bvid"] = bvid_match.group(1)
    if source_url.startswith("file://"):
        fields["source_path"] = source_url.removeprefix("file://")
        fields["source_type"] = "local-video"
    elif "bilibili.com" in source_url:
        fields["source_type"] = "bilibili"
    else:
        fields["source_type"] = "video"
    return fields


def upsert_manifest_item(manifest: dict[str, object], item: dict[str, object]) -> None:
    items = manifest.setdefault("items", [])
    if not isinstance(items, list):
        manifest["items"] = []
        items = manifest["items"]
    source_url = str(item.get("source_url") or "")
    source_path = str(item.get("source_path") or "")
    output_path = str(item.get("output_path") or "")
    for index, old in enumerate(items):
        if not isinstance(old, dict):
            continue
        same_url = bool(source_url) and old.get("source_url") == source_url
        same_path = bool(source_path) and old.get("source_path") == source_path
        same_output = bool(output_path) and old.get("output_path") == output_path
        if same_url or same_path or same_output:
            items[index] = {**old, **item}
            return
    items.append(item)


def postprocess_video_note(path: pathlib.Path, cfg: dict[str, str], extra: dict[str, str] | None = None) -> dict[str, object]:
    markdown = path.read_text(encoding="utf-8", errors="replace")
    existing_meta, body = parse_frontmatter(markdown)
    fields = parse_markdown_metadata(body)
    if extra:
        fields.update({key: value for key, value in extra.items() if value})

    if fields.get("source_type") == "local-video" and fields.get("duration", "") in {"", "未知"}:
        source_path = pathlib.Path(fields.get("source_path", ""))
        duration = probe_media_duration(source_path) if source_path.is_file() else ""
        if duration:
            fields["duration"] = duration
            body = re.sub(
                r"(?m)^>\s*\*\*视频时长\*\*：.*$",
                f"> **视频时长**：{duration}",
                body,
                count=1,
            )

    source_type = fields.get("source_type", "video")
    tags = ["video", f"source/{source_type}", "status/draft"]
    source_ref = fields.get("source_url") or fields.get("source_path") or rel(path)
    source_hash = sha256_text(body)
    title = fields.get("title") or path.stem
    meta: dict[str, object] = {
        "title": title,
        "type": "video-note",
        "source_type": source_type,
        "source_path": fields.get("source_path", ""),
        "source_url": fields.get("source_url", ""),
        "bvid": fields.get("bvid", ""),
        "avid": fields.get("avid", ""),
        "author": fields.get("author", ""),
        "published": fields.get("published", ""),
        "duration": fields.get("duration", ""),
        "transcript_source": fields.get("transcript_source", ""),
        "transcribed_at": fields.get("transcribed_at", ""),
        "created": str(existing_meta.get("created") or today()),
        "updated": today(),
        "status": "draft",
        "model": cfg["DEFAULT_LLM_MODEL"],
        "tags": tags,
        "source_hash": source_hash,
    }
    atomic_write_text(path, "\n\n".join([frontmatter(meta), body]).rstrip() + "\n")

    keyframe_info: dict[str, object] = {"enabled": False, "status": "disabled", "assets": []}
    if parse_bool(cfg.get("EXTRACT_KEYFRAMES", "false")):
        try:
            keyframe_info = add_keyframes_to_note(
                path,
                meta,
                max_frames=max(1, int(cfg.get("KEYFRAME_MAX_COUNT") or 4)),
                cookie_file=str(cfg.get("BILIBILI_COOKIES_FILE") or ""),
            )
            if keyframe_info.get("status") == "generated":
                print(f"keyframes: {rel(path)} -> {len(keyframe_info.get('assets', []))} 张")
            elif keyframe_info.get("reason"):
                print(f"keyframes skipped: {rel(path)} ({keyframe_info['reason']})")
        except Exception as exc:
            keyframe_info = {"enabled": True, "status": "failed", "reason": str(exc), "assets": []}
            print(f"keyframes failed: {rel(path)} ({exc})", file=sys.stderr)

    return {
        "source_path": fields.get("source_path", ""),
        "source_url": fields.get("source_url", ""),
        "source_ref": source_ref,
        "source_type": source_type,
        "source_hash": source_hash,
        "output_path": rel(path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": cfg["DEFAULT_LLM_MODEL"],
        "title": title,
        "bvid": fields.get("bvid", ""),
        "avid": fields.get("avid", ""),
        "author": fields.get("author", ""),
        "published": fields.get("published", ""),
        "duration": fields.get("duration", ""),
        "transcript_source": fields.get("transcript_source", ""),
        "transcribed_at": fields.get("transcribed_at", ""),
        "keyframe_status": keyframe_info.get("status", "disabled"),
        "keyframe_assets": keyframe_info.get("assets", []),
        "keyframes": keyframe_info.get("frames", []),
        "keyframe_error": keyframe_info.get("reason", ""),
        "error": "",
    }


def postprocess_video_notes(paths: list[str], cfg: dict[str, str], extras: dict[str, dict[str, str]] | None = None) -> None:
    if not paths:
        return
    manifest_enabled = parse_bool(cfg.get("VIDEO_MANIFEST_ENABLED", "true"))
    manifest_path: pathlib.Path | None = None
    manifest: dict[str, object] = {"items": []}
    if manifest_enabled:
        index_dir = pathlib.Path(cfg.get("INDEX_DIR", "indexes"))
        if not index_dir.is_absolute():
            index_dir = ROOT / index_dir
        manifest_path = index_dir / "video-manifest.json"
        manifest = load_manifest(manifest_path)
    for raw_path in paths:
        path = pathlib.Path(raw_path)
        if not path.exists():
            continue
        initial_validation = video_validation(path, cfg)
        if not initial_validation.complete:
            raise RuntimeError(f"视频笔记尚未完成，未写入完成索引：{path.name}: {'；'.join(initial_validation.errors)}")
        key = path.as_posix()
        item = postprocess_video_note(path, cfg, (extras or {}).get(key))
        if item.get("keyframe_status") == "failed":
            raise RuntimeError(f"关键帧生成失败，视频结果保留待恢复：{item.get('keyframe_error') or path.name}")
        final_validation = video_validation(path, cfg)
        if not final_validation.complete:
            raise RuntimeError(f"最终视频笔记校验失败，未写入完成索引：{path.name}: {'；'.join(final_validation.errors)}")
        if manifest_enabled:
            upsert_manifest_item(manifest, item)
            print(f"manifest: {rel(path)}")
        else:
            print(f"frontmatter: {rel(path)}")
    if manifest_enabled and manifest_path is not None:
        save_manifest(manifest_path, manifest)


def repair_local_video_note_duration(path: pathlib.Path) -> str:
    markdown = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(markdown)
    fields = parse_markdown_metadata(body)
    if fields.get("source_type") != "local-video" or fields.get("duration", "") not in {"", "未知"}:
        return ""
    source_path = pathlib.Path(fields.get("source_path", ""))
    if not source_path.is_file():
        return ""
    duration = probe_media_duration(source_path)
    if not duration:
        return ""

    updated_body = re.sub(
        r"(?m)^>\s*\*\*视频时长\*\*：.*$",
        f"> **视频时长**：{duration}",
        body,
        count=1,
    )
    if meta:
        meta["duration"] = duration
        updated = "\n\n".join([frontmatter(meta), updated_body]).rstrip() + "\n"
    else:
        updated = updated_body.rstrip() + "\n"
    path.write_text(updated, encoding="utf-8")
    return duration


def repair_local_video_durations(root: pathlib.Path) -> int:
    if not root.exists():
        raise FileNotFoundError(root)
    paths = [root] if root.is_file() else sorted(root.rglob("*.md"))
    repaired = 0
    missing_source = 0
    for path in paths:
        markdown = path.read_text(encoding="utf-8", errors="replace")
        fields = parse_markdown_metadata(parse_frontmatter(markdown)[1])
        if fields.get("source_type") != "local-video" or fields.get("duration", "") not in {"", "未知"}:
            continue
        duration = repair_local_video_note_duration(path)
        if duration:
            repaired += 1
            print(f"duration repaired: {path} -> {duration}")
        else:
            missing_source += 1
            print(f"duration skipped (source unavailable): {path}", file=sys.stderr)
    print(f"duration repair done repaired={repaired} unavailable={missing_source}")
    return 0 if missing_source == 0 else 1


def append_processed(avid: str, cfg: dict[str, str], note_path: str = "") -> None:
    if not parse_bool(cfg.get("BILIBILI_INCREMENTAL_STATE_ENABLED", "true")):
        return
    stage = cfg.get("VIDEO_TRANSACTION_STAGE_DIR")
    if stage:
        if not note_path:
            raise ValueError("事务内完成标记必须关联笔记路径")
        path = pathlib.Path(note_path).resolve()
        try:
            relative = path.relative_to(pathlib.Path(stage).resolve())
            target = pathlib.Path(cfg["VIDEO_TRANSACTION_FINAL_DIR"]).resolve() / relative
            existing = False
        except ValueError:
            target, existing = path, True
        pending_path = pathlib.Path(cfg["INDEX_DIR"]) / "pending-processed.json"
        pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.is_file() else []
        pending.append({"avid": avid, "target": str(target), "existing": existing})
        atomic_write_text(pending_path, json.dumps(pending, ensure_ascii=False))
        return
    state_dir = pathlib.Path(os.path.expandvars(cfg.get("BILIBILI_STATE_DIR", str(ROOT / "indexes/bilibili-state")))).expanduser()
    if not state_dir.is_absolute():
        state_dir = ROOT / state_dir
    _merge_processed_ids(state_dir / "processed_videos.txt", [avid])


def run_url(project_dir: pathlib.Path, cfg: dict[str, str], url: str, dry_run: bool, output_filename: str = "") -> int:
    env = project_env(cfg)
    script_dir = project_dir / "scripts" / "bilibili"
    transcript = script_dir / "bilibili_transcript.sh"
    batch = script_dir / "batch_transcribe.py"
    transcribe_command = bash_command(cfg, transcript, url)
    if output_filename:
        transcribe_command.extend(["--output-filename", output_filename])
    print("transcribe:", " ".join(transcribe_command))
    if dry_run:
        print("then run summary-only for the generated Markdown")
        return 0

    code, output = stream_command(transcribe_command, project_dir, env, timeout=36000)
    if code != 0:
        return code

    paths = extract_markdown_paths(output)
    if not paths:
        paths = [str(stage_existing_note(pathlib.Path(path), cfg)) for path in extract_retryable_existing_markdown_paths(output, cfg)]
        if paths:
            print(f"检测到已有但未完成的笔记，保留转写并重试 summary-only: {paths[-1]}")
        else:
            skipped = extract_skipped_existing_markdown_paths(output)
            if skipped:
                valid_existing = all(video_validation(pathlib.Path(path), cfg).complete for path in skipped)
                if valid_existing:
                    print("[无需更新] 已有同名完整笔记；未调用 ASR/Qwen，正式文件保持不变。")
                    record_transaction_batch(cfg, {"total": len(skipped), "skipped": len(skipped), "failed": 0})
                    return 0
                print("[补跑失败] 同名笔记未通过完成校验，且没有可恢复原文；请重新转写。", file=sys.stderr)
                return 1
            print("未从输出中识别到 Markdown 路径，无法确认完成状态", file=sys.stderr)
            return 1

    failures = 0
    if cfg.get("VIDEO_OUTPUT_MODE", "full") != "transcription-only":
        for path in paths[-1:]:
            summary_command = python_command(cfg, batch) + ["--summary-only", path]
            print("\nsummary:", " ".join(summary_command))
            summary_code, _summary_output = stream_command(summary_command, project_dir, env, timeout=36000)
            if summary_code != 0:
                failures += 1
    for path in paths[-1:]:
        validation = video_validation(pathlib.Path(path), cfg)
        if not validation.complete:
            failures += 1
            print(f"视频完成校验失败: {'；'.join(validation.errors)}", file=sys.stderr)
    if not failures:
        try:
            postprocess_video_notes(paths[-1:], cfg)
        except Exception as exc:
            failures += 1
            for path in paths[-1:]:
                mark_video_output_failed(path, str(exc))
            print(f"视频后处理失败，暂存结果保留：{exc}", file=sys.stderr)
    return 1 if failures else 0


def run_local_file(project_dir: pathlib.Path, cfg: dict[str, str], local_file: str, dry_run: bool, output_filename: str = "") -> int:
    env = project_env(cfg)
    script_dir = project_dir / "scripts" / "bilibili"
    transcript = script_dir / "bilibili_transcript.sh"
    batch = script_dir / "batch_transcribe.py"
    transcribe_command = bash_command(cfg, transcript, "--local-file", local_file)
    if output_filename:
        transcribe_command.extend(["--output-filename", output_filename])
    print("transcribe:", " ".join(transcribe_command))
    if dry_run:
        print("then run summary-only for the generated Markdown")
        return 0

    code, output = stream_command(transcribe_command, project_dir, env, timeout=36000)
    if code != 0:
        return code

    paths = extract_markdown_paths(output)
    if not paths:
        paths = [str(stage_existing_note(pathlib.Path(path), cfg)) for path in extract_retryable_existing_markdown_paths(output, cfg)]
        if paths:
            print(f"检测到已有但未完成的笔记，保留转写并重试 summary-only: {paths[-1]}")
        else:
            skipped_paths = extract_skipped_existing_markdown_paths(output)
            if skipped_paths:
                valid_existing = all(video_validation(pathlib.Path(path), cfg).complete for path in skipped_paths)
                if valid_existing:
                    print("[无需更新] 已有同名完整笔记；未调用 ASR 或 Qwen，正式文件保持不变。")
                else:
                    print("[补跑失败] 同名笔记未通过视频完成校验，且缺少可恢复原文；请重新转写。", file=sys.stderr)
                record_transaction_batch(cfg, {"total": 1, "skipped": 1 if valid_existing else 0, "failed": 0 if valid_existing else 1})
                emit_local_batch_result(1, 0, 1 if valid_existing else 0, 0 if valid_existing else 1)
                return 0 if valid_existing else 1
            else:
                print("未从输出中识别到 Markdown 路径，无法确认完成状态", file=sys.stderr)
            return 1

    summary_code = 0
    if cfg.get("VIDEO_OUTPUT_MODE", "full") != "transcription-only":
        summary_command = python_command(cfg, batch) + ["--summary-only", paths[-1]]
        print("\nsummary:", " ".join(summary_command))
        summary_code, _summary_output = stream_command(summary_command, project_dir, env, timeout=36000)
    validation = video_validation(pathlib.Path(paths[-1]), cfg)
    if not validation.complete:
        summary_code = 1
        print(f"视频完成校验失败: {'；'.join(validation.errors)}", file=sys.stderr)
    if summary_code == 0:
        try:
            postprocess_video_notes(paths[-1:], cfg)
        except Exception as exc:
            summary_code = 1
            mark_video_output_failed(paths[-1], str(exc))
            print(f"视频后处理失败，暂存结果保留：{exc}", file=sys.stderr)
    # A retained output still counts as changed even if validation or summary
    # failed. The separate failed count prevents callers from indexing it as done.
    record_transaction_batch(cfg, {"total": 1, "skipped": 0, "failed": 1 if summary_code else 0})
    emit_local_batch_result(1, 1, 0, 1 if summary_code else 0)
    return summary_code


def batch_failure_path(cfg: dict[str, str]) -> pathlib.Path:
    root = cfg.get("VIDEO_TRANSACTION_FINAL_DIR") or cfg["BILIBILI_OUTPUT_DIR"]
    return pathlib.Path(root).expanduser() / ".local-note-studio-batch-failures.json"


def save_batch_failures(cfg: dict[str, str], collection: dict[str, str], failures: list[dict[str, str]]) -> pathlib.Path | None:
    if not parse_bool(cfg.get("BILIBILI_INCREMENTAL_STATE_ENABLED", "true")):
        return None
    path = batch_failure_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"collection": collection, "failures": failures, "updated_at": now_iso()}
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def load_batch_failures(cfg: dict[str, str], collection: dict[str, str]) -> list[dict[str, str]]:
    if not parse_bool(cfg.get("BILIBILI_INCREMENTAL_STATE_ENABLED", "true")):
        raise RuntimeError("隐身模式不读取批量失败状态；请关闭隐身模式后再使用“只重试失败项”。")
    path = batch_failure_path(cfg)
    if not path.exists():
        raise RuntimeError("没有可重试的失败列表；请先运行一次收藏夹/系列批处理。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    saved = payload.get("collection") or {}
    if saved.get("type") != collection.get("type") or str(saved.get("id")) != str(collection.get("id")):
        raise RuntimeError("失败列表属于另一个收藏夹/系列，请先切回原目标或重新运行当前批次。")
    return [
        item
        for item in payload.get("failures") or []
        if isinstance(item, dict) and str(item.get("manual_status") or "failed") in {"failed", "rebuild"}
    ]


def collection_llm_cooldown(cfg: dict[str, str]) -> float:
    try:
        return max(0.0, float(cfg.get("COOLDOWN_DELAY") or 0))
    except (TypeError, ValueError):
        print("COOLDOWN_DELAY 配置无效，本次收藏夹批处理不执行 LLM Cool Down。", file=sys.stderr)
        return 0.0


def wait_for_collection_llm_cooldown(delay: float, next_index: int, total: int) -> None:
    if delay <= 0:
        return
    deadline = time.monotonic() + delay
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(
            f"[Qwen {next_index}/{total}] LLM Cool Down，剩余 {int(remaining + 0.999)} 秒...",
            flush=True,
        )
        time.sleep(min(1.0, remaining))


def run_collection_batch(
    project_dir: pathlib.Path,
    cfg: dict[str, str],
    limit: int,
    dry_run: bool,
    collection_type: str,
    collection_id: str,
    collection_mid: str,
    retry_failed: bool,
) -> int:
    env = project_env(cfg)
    script_dir = project_dir / "scripts" / "bilibili"
    scanner = script_dir / "bilibili_scanner.py"
    transcript = script_dir / "bilibili_transcript.sh"
    batch = script_dir / "batch_transcribe.py"
    collection = {"type": collection_type, "id": collection_id, "mid": collection_mid}
    scan_command = python_command(cfg, scanner) + [
        "--collection-type", collection_type,
        "--collection-id", collection_id,
    ]
    if collection_mid:
        scan_command.extend(["--collection-mid", collection_mid])
    llm_cooldown = collection_llm_cooldown(cfg)
    print("scan:", " ".join(scan_command))
    if dry_run:
        scope = "all" if limit == 0 else str(limit)
        print(f"then process {scope} new video(s); retry_failed={retry_failed}; LLM Cool Down={llm_cooldown:g}s")
        return 0
    print(f"LLM Cool Down：{llm_cooldown:g} 秒（两次 Qwen 调用之间）")

    if retry_failed:
        videos = load_batch_failures(cfg, collection)
        print(f"读取失败列表：{len(videos)} 条待重试")
    else:
        scan_code, scan_output = stream_command(scan_command, project_dir, env, timeout=120)
        if scan_code != 0:
            return scan_code
        videos = parse_scanner_output(scan_output)
    if not videos:
        print("没有新视频或失败条目需要处理")
        result = {"total": 0, "processed": 0, "success": 0, "failed": 0, "current": "", "failures": []}
        print("BATCH_RESULT_JSON:" + json.dumps(result, ensure_ascii=False))
        return 0

    selected = videos if limit <= 0 else videos[:limit]
    print(f"\n批量处理 {len(selected)}/{len(videos)} 个视频")
    failed_items: list[dict[str, str]] = []
    success_count = 0
    skipped_count = 0
    processed_paths: list[str] = []
    extras: dict[str, dict[str, str]] = {}
    for index, video in enumerate(selected, 1):
        bvid = video.get("bvid", "")
        avid = video.get("avid", "")
        title = video.get("title", bvid)
        if not bvid:
            print(f"跳过无 BVID 条目: {video}", file=sys.stderr)
            failed_items.append({**video, "stage": "scan", "error": "缺少 BVID"})
            continue
        print(f"\n[转录 {index}/{len(selected)}] {title} ({bvid})")
        existing_path = str(video.get("path") or "") if retry_failed and video.get("stage") == "qwen" else ""
        if existing_path and pathlib.Path(existing_path).exists():
            paths = [str(stage_existing_note(pathlib.Path(existing_path), cfg))]
            code = 0
        else:
            transcribe_command = bash_command(cfg, transcript, f"https://www.bilibili.com/video/{bvid}/")
            code, output = stream_command(transcribe_command, project_dir, env, timeout=36000, allow_failure=True)
            paths = extract_markdown_paths(output) if code == 0 else []
            if code == 0 and not paths:
                skipped = extract_skipped_existing_markdown_paths(output)
                if skipped and all(video_validation(pathlib.Path(path), cfg).complete for path in skipped):
                    success_count += 1
                    skipped_count += 1
                    if avid:
                        append_processed(avid, cfg, skipped[-1])
                    continue
                paths = [str(stage_existing_note(pathlib.Path(path), cfg)) for path in extract_retryable_existing_markdown_paths(output, cfg)]
        if code != 0 or not paths:
            failed_items.append({**video, "stage": "transcribe", "error": "转录失败或未生成 Markdown"})
            print(f"[转录 {index}/{len(selected)}] 失败", file=sys.stderr)
            continue
        print(f"[转录 {index}/{len(selected)}] 完成")
        for path in paths[-1:]:
            extras[path] = {
                "avid": avid,
                "bvid": bvid,
                "title": title,
                "author": video.get("upper", ""),
                "duration": video.get("duration", ""),
            }
            summary_code = 0
            if cfg.get("VIDEO_OUTPUT_MODE", "full") != "transcription-only":
                summary_command = python_command(cfg, batch) + ["--summary-only", path]
                print(f"\n[Qwen {index}/{len(selected)}] {title}")
                print("summary:", " ".join(summary_command))
                summary_code, _summary_output = stream_command(summary_command, project_dir, env, timeout=36000, allow_failure=True)
            validation = video_validation(pathlib.Path(path), cfg)
            if summary_code != 0 or not validation.complete:
                mark_video_output_failed(path, "；".join(validation.errors) or "Qwen 整理失败")
                failed_items.append({**video, "stage": "qwen", "path": path,
                                     "error": "；".join(validation.errors) or "Qwen 整理失败"})
                print(f"[Qwen {index}/{len(selected)}] 失败：{'；'.join(validation.errors)}", file=sys.stderr)
            else:
                try:
                    postprocess_video_notes([path], cfg, extras)
                except Exception as exc:
                    failed_items.append({**video, "stage": "postprocess", "path": path, "error": str(exc)})
                    mark_video_output_failed(path, str(exc))
                    print(f"[后处理 {index}/{len(selected)}] 失败：{exc}", file=sys.stderr)
                    continue
                print(f"[Qwen {index}/{len(selected)}] 完成")
                if avid:
                    append_processed(avid, cfg, path)
                success_count += 1
                processed_paths.append(path)
            if index < len(selected) and llm_cooldown > 0:
                wait_for_collection_llm_cooldown(llm_cooldown, index + 1, len(selected))
    record_transaction_batch(cfg, {"total": len(selected), "skipped": skipped_count, "failed": len(failed_items)})
    failure_file = save_batch_failures(cfg, collection, failed_items)
    result = {
        "total": len(selected),
        "processed": success_count + len(failed_items),
        "success": success_count,
        "failed": len(failed_items),
        "current": selected[-1].get("title", "") if selected else "",
        "failure_file": str(failure_file) if failure_file else "",
        "failures": failed_items,
    }
    print(f"\n批量完成：总数 {len(selected)}，成功 {success_count}，失败 {len(failed_items)}。")
    print("BATCH_RESULT_JSON:" + json.dumps(result, ensure_ascii=False))
    # A single failure must not abort remaining entries; the structured result drives retry.
    return 0


def main() -> int:
    cfg = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--favorite", action="store_true", help="process configured Bilibili favorite list")
    parser.add_argument("--url", help="process one Bilibili video URL")
    parser.add_argument("--local-file", help="process one local video or audio file")
    parser.add_argument("--local-dir", help="process a local video directory")
    parser.add_argument("--recursive", action="store_true", help="recurse through local directory")
    parser.add_argument("--summary-only", action="store_true", help="fill summaries for existing Markdown outputs")
    parser.add_argument("--repair-local-durations", help="repair unknown durations in existing local-video Markdown files")
    parser.add_argument("--limit", type=int, default=0, help="in favorite mode, process only the first N new videos")
    parser.add_argument("--collection-type", choices=["favorite", "series"], default="favorite")
    parser.add_argument("--collection-id", default="")
    parser.add_argument("--collection-mid", default="")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--no-video-manifest", action="store_true", help="skip writing indexes/video-manifest.json after postprocessing")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing Markdown outputs")
    parser.add_argument("--output-filename", default="", help="custom Markdown file name for one URL/local file; directory separators are not allowed")
    parser.add_argument("--sync-env", action="store_true", help="deprecated after migration; current project env.local is used directly")
    parser.add_argument("--dry-run", action="store_true", help="print the command without running it")
    args = parser.parse_args()
    if args.no_video_manifest:
        cfg["VIDEO_MANIFEST_ENABLED"] = "false"
    if args.overwrite:
        cfg["OVERWRITE_OUTPUT"] = "true"

    project_dir = ROOT
    script_dir = ROOT / "scripts" / "bilibili"

    selected = sum(bool(item) for item in [args.favorite, args.url, args.local_file, args.local_dir, args.summary_only, args.repair_local_durations])
    if selected != 1:
        parser.error("choose exactly one of --favorite, --url, --local-file, --local-dir, --summary-only")

    if args.favorite:
        collection_id = args.collection_id or cfg.get("BILIBILI_FAV_MEDIA_ID", "")
        if not collection_id:
            parser.error("请先在界面读取并选择收藏夹/系列")
        runner = lambda staged_cfg: run_collection_batch(
            project_dir, staged_cfg, max(0, args.limit), args.dry_run,
            args.collection_type, collection_id, args.collection_mid, args.retry_failed,
        )
        if args.dry_run:
            return runner(cfg)
        return run_video_transaction(cfg["BILIBILI_OUTPUT_DIR"], cfg, runner)

    if args.repair_local_durations:
        repair_root = pathlib.Path(os.path.expanduser(os.path.expandvars(args.repair_local_durations))).resolve()
        print(f"repair local durations: {repair_root}")
        if args.dry_run:
            return 0
        return repair_local_video_durations(repair_root)

    if args.url:
        if args.sync_env:
            print("--sync-env is no longer needed after migration; current project env.local is used directly.")
        runner = lambda staged_cfg: run_url(project_dir, staged_cfg, args.url, args.dry_run, args.output_filename)
        if args.dry_run:
            return runner(cfg)
        return run_video_transaction(cfg["BILIBILI_OUTPUT_DIR"], cfg, runner)

    if args.local_file:
        if args.sync_env:
            print("--sync-env is no longer needed after migration; current project env.local is used directly.")
        runner = lambda staged_cfg: run_local_file(project_dir, staged_cfg, args.local_file, args.dry_run, args.output_filename)
        if args.dry_run:
            return runner(cfg)
        return run_video_transaction(cfg["BILIBILI_OUTPUT_DIR"], cfg, runner)

    def run_local_directory(staged_cfg: dict[str, str]) -> int:
        if args.output_filename:
            parser.error("--output-filename cannot be used with --local-dir")
        command = python_command(staged_cfg, script_dir / "batch_transcribe.py")
        command.extend(["--local-dir", args.local_dir or "", "--output-dir", staged_cfg["BILIBILI_OUTPUT_DIR"]])
        if args.recursive:
            command.append("--recursive")
        env = project_env(staged_cfg)
        if args.sync_env:
            print("--sync-env is no longer needed after migration; current project env.local is used directly.")
        print(" ".join(command))
        if args.dry_run:
            return 0
        code, output = stream_command(command, project_dir, env, timeout=36000, allow_failure=True)
        paths = extract_markdown_paths(output)
        postprocess_failed = False
        for path in paths:
            if not video_validation(pathlib.Path(path), staged_cfg).complete:
                continue
            try:
                postprocess_video_notes([path], staged_cfg)
            except Exception as exc:
                postprocess_failed = True
                mark_video_output_failed(path, str(exc))
                print(f"视频后处理失败：{exc}", file=sys.stderr)
        return 1 if code != 0 or postprocess_failed else 0

    if args.local_dir:
        if args.dry_run:
            return run_local_directory(cfg)
        return run_video_transaction(cfg["BILIBILI_OUTPUT_DIR"], cfg, run_local_directory)

    command = python_command(cfg, script_dir / "batch_transcribe.py") + ["--summary-only"]
    env = project_env(cfg)
    if args.sync_env:
        print("--sync-env is no longer needed after migration; current project env.local is used directly.")
    print(" ".join(command))
    if args.dry_run:
        return 0
    code, output = stream_command(command, project_dir, env, timeout=36000)
    paths = extract_markdown_paths(output)
    if code == 0:
        postprocess_video_notes(paths, cfg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
