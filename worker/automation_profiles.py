#!/usr/bin/env python3
"""Validated, non-secret automation profiles for Local Note Studio."""

from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from automation_core import AutomationError, PROFILE_SCHEMA_VERSION, app_data_root, sanitize_mapping


PROFILE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ALLOWED_CONTENT_TYPES = {"opus", "video"}
ALLOWED_SUBTITLE_STRATEGIES = {"yt-dlp", "web", "asr"}
ALLOWED_RUNTIME_BACKENDS = {"managed", "conda", "python"}
ALLOWED_OPUS_IMAGE_ANALYSIS_MODES = {"off", "ocr", "vision"}
ALLOWED_PROFILE_KEYS = {
    "id",
    "enabled",
    "up_mid",
    "content_types",
    "output_dir",
    "output_destinations",
    "allowed_output_roots",
    "allowed_input_roots",
    "allowed_domains",
    "stock_terms",
    "overwrite_outputs",
    "cooldown_delay",
    "limit",
    "max_limit",
    "subtitle_strategy",
    "lock_timeout_seconds",
    "execution_timeout_seconds",
    "runtime_backend",
    "python_bin",
    "conda_env",
    "conda_bin",
    "model",
    "asr_model",
    "extract_keyframes",
    "dialogue_detection",
    "keep_original_subtitles",
    "timeout_seconds",
    "retry_count",
    "chunk_chars",
    "opus_image_analysis",
}


def default_profiles_path() -> pathlib.Path:
    configured = os.environ.get("LOCAL_NOTE_STUDIO_PROFILES_FILE", "").strip()
    return pathlib.Path(configured).expanduser() if configured else app_data_root() / "config" / "automation-profiles.json"


def _absolute_path(raw: Any, label: str, must_exist: bool = False) -> pathlib.Path:
    value = str(raw or "").strip()
    if not value:
        raise AutomationError(f"{label} is required", "PROFILE_INVALID")
    expanded = pathlib.Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute():
        raise AutomationError(f"{label} must be an absolute path", "PROFILE_INVALID")
    resolved = expanded.resolve(strict=False)
    if must_exist and not resolved.exists():
        raise AutomationError(f"{label} does not exist: {resolved}", "SOURCE_NOT_FOUND")
    return resolved


def path_is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_allowed_path(raw: str, roots: tuple[pathlib.Path, ...], label: str, must_exist: bool = False) -> pathlib.Path:
    path = _absolute_path(raw, label, must_exist=must_exist)
    if not roots or not any(path_is_within(path, root) for root in roots):
        raise AutomationError(f"{label} is outside the profile allowlist", "PATH_NOT_ALLOWED")
    return path


def validate_allowed_url(raw: str, domains: tuple[str, ...]) -> str:
    value = str(raw or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AutomationError("URL must use http or https", "URL_NOT_ALLOWED")
    host = parsed.hostname.rstrip(".").lower()
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        raise AutomationError(f"URL domain is not allowed: {host}", "URL_NOT_ALLOWED")
    if parsed.username or parsed.password:
        raise AutomationError("credentials in URLs are not allowed", "URL_NOT_ALLOWED")
    sensitive_query_keys = {"token", "access_token", "api_key", "apikey", "authorization", "password", "secret"}
    if any(key.lower() in sensitive_query_keys for key, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)):
        raise AutomationError("secret-bearing URL query parameters are not allowed", "URL_NOT_ALLOWED")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _safe_executable(raw: Any, label: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if any(char.isspace() for char in value) or value.startswith("-"):
        raise AutomationError(f"{label} must be one executable path without arguments", "PROFILE_INVALID")
    name = pathlib.Path(value).name
    allowed = {"python", "python3", "conda"}
    if name not in allowed and not re.fullmatch(r"python3\.\d+", name):
        raise AutomationError(f"{label} executable is not allowed", "PROFILE_INVALID")
    if "/" in value and not pathlib.Path(value).expanduser().is_absolute():
        raise AutomationError(f"{label} must be a command name or absolute path", "PROFILE_INVALID")
    return str(pathlib.Path(value).expanduser()) if "/" in value else value


def _bounded_int(value: Any, label: str, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int):
        raise AutomationError(f"{label} must be an integer", "PROFILE_INVALID")
    parsed = value
    if parsed < minimum or parsed > maximum:
        raise AutomationError(f"{label} must be between {minimum} and {maximum}", "PROFILE_INVALID")
    return parsed


def _strict_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise AutomationError(f"{key} must be a boolean", "PROFILE_INVALID")
    return value


@dataclass(frozen=True)
class AutomationProfile:
    id: str
    enabled: bool
    up_mid: str
    content_types: tuple[str, ...]
    output_dir: pathlib.Path
    output_destinations: dict[str, pathlib.Path]
    allowed_output_roots: tuple[pathlib.Path, ...]
    allowed_input_roots: tuple[pathlib.Path, ...]
    allowed_domains: tuple[str, ...]
    stock_terms: bool
    overwrite_outputs: bool
    cooldown_delay: int
    limit: int
    max_limit: int
    subtitle_strategy: str
    lock_timeout_seconds: int
    execution_timeout_seconds: int
    runtime_backend: str
    python_bin: str
    conda_env: str
    conda_bin: str
    model: str
    asr_model: str
    extract_keyframes: bool
    dialogue_detection: bool
    keep_original_subtitles: bool
    timeout_seconds: int
    retry_count: int
    chunk_chars: int
    opus_image_analysis: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AutomationProfile":
        unknown = set(data) - ALLOWED_PROFILE_KEYS
        if unknown:
            raise AutomationError(f"profile has unsupported fields: {', '.join(sorted(unknown))}", "PROFILE_INVALID")
        profile_id = str(data.get("id") or "").strip()
        if not PROFILE_ID_PATTERN.fullmatch(profile_id):
            raise AutomationError("profile id must match [a-z][a-z0-9_-]*", "PROFILE_INVALID")
        up_mid = str(data.get("up_mid") or "").strip()
        if up_mid and not up_mid.isdigit():
            raise AutomationError("up_mid must contain digits only", "PROFILE_INVALID")
        if not isinstance(data.get("content_types"), list):
            raise AutomationError("content_types must be a list", "PROFILE_INVALID")
        content_types = tuple(dict.fromkeys(str(item).strip() for item in data.get("content_types", [])))
        if not content_types or any(item not in ALLOWED_CONTENT_TYPES for item in content_types):
            raise AutomationError("content_types must contain opus and/or video", "PROFILE_INVALID")
        output_dir = _absolute_path(data.get("output_dir"), "output_dir")
        raw_output_roots = data.get("allowed_output_roots") or [str(output_dir)]
        if not isinstance(raw_output_roots, list):
            raise AutomationError("allowed_output_roots must be a list", "PROFILE_INVALID")
        output_roots = tuple(_absolute_path(item, "allowed_output_roots item") for item in raw_output_roots)
        if not any(path_is_within(output_dir, root) for root in output_roots):
            raise AutomationError("output_dir is outside allowed_output_roots", "PATH_NOT_ALLOWED")
        raw_destinations = data.get("output_destinations", {})
        if not isinstance(raw_destinations, dict):
            raise AutomationError("output_destinations must be an object", "PROFILE_INVALID")
        output_destinations: dict[str, pathlib.Path] = {}
        for destination_id, raw_path in raw_destinations.items():
            if not isinstance(destination_id, str) or not PROFILE_ID_PATTERN.fullmatch(destination_id):
                raise AutomationError("output destination id must match [a-z][a-z0-9_-]*", "PROFILE_INVALID")
            if not isinstance(raw_path, str):
                raise AutomationError(
                    f"output destination {destination_id} must be a string",
                    "PROFILE_INVALID",
                )
            raw_relative = raw_path.strip()
            if "\x00" in raw_relative:
                raise AutomationError(f"output destination {destination_id} contains an invalid character", "PROFILE_INVALID")
            raw_parts = raw_relative.split("/")
            if not raw_relative or any(part in {"", ".", ".."} for part in raw_parts):
                raise AutomationError(
                    f"output destination {destination_id} must be a non-empty relative path without dot segments",
                    "PROFILE_INVALID",
                )
            relative = pathlib.Path(raw_relative)
            if relative.is_absolute():
                raise AutomationError(
                    f"output destination {destination_id} must be a non-empty relative path without dot segments",
                    "PROFILE_INVALID",
                )
            destination_path = (output_dir / relative).resolve(strict=False)
            if not path_is_within(destination_path, output_dir) or not any(
                path_is_within(destination_path, root) for root in output_roots
            ):
                raise AutomationError(f"output destination {destination_id} is outside the profile allowlist", "PATH_NOT_ALLOWED")
            output_destinations[destination_id] = destination_path
        if not isinstance(data.get("allowed_input_roots", []), list):
            raise AutomationError("allowed_input_roots must be a list", "PROFILE_INVALID")
        input_roots = tuple(_absolute_path(item, "allowed_input_roots item") for item in data.get("allowed_input_roots", []))
        if not isinstance(data.get("allowed_domains", ["bilibili.com"]), list):
            raise AutomationError("allowed_domains must be a list", "PROFILE_INVALID")
        domains = tuple(
            dict.fromkeys(str(item).strip().lower().rstrip(".") for item in data.get("allowed_domains", ["bilibili.com"]) if str(item).strip())
        )
        if not domains or any("/" in item or ":" in item for item in domains):
            raise AutomationError("allowed_domains must contain host names only", "PROFILE_INVALID")
        max_limit = _bounded_int(data.get("max_limit", 500), "max_limit", 1, 5000, 500)
        limit = _bounded_int(data.get("limit", 20), "limit", 0, max_limit, 20)
        strategy = str(data.get("subtitle_strategy") or "yt-dlp").strip()
        if strategy not in ALLOWED_SUBTITLE_STRATEGIES:
            raise AutomationError("subtitle_strategy is invalid", "PROFILE_INVALID")
        backend = str(data.get("runtime_backend") or "managed").strip()
        if backend not in ALLOWED_RUNTIME_BACKENDS:
            raise AutomationError("runtime_backend is invalid", "PROFILE_INVALID")
        opus_image_analysis = str(data.get("opus_image_analysis") or "off").strip().lower()
        if opus_image_analysis not in ALLOWED_OPUS_IMAGE_ANALYSIS_MODES:
            raise AutomationError("opus_image_analysis must be off, ocr, or vision", "PROFILE_INVALID")
        return cls(
            id=profile_id,
            enabled=_strict_bool(data, "enabled", False),
            up_mid=up_mid,
            content_types=content_types,
            output_dir=output_dir,
            output_destinations=output_destinations,
            allowed_output_roots=output_roots,
            allowed_input_roots=input_roots,
            allowed_domains=domains,
            stock_terms=_strict_bool(data, "stock_terms", True),
            overwrite_outputs=_strict_bool(data, "overwrite_outputs", False),
            cooldown_delay=_bounded_int(data.get("cooldown_delay", 60), "cooldown_delay", 0, 3600, 60),
            limit=limit,
            max_limit=max_limit,
            subtitle_strategy=strategy,
            lock_timeout_seconds=_bounded_int(data.get("lock_timeout_seconds", 0), "lock_timeout_seconds", 0, 86400, 0),
            execution_timeout_seconds=_bounded_int(
                data.get("execution_timeout_seconds", 21600), "execution_timeout_seconds", 0, 172800, 21600
            ),
            runtime_backend=backend,
            python_bin=_safe_executable(data.get("python_bin", ""), "python_bin"),
            conda_env=str(data.get("conda_env") or "").strip(),
            conda_bin=_safe_executable(data.get("conda_bin", ""), "conda_bin"),
            model=str(data.get("model") or "").strip(),
            asr_model=str(_absolute_path(data.get("asr_model"), "asr_model")) if data.get("asr_model") else "",
            extract_keyframes=_strict_bool(data, "extract_keyframes", False),
            dialogue_detection=_strict_bool(data, "dialogue_detection", False),
            keep_original_subtitles=_strict_bool(data, "keep_original_subtitles", False),
            timeout_seconds=_bounded_int(data.get("timeout_seconds", 0), "timeout_seconds", 0, 86400, 0),
            retry_count=_bounded_int(data.get("retry_count", 0), "retry_count", 0, 20, 0),
            chunk_chars=_bounded_int(data.get("chunk_chars", 0), "chunk_chars", 0, 1000000, 0),
            opus_image_analysis=opus_image_analysis,
        )

    def public_dict(self) -> dict[str, Any]:
        return sanitize_mapping(
            {
                "id": self.id,
                "enabled": self.enabled,
                "up_mid": self.up_mid,
                "content_types": list(self.content_types),
                "output_dir": str(self.output_dir),
                "output_destinations": {
                    destination_id: path.relative_to(self.output_dir).as_posix()
                    for destination_id, path in self.output_destinations.items()
                },
                "allowed_domains": list(self.allowed_domains),
                "stock_terms": self.stock_terms,
                "overwrite_outputs": self.overwrite_outputs,
                "cooldown_delay": self.cooldown_delay,
                "limit": self.limit,
                "subtitle_strategy": self.subtitle_strategy,
                "runtime_backend": self.runtime_backend,
                "opus_image_analysis": self.opus_image_analysis,
            }
        )


def load_profiles(path: pathlib.Path | None = None, include_disabled: bool = False) -> dict[str, AutomationProfile]:
    profile_path = path or default_profiles_path()
    if not profile_path.exists():
        return {}
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AutomationError(f"cannot read profiles: {exc}", "PROFILE_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise AutomationError(f"profiles schema_version must be {PROFILE_SCHEMA_VERSION}", "PROFILE_INVALID")
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise AutomationError("profiles must be a list", "PROFILE_INVALID")
    result: dict[str, AutomationProfile] = {}
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            raise AutomationError("each profile must be an object", "PROFILE_INVALID")
        profile = AutomationProfile.from_mapping(raw)
        if profile.id in result:
            raise AutomationError(f"duplicate profile id: {profile.id}", "PROFILE_INVALID")
        if include_disabled or profile.enabled:
            result[profile.id] = profile
    return result


def require_profile(profile_id: str) -> AutomationProfile:
    profiles = load_profiles()
    try:
        return profiles[profile_id]
    except KeyError as exc:
        raise AutomationError(f"enabled profile not found: {profile_id}", "PROFILE_NOT_FOUND") from exc


def resolve_output_destination(profile: AutomationProfile, destination_id: str = "") -> pathlib.Path:
    value = str(destination_id or "").strip()
    if not value:
        return profile.output_dir
    if not PROFILE_ID_PATTERN.fullmatch(value):
        raise AutomationError("output destination id is invalid", "PATH_NOT_ALLOWED")
    try:
        destination = profile.output_destinations[value].resolve(strict=False)
    except KeyError as exc:
        raise AutomationError(f"output destination is not allowed: {value}", "PATH_NOT_ALLOWED") from exc
    if not path_is_within(destination, profile.output_dir) or not any(
        path_is_within(destination, root) for root in profile.allowed_output_roots
    ):
        raise AutomationError(f"output destination is outside the profile allowlist: {value}", "PATH_NOT_ALLOWED")
    return destination
