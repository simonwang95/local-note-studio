#!/usr/bin/env python3
"""Shared safety, audit, and result primitives for Local Note Studio automation."""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import threading
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterator


RESULT_SCHEMA_VERSION = "1.0"
WORKER_VERSION = "0.1.19"
PROFILE_SCHEMA_VERSION = "1.0"
RULES_VERSION = "1.0"
SECRET_KEYS = {
    "api_key",
    "cookie",
    "cookies",
    "authorization",
    "token",
    "password",
    "secret",
}
PUBLIC_NUMERIC_DIAGNOSTIC_KEYS = {
    "completion_tokens",
    "max_tokens",
}
OMITTED_CONTRACT_KEYS = {
    "base64",
    "browser_profile",
    "image_base64",
    "image_data",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def app_data_root() -> pathlib.Path:
    configured = os.environ.get("LOCAL_NOTE_STUDIO_APP_DATA_DIR", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser().resolve()
    state = os.environ.get("LOCAL_NOTE_STUDIO_STATE_DIR", "").strip()
    if state:
        return pathlib.Path(state).expanduser().resolve().parent
    return pathlib.Path.home() / "Library/Application Support/Local Note Studio"


def state_dir() -> pathlib.Path:
    configured = os.environ.get("LOCAL_NOTE_STUDIO_STATE_DIR", "").strip()
    path = pathlib.Path(configured).expanduser() if configured else app_data_root() / "state"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        path.chmod(0o700)
    return path


def new_run_id() -> str:
    return str(uuid.uuid4())


def stable_source_ref(source: str) -> str:
    value = str(source or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, "", ""))
        match = re.search(r"(?:BV[0-9A-Za-z]+|opus/\d+|space\.bilibili\.com/\d+)", clean, re.I)
        return match.group(0) if match else "url:" + hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]
    return "file:" + hashlib.sha256(str(pathlib.Path(value).expanduser()).encode("utf-8")).hexdigest()[:16]


def redact_text(value: str) -> str:
    text = str(value)
    patterns = (
        r"(?i)([?&](?:access[_-]?token|api[_-]?key|authorization|password|secret|signature|sig|key)=)[^&#\s]+",
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+",
        r"(?i)((?:api[\s_-]?key|token|password|secret)(?:\s+provided)?\s*[:=]\s*)[^\s,;]+",
        r"(?i)(SESSDATA|bili_jct|DedeUserID|buvid3)=([^;\s]+)",
        r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b",
    )
    for pattern in patterns:
        replacement = r"\1<redacted>" if "(" in pattern and not pattern.startswith(r"(?i)\bsk-") else "<redacted>"
        text = re.sub(pattern, replacement, text)
    return text


def sanitize_mapping(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in PUBLIC_NUMERIC_DIAGNOSTIC_KEYS:
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    clean[key] = item
                continue
            if lowered in OMITTED_CONTRACT_KEYS or any(secret in lowered for secret in SECRET_KEYS):
                continue
            clean[key] = sanitize_mapping(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitize_mapping(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class AutomationError(RuntimeError):
    def __init__(self, message: str, error_code: str, retryable: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class LockBusyError(AutomationError):
    def __init__(self, metadata: dict[str, Any] | None = None):
        owner = metadata or {}
        detail = f"pid={owner.get('pid', '?')} task={owner.get('task', '?')} caller={owner.get('caller', '?')}"
        super().__init__(f"another Local Note Studio task is running ({detail})", "TASK_LOCKED", True)
        self.metadata = owner


def classify_error(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, AutomationError):
        return exc.error_code, exc.retryable
    if isinstance(exc, TimeoutError):
        return "TASK_TIMEOUT", True
    if isinstance(exc, KeyboardInterrupt):
        return "TASK_CANCELLED", True
    if isinstance(exc, sqlite3.Error):
        return "STATE_STORAGE_ERROR", True
    text = str(exc).lower()
    rules = (
        (("cookie", "未登录", "login"), "BILIBILI_AUTH_INVALID", True),
        (("412", "risk", "风控"), "BILIBILI_RATE_LIMITED", True),
        (("subtitle", "字幕"), "BILIBILI_SUBTITLE_UNAVAILABLE", True),
        (("download", "下载"), "BILIBILI_DOWNLOAD_FAILED", True),
        (("whisper", "asr", "转写"), "ASR_FAILED", True),
        (("llm", "qwen", "model", "模型"), "LLM_FAILED", True),
        (("integrity", "完整性"), "OUTPUT_INTEGRITY_FAILED", True),
        (("permission", "access denied", "无权限"), "SOURCE_ACCESS_DENIED", False),
        (("not found", "不存在"), "SOURCE_NOT_FOUND", False),
        (("unsupported", "不支持"), "UNSUPPORTED_REQUEST", False),
    )
    for needles, code, retryable in rules:
        if any(needle in text for needle in needles):
            return code, retryable
    if isinstance(exc, (ValueError, TypeError)):
        return "INVALID_REQUEST", False
    return "TASK_FAILED", True


@dataclass
class TaskResult:
    run_id: str
    caller: str
    task: str
    status: str
    started_at: str
    finished_at: str | None = None
    source_ref: str = ""
    output_dir: str = ""
    counts: dict[str, int] = field(
        default_factory=lambda: {
            "discovered": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
        }
    )
    outputs: list[str] = field(default_factory=list)
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    manifest_path: str = ""
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    retryable: bool = False
    schema_version: str = RESULT_SCHEMA_VERSION

    def finish(self) -> "TaskResult":
        self.finished_at = self.finished_at or utc_now()
        return self

    def as_dict(self) -> dict[str, Any]:
        return sanitize_mapping(asdict(self))


class GlobalTaskLock:
    """Advisory cross-process lock whose metadata never contains request secrets."""

    def __init__(self, task: str, caller: str, run_id: str, timeout_seconds: int = 0):
        self.task = task
        self.caller = caller
        self.run_id = run_id
        self.timeout_seconds = max(0, int(timeout_seconds))
        self.lock_path = state_dir() / "global-task.lock"
        self.metadata_path = state_dir() / "global-task.json"
        self._handle: Any = None

    def _metadata(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "task": self.task,
            "caller": self.caller,
            "pid": os.getpid(),
            "started_at": utc_now(),
        }

    def acquire(self) -> "GlobalTaskLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._handle = self.lock_path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    self._handle.close()
                    self._handle = None
                    raise
                if self.timeout_seconds == 0 or time.monotonic() >= deadline:
                    metadata = read_lock_status()
                    self._handle.close()
                    self._handle = None
                    raise LockBusyError(metadata)
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        _atomic_json(self.metadata_path, self._metadata(), mode=0o600)
        os.set_inheritable(self._handle.fileno(), True)
        os.environ["LOCAL_NOTE_STUDIO_LOCK_FD"] = str(self._handle.fileno())
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        with contextlib.suppress(OSError):
            current = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if current.get("run_id") == self.run_id:
                self.metadata_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        if os.environ.get("LOCAL_NOTE_STUDIO_LOCK_FD") == str(self._handle.fileno()):
            os.environ.pop("LOCAL_NOTE_STUDIO_LOCK_FD", None)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "GlobalTaskLock":
        return self.acquire()

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.release()


def read_lock_status() -> dict[str, Any] | None:
    lock_path = state_dir() / "global-task.lock"
    metadata_path = state_dir() / "global-task.json"
    if not lock_path.exists():
        return None
    handle = lock_path.open("r", encoding="utf-8")
    held = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            held = True
        if not held:
            return None
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            return sanitize_mapping(raw) if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return {"status": "running", "metadata": "unavailable"}
    finally:
        if not held:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _atomic_json(path: pathlib.Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.chmod(mode)
    os.replace(temp, path)


class HistoryStore:
    def __init__(self, path: pathlib.Path | None = None):
        self.path = path or state_dir() / "automation-history.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()
        self._recover_interrupted()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                  run_id TEXT PRIMARY KEY,
                  owner_pid INTEGER,
                  caller TEXT NOT NULL,
                  task TEXT NOT NULL,
                  profile_id TEXT NOT NULL DEFAULT '',
                  request_json TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  status TEXT NOT NULL,
                  result_json TEXT,
                  outputs_json TEXT NOT NULL DEFAULT '[]',
                  counts_json TEXT NOT NULL DEFAULT '{}',
                  error_code TEXT,
                  retryable INTEGER NOT NULL DEFAULT 0,
                  retry_of TEXT,
                  worker_version TEXT NOT NULL,
                  schema_version TEXT NOT NULL,
                  rules_version TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
            if "owner_pid" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN owner_pid INTEGER")
            connection.execute("CREATE INDEX IF NOT EXISTS runs_started_at ON runs(started_at DESC)")

    def _recover_interrupted(self) -> None:
        lock = read_lock_status()
        active_run_id = str((lock or {}).get("run_id") or "")
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM runs WHERE status IN ('running', 'waiting')").fetchall()
            for row in rows:
                if active_run_id and row["run_id"] == active_run_id:
                    continue
                if _pid_is_alive(int(row["owner_pid"] or 0)):
                    continue
                finished_at = utc_now()
                try:
                    request = json.loads(row["request_json"] or "{}")
                except ValueError:
                    request = {}
                result = TaskResult(
                    run_id=row["run_id"],
                    caller=row["caller"],
                    task=row["task"],
                    status="interrupted",
                    started_at=row["started_at"],
                    finished_at=finished_at,
                    source_ref=stable_source_ref(str(request.get("source") or "")),
                    output_dir=str(request.get("output_dir") or ""),
                    error={"error_code": "TASK_INTERRUPTED", "message": "task process ended without a final result"},
                    retryable=True,
                )
                result.counts["failed"] = 1
                payload = result.as_dict()
                connection.execute(
                    """
                    UPDATE runs SET finished_at=?, status='interrupted', result_json=?, outputs_json='[]',
                      counts_json=?, error_code='TASK_INTERRUPTED', retryable=1 WHERE run_id=?
                    """,
                    (finished_at, json.dumps(payload, ensure_ascii=False), json.dumps(payload["counts"]), row["run_id"]),
                )

    def start(
        self,
        run_id: str,
        caller: str,
        task: str,
        request: Any,
        started_at: str,
        profile_id: str = "",
        retry_of: str | None = None,
    ) -> None:
        clean = sanitize_mapping(request)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO runs
                (run_id, owner_pid, caller, task, profile_id, request_json, started_at, status, retry_of,
                 worker_version, schema_version, rules_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    os.getpid(),
                    caller,
                    task,
                    profile_id,
                    json.dumps(clean, ensure_ascii=False),
                    started_at,
                    retry_of,
                    WORKER_VERSION,
                    RESULT_SCHEMA_VERSION,
                    RULES_VERSION,
                ),
            )

    def mark_running(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))

    def progress(self, result: TaskResult) -> None:
        payload = result.as_dict()
        payload["status"] = "running"
        payload["finished_at"] = None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs SET status='running', result_json=?, outputs_json=?, counts_json=? WHERE run_id=?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload.get("outputs", []), ensure_ascii=False),
                    json.dumps(payload.get("counts", {}), ensure_ascii=False),
                    result.run_id,
                ),
            )

    def finish(self, result: TaskResult) -> None:
        payload = result.finish().as_dict()
        error_code = str((payload.get("error") or {}).get("error_code") or "")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs SET finished_at=?, status=?, result_json=?, outputs_json=?, counts_json=?,
                  error_code=?, retryable=? WHERE run_id=?
                """,
                (
                    result.finished_at,
                    result.status,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload.get("outputs", []), ensure_ascii=False),
                    json.dumps(payload.get("counts", {}), ensure_ascii=False),
                    error_code or None,
                    1 if result.retryable else 0,
                    result.run_id,
                ),
            )

    def list(self, limit: int = 20, run_id: str = "") -> list[dict[str, Any]]:
        limit = min(100, max(1, int(limit)))
        query = "SELECT * FROM runs"
        parameters: list[Any] = []
        if run_id:
            query += " WHERE run_id=?"
            parameters.append(run_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("request_json", "result_json", "outputs_json", "counts_json"):
                try:
                    item[key.removesuffix("_json")] = json.loads(item.pop(key) or "null")
                except ValueError:
                    item[key.removesuffix("_json")] = None
                    item.pop(key, None)
            item["retryable"] = bool(item.get("retryable"))
            items.append(sanitize_mapping(item))
        return items


@contextlib.contextmanager
def audited_task(
    result: TaskResult,
    request: Any,
    profile_id: str = "",
    retry_of: str | None = None,
    lock_timeout_seconds: int = 0,
    use_lock: bool = True,
) -> Iterator[TaskResult]:
    history = HistoryStore()
    history.start(result.run_id, result.caller, result.task, request, result.started_at, profile_id, retry_of)
    lock = GlobalTaskLock(result.task, result.caller, result.run_id, lock_timeout_seconds) if use_lock else None
    heartbeat_stop = threading.Event()
    heartbeat: threading.Thread | None = None
    try:
        if lock:
            lock.acquire()
        history.mark_running(result.run_id)
        history.progress(result)
        heartbeat = threading.Thread(
            target=_history_heartbeat,
            args=(history, result, heartbeat_stop),
            name=f"local-note-history-{result.run_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        yield result
    except BaseException as exc:
        code, retryable = classify_error(exc)
        result.status = "cancelled" if code == "TASK_CANCELLED" else "timeout" if code == "TASK_TIMEOUT" else "failed"
        result.counts["failed"] = max(1, result.counts.get("failed", 0))
        result.error = {"error_code": code, "message": redact_text(str(exc))}
        result.retryable = retryable
        raise
    finally:
        heartbeat_stop.set()
        if heartbeat:
            heartbeat.join(timeout=2)
        if lock:
            lock.release()
        history.finish(result)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _history_heartbeat(history: HistoryStore, result: TaskResult, stop: threading.Event) -> None:
    while not stop.wait(1.0):
        with contextlib.suppress(sqlite3.Error, OSError):
            history.progress(result)
