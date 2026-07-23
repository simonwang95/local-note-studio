#!/usr/bin/env python3
"""Dependency-free stdio MCP adapter for Local Note Studio automation."""

from __future__ import annotations

import json
import signal
import sys
from typing import Any

from automation_core import redact_text
from local_notes_agent import run_agent_action, terminate_active_worker
from local_notes_retrieval import run_read_action


SERVER_VERSION = "1.2.0"
PROTOCOL_VERSION = "2025-11-25"


def tool_definitions() -> list[dict[str, Any]]:
    profile_property = {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$", "description": "Enabled automation profile ID."}
    destination_property = {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_-]{0,63}$",
        "description": "Optional named output destination configured by the selected profile; never a filesystem path.",
    }
    limit_property = {"type": "integer", "minimum": 0, "description": "Safe item limit; 0 processes all incomplete items within the profile cap."}
    read_limit_property = {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
    offset_property = {"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}
    max_chars_property = {"type": "integer", "minimum": 1, "maximum": 50000, "default": 12000}
    date_range_property = {
        "type": "object",
        "properties": {
            "start": {"type": "string", "format": "date", "description": "Inclusive start date."},
            "end": {"type": "string", "format": "date", "description": "Inclusive end date."},
        },
        "additionalProperties": False,
    }
    return [
        {
            "name": "local_notes_env_check",
            "description": "Check the fixed runtime and non-secret environment for an automation profile.",
            "inputSchema": {"type": "object", "properties": {"profile": profile_property}, "required": ["profile"], "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "local_notes_sync_up",
            "description": "Incrementally organize allowed video and opus content for the UP configured by a profile. Writes notes, manifests, and audit history; it never overwrites complete notes unless the protected profile explicitly allows it.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": profile_property, "limit": limit_property, "dry_run": {"type": "boolean", "default": False}},
                "required": ["profile"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "local_notes_ingest_url",
            "description": "Organize one URL whose domain is allowed by the selected profile. Writes notes, manifests, and audit history.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": profile_property,
                    "url": {"type": "string", "minLength": 1, "description": "HTTP(S) URL without embedded credentials."},
                    "destination": destination_property,
                    "dry_run": {"type": "boolean", "default": False},
                },
                "required": ["profile", "url"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "local_notes_ingest_file",
            "description": "Organize one regular local file inside an input root allowed by the selected profile; directories are rejected. Writes notes, manifests, and audit history.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": profile_property,
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Absolute path to one regular file inside an allowed input root; directories are rejected.",
                    },
                    "destination": destination_property,
                    "dry_run": {"type": "boolean", "default": False},
                },
                "required": ["profile", "path"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "local_notes_get_status",
            "description": "Read the current global lock and persistent automation task history.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "Optional exact run ID."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "local_notes_retry_failed",
            "description": "Retry only failed UP-sync items for the selected profile. Writes notes, manifests, and audit history.",
            "inputSchema": {
                "type": "object",
                "properties": {"profile": profile_property, "limit": limit_property, "dry_run": {"type": "boolean", "default": False}},
                "required": ["profile"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
        },
        {
            "name": "local_notes_list_profiles",
            "description": "List enabled non-secret automation profiles.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "local_notes_search",
            "description": "Deterministically search indexed Markdown notes from all enabled Profiles. Read-only: no network, model, database, shell, history, lock, note, Manifest, or index writes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "author": {"type": "string", "maxLength": 100},
                    "date_range": date_range_property,
                    "limit": read_limit_property,
                    "offset": offset_property,
                    "max_chars": {**max_chars_property, "minimum": 5000, "default": 20000},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "local_notes_get",
            "description": "Read metadata, section provenance, and a bounded page of one indexed Markdown note by note_id or indexed path.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path_or_id": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "offset": offset_property,
                    "max_chars": max_chars_property,
                    "section": {"type": "string", "maxLength": 300},
                },
                "required": ["path_or_id"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "local_notes_list_recent",
            "description": "List recently published indexed Markdown notes from enabled Profiles using trusted publication timestamps only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "author": {"type": "string", "maxLength": 100},
                    "content_type": {"type": "string", "maxLength": 100},
                    "days": {"type": "integer", "minimum": 0, "maximum": 36500, "default": 30},
                    "limit": read_limit_property,
                    "offset": offset_property,
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
        {
            "name": "local_notes_get_viewpoints",
            "description": "Retrieve and deterministically group direct evidence as of a required historical date. It does not summarize viewpoints or generate investment advice.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol_or_topic": {"type": "string", "minLength": 1, "maxLength": 200},
                    "as_of_date": {"type": "string", "format": "date"},
                    "limit": read_limit_property,
                },
                "required": ["symbol_or_topic", "as_of_date"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
        },
    ]


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    read_mapping = {
        "local_notes_search": "search",
        "local_notes_get": "get",
        "local_notes_list_recent": "list-recent",
        "local_notes_get_viewpoints": "get-viewpoints",
    }
    if name in read_mapping:
        validate_tool_arguments(name, arguments)
        result = run_read_action(read_mapping[name], arguments, caller="mcp")
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": result.get("status") != "completed",
        }
    mapping = {
        "local_notes_env_check": ("env-check", ""),
        "local_notes_sync_up": ("sync-up", ""),
        "local_notes_ingest_url": ("ingest-url", "url"),
        "local_notes_ingest_file": ("ingest-file", "path"),
        "local_notes_get_status": ("status", ""),
        "local_notes_retry_failed": ("retry-failed", ""),
        "local_notes_list_profiles": ("profiles", ""),
    }
    if name not in mapping:
        raise ValueError(f"unknown tool: {name}")
    validate_tool_arguments(name, arguments)
    action, source_key = mapping[name]
    result = run_agent_action(
        action,
        profile_id=str(arguments.get("profile") or ""),
        source=str(arguments.get(source_key) or "") if source_key else "",
        destination=str(arguments.get("destination") or ""),
        limit=arguments.get("limit") if action in {"sync-up", "retry-failed"} else None,
        dry_run=bool(arguments.get("dry_run", False)),
        caller="mcp",
        run_id=str(arguments.get("run_id") or ""),
        status_limit=int(arguments.get("limit") or 20) if action == "status" else 20,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
        "isError": result.get("status") not in {"completed", "no_changes", "partial_failed"},
    }


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> None:
    rules = {
        "local_notes_env_check": ({"profile"}, {"profile"}),
        "local_notes_sync_up": ({"profile", "limit", "dry_run"}, {"profile"}),
        "local_notes_ingest_url": ({"profile", "url", "destination", "dry_run"}, {"profile", "url"}),
        "local_notes_ingest_file": ({"profile", "path", "destination", "dry_run"}, {"profile", "path"}),
        "local_notes_get_status": ({"run_id", "limit"}, set()),
        "local_notes_retry_failed": ({"profile", "limit", "dry_run"}, {"profile"}),
        "local_notes_list_profiles": (set(), set()),
        "local_notes_search": ({"query", "author", "date_range", "limit", "offset", "max_chars"}, {"query"}),
        "local_notes_get": ({"path_or_id", "offset", "max_chars", "section"}, {"path_or_id"}),
        "local_notes_list_recent": ({"author", "content_type", "days", "limit", "offset"}, set()),
        "local_notes_get_viewpoints": ({"symbol_or_topic", "as_of_date", "limit"}, {"symbol_or_topic", "as_of_date"}),
    }
    allowed, required = rules[name]
    unknown = set(arguments) - allowed
    missing = required - set(arguments)
    if unknown:
        raise ValueError(f"unexpected tool arguments: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing tool arguments: {', '.join(sorted(missing))}")
    for key in (
        "profile",
        "url",
        "path",
        "destination",
        "run_id",
        "query",
        "author",
        "path_or_id",
        "section",
        "content_type",
        "symbol_or_topic",
        "as_of_date",
    ):
        if key in arguments and not isinstance(arguments[key], str):
            raise ValueError(f"{key} must be a string")
    if "destination" in arguments:
        value = arguments["destination"]
        if not value or len(value) > 64 or not value[0].islower() or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in value
        ):
            raise ValueError("destination must be a configured lowercase identifier")
    for key in ("limit", "offset", "max_chars", "days"):
        if key in arguments and (isinstance(arguments[key], bool) or not isinstance(arguments[key], int)):
            raise ValueError(f"{key} must be an integer")
    if "limit" in arguments:
        read_tools = {"local_notes_search", "local_notes_list_recent", "local_notes_get_viewpoints"}
        minimum = 1 if name == "local_notes_get_status" or name in read_tools else 0
        maximum = 100 if name == "local_notes_get_status" else 50 if name in read_tools else None
        if arguments["limit"] < minimum or (maximum is not None and arguments["limit"] > maximum):
            suffix = f" and at most {maximum}" if maximum is not None else ""
            raise ValueError(f"limit must be at least {minimum}{suffix}")
    if "dry_run" in arguments and not isinstance(arguments["dry_run"], bool):
        raise ValueError("dry_run must be a boolean")
    if "offset" in arguments and not 0 <= arguments["offset"] <= 10000000:
        raise ValueError("offset must be between 0 and 10000000")
    if "max_chars" in arguments:
        minimum_chars = 5000 if name == "local_notes_search" else 1
        if not minimum_chars <= arguments["max_chars"] <= 50000:
            raise ValueError(f"max_chars must be between {minimum_chars} and 50000")
    if "days" in arguments and not 0 <= arguments["days"] <= 36500:
        raise ValueError("days must be between 0 and 36500")
    if "date_range" in arguments:
        value = arguments["date_range"]
        if not isinstance(value, dict) or set(value) - {"start", "end"}:
            raise ValueError("date_range must be an object containing only start and end")
        if any(not isinstance(item, str) for item in value.values()):
            raise ValueError("date_range values must be strings")


def response_for(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    try:
        if method == "initialize":
            result: Any = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "local-note-studio", "version": SERVER_VERSION},
                "instructions": "Use named profiles only. Write tools are incremental and audited; secrets are loaded locally and are never tool arguments.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions()}
        elif method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            result = call_tool(str(params.get("name") or ""), arguments)
        else:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except (TypeError, ValueError) as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": redact_text(str(exc))}}
    except BaseException as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": redact_text(str(exc))}}


def _shutdown(_signum: int, _frame: Any) -> None:
    terminate_active_worker()
    raise SystemExit(0)


def main() -> int:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = response_for(message)
        except (ValueError, json.JSONDecodeError) as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    terminate_active_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
