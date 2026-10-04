"""Content-free task telemetry shared by the Worker and its subprocesses.

Every event has its own private, atomically published file. A process lock guards
the derived run summary, so concurrent subprocesses cannot lose each other's
measurements. Missing provider usage is unknown; it is never estimated.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
import uuid


_ENV = ContextVar("task_diagnostics_env", default=None)


def _env():
    configured = _ENV.get()
    return configured if configured is not None else os.environ


@contextmanager
def _using_env(env):
    token = _ENV.set(env) if env is not None else None
    try:
        yield
    finally:
        if token is not None:
            _ENV.reset(token)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def incognito():
    return _env().get("LOCAL_NOTE_STUDIO_INCOGNITO", "").strip().lower() in {"1", "true", "yes", "on"}


def clean_source_ref(value):
    value = str(value or "").strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme in {"http", "https"}:
        # Query strings, fragments and URL credentials are never diagnostics.
        host = parts.hostname or ""
        try:
            if parts.port:
                host += f":{parts.port}"
        except ValueError:
            pass
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    return value


def source_identity(value):
    clean = clean_source_ref(value)
    return hashlib.sha256(clean.encode("utf-8")).hexdigest() if clean else None


def _clean_config(value):
    if isinstance(value, dict):
        return {str(key): _clean_config(item) for key, item in value.items()
                if not re.search(r"api.?key|cookie|authorization|password|secret|credential|prompt|transcript|content", str(key), re.I)}
    if isinstance(value, (list, tuple)):
        return [_clean_config(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "<redacted>", value)
    return value if isinstance(value, (int, float, bool)) or value is None else None


def _root():
    state = Path(_env().get("LOCAL_NOTE_STUDIO_STATE_DIR") or
                 Path.home() / "Library/Application Support/Local Note Studio/state")
    return state / "diagnostics"


def _run_id(value=None):
    candidate = str(value or _env().get("LOCAL_NOTE_STUDIO_RUN_ID") or "")
    # Run identifiers are filenames, never arbitrary filesystem paths.
    return candidate if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", candidate) else ""


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".diagnostic-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def emit_event(event_type, run_id=None, **data):
    """Best effort: telemetry failure must not change task business results."""
    ident = _run_id(run_id)
    if not ident or incognito():
        return None
    event = {"type": event_type, "timestamp": utc_now(), "id": uuid.uuid4().hex, **data}
    try:
        _atomic_json(_root() / "events" / ident / f"{event['id']}.json", event)
        summarize_run(ident)
        return event["id"]
    except (OSError, ValueError, TypeError):
        return None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def summarize_run(run_id=None, env=None):
    with _using_env(env):
        return _summarize_run(run_id)


def _summarize_run(run_id=None):
    ident = _run_id(run_id)
    if not ident or incognito():
        return None
    root = _root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(root / f".{ident}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(lock_fd, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            events = []
            for path in (root / "events" / ident).glob("*.json"):
                try:
                    event = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(event, dict):
                        events.append(event)
                except (OSError, ValueError):
                    continue
            events.sort(key=lambda event: (event.get("timestamp", ""), event.get("id", "")))
            context = {}
            for event in sorted(events, key=lambda row: row.get("type") == "run_finish"):
                if event.get("type") in {"run_start", "run_finish"}:
                    context.update({key: value for key, value in event.items()
                                    if key in {"task", "source_ref", "source_identity", "output_dir", "status", "effective_config"}})
            calls = [event for event in events if event.get("type") == "model_start"]
            completed = {event.get("call_id"): event for event in events if event.get("type") == "model_finish"}
            monitored = any(event.get("type") in {"model_start", "stage_start", "cache_hit"} for event in events)
            token_fields = ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")
            metrics = {key: None for key in token_fields}
            usage_complete = bool(calls)
            llm_calls = [event for event in calls if event.get("provider") != "local_asr"]
            # Tokens are a provider metric. Local ASR without provider usage is
            # unknown and does not dilute token completeness for LLM requests.
            for key in token_fields:
                values = [_number((completed.get(call["id"], {}).get("usage") or {}).get(key)) for call in llm_calls]
                metrics[key] = sum(values) if values and all(value is not None for value in values) else None
            usage_complete = bool(llm_calls) and all(
                _number((completed.get(call["id"], {}).get("usage") or {}).get("prompt_tokens")) is not None
                and _number((completed.get(call["id"], {}).get("usage") or {}).get("completion_tokens")) is not None
                for call in llm_calls)
            retries = {}
            for event in events:
                if event.get("type") == "retry":
                    key = (event.get("stage"), event.get("reason"))
                    retries[key] = retries.get(key, 0) + 1
            stage_rows = {}
            stage_starts = {event["id"]: event for event in events if event.get("type") == "stage_start"}
            stage_finishes = {event.get("stage_id"): event for event in events if event.get("type") == "stage_finish"}
            for event in events:
                stage = event.get("stage")
                if not stage:
                    continue
                row = stage_rows.setdefault(stage, {"stage": stage, "status": "unknown", "duration_seconds": None, "calls": 0, "cache_hits": 0})
                if event.get("type") == "model_start":
                    row["calls"] += 1
                elif event.get("type") == "cache_hit":
                    row["cache_hits"] += 1
            for stage, row in stage_rows.items():
                starts_for_stage = [(ident, start) for ident, start in stage_starts.items() if start["stage"] == stage]
                finishes_for_stage = [stage_finishes.get(ident) for ident, _start in starts_for_stage]
                if not starts_for_stage:
                    continue
                if any(finish and finish.get("status") == "failed" for finish in finishes_for_stage):
                    row["status"] = "failed"
                elif any(finish is None for finish in finishes_for_stage):
                    row["status"] = "interrupted" if context.get("status") not in {None, "running", "unknown"} else "running"
                else:
                    row["status"] = finishes_for_stage[-1].get("status", "unknown")
                durations = [_number(finish.get("duration_seconds")) if finish else None for finish in finishes_for_stage]
                row["duration_seconds"] = sum(durations) if all(duration is not None for duration in durations) else None
            metrics.update({
                "model_calls": len(calls) if monitored else None,
                "asr_calls": sum(call.get("provider") == "local_asr" for call in calls) if monitored else None,
                "llm_calls": len(llm_calls) if monitored else None,
                "usage_complete": usage_complete,
                "cache_hits": sum(event.get("type") == "cache_hit" for event in events) if monitored else None,
                "retries": [{"stage": stage, "reason": reason, "count": count} for (stage, reason), count in sorted(retries.items())] if monitored else None,
                "stages": sorted(stage_rows.values(), key=lambda row: row["stage"]) if monitored else None,
            })
            starts = [event for event in events if event.get("type") == "run_start"]
            finishes = [event for event in events if event.get("type") == "run_finish"]
            metrics["duration_seconds"] = None
            if starts and finishes:
                try:
                    metrics["duration_seconds"] = max(0, (datetime.fromisoformat(finishes[-1]["timestamp"]) -
                                                          datetime.fromisoformat(starts[0]["timestamp"])).total_seconds())
                except (ValueError, TypeError):
                    pass
            summary = {"schema_version": 1, "run_id": ident, "task": None, "source_ref": None,
                       "source_identity": None, "output_dir": None, "effective_config": None,
                       "created_at": events[0]["timestamp"] if events else None,
                       "updated_at": events[-1]["timestamp"] if events else None,
                       "status": "unknown", **context, "metrics": metrics}
            _atomic_json(root / f"{ident}.json", summary)
            return summary
    finally:
        # Closing the handle releases flock even when summary serialization fails.
        pass


def _context(task=None, source_ref=None, output_dir=None, effective_config=None):
    source = clean_source_ref(source_ref or _env().get("LOCAL_NOTE_STUDIO_SOURCE_REF"))
    values = {"task": task or _env().get("LOCAL_NOTE_STUDIO_TASK") or None,
              "source_ref": source or None, "source_identity": source_identity(source),
              "output_dir": output_dir or _env().get("LOCAL_NOTE_STUDIO_OUTPUT_DIR") or None}
    if effective_config is not None:
        values["effective_config"] = _clean_config(effective_config)
    return values


def start_run(run_id=None, task=None, source_ref=None, output_dir=None, effective_config=None, env=None):
    with _using_env(env):
        emit_event("run_start", run_id=run_id, status="running", **_context(task, source_ref, output_dir, effective_config))
        return summarize_run(run_id)


def finalize_run(status, run_id=None, task=None, source_ref=None, output_dir=None, effective_config=None, env=None):
    with _using_env(env):
        context = _context(task, source_ref, output_dir, effective_config)
        emit_event("run_finish", run_id=run_id, status=status, **{key: value for key, value in context.items() if value is not None})
        return summarize_run(run_id)


def begin_model_call(stage, model=None, provider="llm", config=None):
    return emit_event("model_start", stage=stage, model=_clean_config(model), provider=provider,
                      config=_clean_config(config))


def finish_model_call(call_id, stage, status, usage=None, reason=None):
    if not call_id:
        return
    usage = usage if isinstance(usage, dict) else {}
    clean_usage = {key: _number(usage.get(key)) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    details = usage.get("completion_tokens_details")
    clean_usage["reasoning_tokens"] = _number(details.get("reasoning_tokens")) if isinstance(details, dict) else _number(usage.get("reasoning_tokens"))
    emit_event("model_finish", call_id=call_id, stage=stage, status=status, usage=clean_usage, reason=reason)


def record_retry(stage, reason):
    emit_event("retry", stage=stage, reason=reason)


def record_cache_hit(stage, category):
    emit_event("cache_hit", stage=stage, category=category)


@contextmanager
def stage_span(stage):
    ident = emit_event("stage_start", stage=stage)
    started = time.monotonic()
    observation = {"status": "completed"}
    try:
        yield observation
    except BaseException:
        observation["status"] = "failed"
        raise
    finally:
        if ident:
            emit_event("stage_finish", stage_id=ident, stage=stage, status=observation["status"],
                       duration_seconds=max(0, time.monotonic() - started))


def diagnostic_stage(stage, status_of_result=None):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with stage_span(stage) as observation:
                result = function(*args, **kwargs)
                if status_of_result is not None:
                    observation["status"] = status_of_result(result)
                return result
        return measured
    return decorate
