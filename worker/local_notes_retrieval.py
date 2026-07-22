#!/usr/bin/env python3
"""Profile-scoped, deterministic, read-only Markdown note retrieval."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import tempfile
import unicodedata
import urllib.parse
from typing import Any, Iterable

from automation_core import (
    AutomationError,
    RESULT_SCHEMA_VERSION,
    new_run_id,
    redact_text,
    sanitize_mapping,
    utc_now,
)
from automation_profiles import AutomationProfile, load_profiles, path_is_within


INDEX_SCHEMA_VERSION = "2.0"
ASSET_INDEX_SCHEMA_VERSION = "2.0"
MAX_QUERY_CHARS = 200
MAX_AUTHOR_CHARS = 100
MAX_NOTE_FILE_BYTES = 5 * 1024 * 1024
MAX_SEARCH_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
DEFAULT_MAX_CHARS = 12000
MAX_RETURN_CHARS = 50000
MAX_OFFSET = 10_000_000
MAX_MATCHED_SECTIONS = 100
MAX_SECTION_CATALOG = 250
WORKER_ROOT = pathlib.Path(__file__).resolve().parent

TRUSTED_DATE_SOURCES = {"published", "frontmatter"}
SUPPORTED_SOURCE_TYPES = {
    "video",
    "bilibili-video",
    "bilibili",
    "bilibili-opus",
    "pptx",
    "presentation",
    "pdf",
    "webpage",
    "wechat-article",
    "local-file",
    "local-video",
    "unknown",
}
METHODOLOGY_MARKERS = (
    "方法",
    "方法论",
    "框架",
    "原则",
    "纪律",
    "筛选",
    "体系",
    "策略",
    "七轨布林",
    "复盘流程",
)


class NoteRetrievalError(AutomationError):
    """Stable retrieval failure whose message never exposes an untrusted path."""


def _state_dir_read_only() -> pathlib.Path:
    configured = os.environ.get("LOCAL_NOTE_STUDIO_STATE_DIR", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser().resolve()
    app_data = os.environ.get("LOCAL_NOTE_STUDIO_APP_DATA_DIR", "").strip()
    root = pathlib.Path(app_data).expanduser().resolve() if app_data else pathlib.Path.home() / "Library/Application Support/Local Note Studio"
    return root / "state"


def index_root() -> pathlib.Path:
    configured = os.environ.get("LOCAL_NOTES_READ_INDEX_DIR", "").strip() or os.environ.get("INDEX_DIR", "").strip()
    if configured:
        path = pathlib.Path(configured).expanduser()
        return path.resolve() if path.is_absolute() else (WORKER_ROOT / path).resolve()
    return _state_dir_read_only() / "indexes"


def profile_index_path(profile: AutomationProfile) -> pathlib.Path:
    return index_root() / "note-indexes" / profile.id / "note-index.json"


def profile_asset_index_path(profile: AutomationProfile) -> pathlib.Path:
    return profile_index_path(profile).with_name("asset-index.json")


def normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _sanitize_note_text(value: Any) -> str:
    text = redact_text(str(value or ""))
    text = re.sub(r"(?im)\b(?:cookie|set-cookie)\s*[:=]\s*[^\r\n]+", "cookie=<redacted>", text)
    return re.sub(r"(?i)data:[^\s)]+", "[embedded data omitted]", text)


def _clean_scalar(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = _sanitize_note_text(str(value).strip())
    if not text or text.casefold() in {"null", "none", "unknown", "未知", "无"}:
        return None
    return text[:4096]


def _clean_url(value: Any) -> str | None:
    text = _clean_scalar(value)
    if not text:
        return None
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))


def _yaml_value(raw: str) -> Any:
    value = raw.strip()
    if not value or value in {"null", "~"}:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [_yaml_value(item) for item in value[1:-1].split(",") if item.strip()]
    if value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    return value


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str, int]:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, normalized, 1
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), -1)
    if closing < 0:
        return {}, normalized, 1
    data: dict[str, Any] = {}
    current_key = ""
    for line in lines[1:closing]:
        if re.match(r"^\s+-\s+", line) and current_key:
            current = data.setdefault(current_key, [])
            if isinstance(current, list):
                current.append(_yaml_value(re.sub(r"^\s+-\s+", "", line)))
            continue
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        current_key = key.strip()
        if current_key:
            data[current_key] = _yaml_value(raw)
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    body_start = closing + 2
    while body_start <= len(lines) and not lines[body_start - 1].strip():
        body_start += 1
    return data, body, body_start


def _normalize_tags(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = _clean_scalar(item)
        if text and text not in result:
            result.append(text[:100])
    return result[:50]


def _normalize_iso(value: Any) -> str | None:
    text = _clean_scalar(value)
    if not text:
        return None
    if re.fullmatch(r"\d{10}(?:\.\d+)?", text):
        try:
            return dt.datetime.fromtimestamp(float(text), tz=dt.timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, ValueError):
            return None
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if compact:
        text = "-".join(compact.groups())
    chinese = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", text)
    if chinese:
        text = f"{int(chinese.group(1)):04d}-{int(chinese.group(2)):02d}-{int(chinese.group(3)):02d}"
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(candidate)
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        try:
            return dt.date.fromisoformat(candidate).isoformat()
        except ValueError:
            return None


def _published(meta: dict[str, Any], manifest: dict[str, Any]) -> tuple[str | None, str]:
    for source in (meta, manifest):
        normalized = _normalize_iso(source.get("published"))
        if normalized:
            return normalized, "published"
    for key in ("published_at", "publish_date", "date"):
        normalized = _normalize_iso(meta.get(key))
        if normalized:
            return normalized, "frontmatter"
    return None, "unknown"


def _id_from_url(pattern: str, source_url: str | None) -> str | None:
    match = re.search(pattern, source_url or "", flags=re.I)
    return match.group(1) if match else None


def _bvid(meta: dict[str, Any], source_url: str | None, filename: str) -> str | None:
    direct = _clean_scalar(meta.get("bvid"))
    if direct and re.fullmatch(r"BV[0-9A-Za-z]{6,}", direct, flags=re.I):
        return "BV" + direct[2:]
    found = _id_from_url(r"/video/(BV[0-9A-Za-z]{6,})", source_url)
    if not found:
        match = re.search(r"(BV[0-9A-Za-z]{6,})", filename, flags=re.I)
        found = match.group(1) if match else None
    return ("BV" + found[2:]) if found else None


def _dynamic_id(meta: dict[str, Any], source_url: str | None, filename: str) -> str | None:
    for key in ("dynamic_id", "opus_id"):
        direct = _clean_scalar(meta.get(key))
        if direct and direct.isdigit():
            return direct
    found = _id_from_url(r"/(?:opus|dynamic)/(\d+)", source_url)
    if found:
        return found
    match = re.search(r"(?i)(?:opus|dynamic)[^0-9]*(\d{6,})", filename)
    return match.group(1) if match else None


def _avid(meta: dict[str, Any], source_url: str | None, filename: str) -> str | None:
    direct = _clean_scalar(meta.get("avid"))
    if direct:
        return direct.removeprefix("av") if direct.casefold().startswith("av") else direct
    match = re.search(r"(?:/video/|\b)av(\d+)", f"{source_url or ''} {filename}", flags=re.I)
    return match.group(1) if match else None


def _content_type(source_type: str | None, note_type: str | None) -> str:
    value = normalize_text(source_type or note_type or "unknown")
    if value in {"bilibili-opus", "opus", "dynamic"}:
        return "bilibili-opus"
    if value in {"bilibili", "bilibili-video", "video", "video-note", "local-video"}:
        return "video"
    if value in {"ppt", "pptx", "presentation", "powerpoint"}:
        return "pptx"
    if value in {"pdf", "webpage", "local-file", "wechat-article"}:
        return value
    return value or "unknown"


def classify_heading(heading: str, inherited: str = "unknown") -> str:
    normalized = normalize_text(heading)
    compact = re.sub(r"\s+", "", normalized)
    if any(marker in compact for marker in ("原文抽取", "up主原文", "动态原文", "正文原文")):
        return "source_text"
    if any(marker in compact for marker in ("完整转写", "完整转录", "原始字幕", "字幕原文", "asr", "转写全文", "校对正文")):
        return "transcript"
    if any(marker in compact for marker in ("图片分析", "视觉分析", "图像分析", "ocr分析", "图片ocr")):
        return "llm_visual_analysis"
    if any(marker in compact for marker in ("来源追溯", "来源信息", "视频信息", "a股术语校验", "转换说明")):
        return "deterministic_metadata"
    if any(
        marker in compact
        for marker in (
            "qwen整理",
            "一句话概括",
            "速读摘要",
            "核心观点",
            "思维导图",
            "结构化笔记",
            "结构化正文",
            "详细讲义",
            "关键概念",
            "术语与概念",
            "待核验",
            "复习清单",
            "可复习清单",
            "金句/重要原话",
            "关键帧图文笔记",
            "风险提示",
            "全文翻译",
        )
    ):
        return "llm_organized"
    return inherited if inherited in {
        "source_text",
        "transcript",
        "llm_organized",
        "llm_visual_analysis",
        "deterministic_metadata",
    } else "unknown"


def extract_sections(markdown: str) -> tuple[list[str], list[dict[str, Any]]]:
    _meta, body, body_start = parse_frontmatter(markdown)
    lines = body.splitlines()
    headings: list[tuple[int, int, str]] = []
    fenced = False
    for index, line in enumerate(lines):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    if headings and headings[0][0] > 0:
        sections.append(
            {
                "heading": "正文开头",
                "level": 0,
                "start_line": body_start,
                "end_line": body_start + headings[0][0] - 1,
                "provenance": "unknown",
            }
        )
    for position, (line_index, level, heading) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        inherited = stack[-1][1] if stack else "unknown"
        provenance = classify_heading(heading, inherited)
        stack.append((level, provenance))
        end_index = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines) - 1
        sections.append(
            {
                "heading": heading[:300],
                "level": level,
                "start_line": body_start + line_index,
                "end_line": max(body_start + line_index, body_start + end_index),
                "provenance": provenance,
            }
        )
    if not headings and lines:
        sections.append(
            {
                "heading": "全文",
                "level": 0,
                "start_line": body_start,
                "end_line": body_start + len(lines) - 1,
                "provenance": "unknown",
            }
        )
    return [item[2][:300] for item in headings], sections


def _safe_note_files(profile: AutomationProfile) -> list[pathlib.Path]:
    output_dir = profile.output_dir.resolve()
    if not output_dir.is_dir() or not any(path_is_within(output_dir, root) for root in profile.allowed_output_roots):
        raise NoteRetrievalError("Profile note directory is unavailable", "NOTE_INDEX_UNAVAILABLE", True)
    paths: dict[str, pathlib.Path] = {}
    for candidate in output_dir.rglob("*.md"):
        if ".obsidian" in candidate.parts:
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_file() or not path_is_within(resolved, output_dir):
            continue
        if not any(path_is_within(resolved, root) for root in profile.allowed_output_roots):
            continue
        paths[str(resolved)] = resolved
    return [paths[key] for key in sorted(paths)]


def _scan_signature(profile: AutomationProfile) -> tuple[str, list[pathlib.Path]]:
    paths = _safe_note_files(profile)
    output_dir = profile.output_dir.resolve()
    records: list[str] = []
    for path in paths:
        stat = path.stat()
        records.append(f"{path.relative_to(output_dir).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}")
    digest = hashlib.sha256("\n".join(records).encode("utf-8")).hexdigest()
    return digest, paths


def _manifest_roots() -> list[pathlib.Path]:
    roots = [index_root(), _state_dir_read_only() / "up-sync"]
    result: list[pathlib.Path] = []
    for root in roots:
        resolved = root.resolve(strict=False)
        if resolved.exists() and resolved not in result:
            result.append(resolved)
    return result


def _resolve_manifest_output(raw: Any) -> pathlib.Path | None:
    text = _clean_scalar(raw)
    if not text:
        return None
    if text.startswith("file://"):
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path)
    path = pathlib.Path(text).expanduser()
    return path.resolve(strict=False) if path.is_absolute() else (WORKER_ROOT / path).resolve(strict=False)


def load_manifest_lookup(profile: AutomationProfile) -> dict[str, dict[str, Any]]:
    allowed_keys = {
        "source_path",
        "source_url",
        "title",
        "author",
        "author_mid",
        "published",
        "source_type",
        "content_type",
        "model",
        "organize_model",
        "organized_status",
        "status",
        "source_hash",
    }
    lookup: dict[str, dict[str, Any]] = {}
    output_dir = profile.output_dir.resolve()
    candidates: list[pathlib.Path] = []
    for root in _manifest_roots():
        candidates.extend(root.rglob("*manifest.json"))
        if root.name == "up-sync":
            candidates.extend(root.glob("*.json"))
    for path in sorted(set(candidates)):
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in payload.get("items", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            sanitized = {key: item.get(key) for key in allowed_keys if item.get(key) not in (None, "")}
            for output_key in ("organized_output_path", "note_path", "output", "output_path"):
                resolved = _resolve_manifest_output(item.get(output_key))
                if resolved and path_is_within(resolved, output_dir):
                    lookup[str(resolved)] = sanitized
    return lookup


def _stable_note_id(
    profile: AutomationProfile,
    relative_path: str,
    source_type: str,
    bvid: str | None,
    dynamic_id: str | None,
    source_url: str | None,
    source_path: str | None,
    source_hash: str | None,
) -> str:
    kind = source_type or "unknown"
    if bvid:
        identity = f"{kind}|bvid:{bvid.casefold()}"
    elif dynamic_id:
        identity = f"{kind}|dynamic_id:{dynamic_id}"
    elif source_url:
        identity = f"{kind}|source_url:{source_url}"
    elif source_hash and source_path:
        identity = f"{kind}|source_path:{normalize_text(source_path)}|source_hash:{source_hash.casefold()}"
    elif source_hash:
        identity = f"{kind}|source_hash:{source_hash.casefold()}"
    else:
        identity = f"{profile.id}|note_path:{unicodedata.normalize('NFKC', relative_path)}"
    return "note_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _metadata_quality(entry: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    missing = [key for key in ("author", "published", "source_hash") if not entry.get(key)]
    if not entry.get("organize_model") and not entry.get("model"):
        missing.append("model")
    if not entry.get("source_url") and not entry.get("source_path"):
        missing.append("source_identity")
    content_type = entry.get("content_type")
    source_type = normalize_text(entry.get("source_type"))
    is_bilibili_video = source_type in {"bilibili", "bilibili-video"} or "bilibili.com/video/" in normalize_text(entry.get("source_url"))
    if content_type == "video" and is_bilibili_video and not entry.get("bvid"):
        missing.append("bvid")
    if content_type == "bilibili-opus" and not entry.get("dynamic_id"):
        missing.append("dynamic_id")
    weights = {"author": 15, "published": 20, "model": 10, "source_identity": 20, "source_hash": 15, "bvid": 10, "dynamic_id": 10}
    score = max(0, 100 - sum(weights.get(item, 5) for item in missing))
    level = "high" if score >= 85 else "medium" if score >= 60 else "low"
    return missing, {"score": score, "level": level, "date_source": entry.get("date_source"), "legacy_compatible": bool(missing)}


def build_note_entry(
    profile: AutomationProfile,
    path: pathlib.Path,
    manifest: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = manifest or {}
    stat = path.stat()
    output_dir = profile.output_dir.resolve()
    relative_path = path.relative_to(output_dir).as_posix()
    markdown = ""
    readable = stat.st_size <= MAX_NOTE_FILE_BYTES
    if readable:
        markdown = path.read_text(encoding="utf-8", errors="replace")
    meta, body, _body_start = parse_frontmatter(markdown)

    def first(*values: Any) -> str | None:
        for value in values:
            clean = _clean_scalar(value)
            if clean:
                return clean
        return None

    source_url = _clean_url(meta.get("source_url")) or _clean_url(manifest.get("source_url"))
    note_type = first(meta.get("type"), manifest.get("type")) or "unknown"
    source_type = first(meta.get("source_type"), manifest.get("source_type")) or "unknown"
    if source_type not in SUPPORTED_SOURCE_TYPES and source_type in {"", "markdown"}:
        source_type = "unknown"
    content_type = first(meta.get("content_type"), manifest.get("content_type")) or _content_type(source_type, note_type)
    title_match = re.search(r"(?m)^#\s+(.+)$", body)
    title = first(meta.get("title"), manifest.get("title"), title_match.group(1).strip() if title_match else None, path.stem) or path.stem
    published, date_source = _published(meta, manifest)
    bvid = _bvid(meta, source_url, path.name)
    dynamic_id = _dynamic_id(meta, source_url, path.name)
    avid = _avid(meta, source_url, path.name)
    model = first(meta.get("model"), manifest.get("model"))
    organize_model = first(meta.get("organize_model"), manifest.get("organize_model")) or model
    source_path = first(meta.get("source_path"), manifest.get("source_path"))
    source_hash = first(meta.get("source_hash"), manifest.get("source_hash"))
    headings, sections = extract_sections(markdown) if readable else ([], [])
    entry: dict[str, Any] = {
        "note_id": "",
        "profile_id": profile.id,
        "path": str(path),
        "note_path": str(path),
        "relative_path": relative_path,
        "source_path": source_path,
        "source_url": source_url,
        "title": title[:500],
        "author": first(meta.get("author"), manifest.get("author")),
        "author_mid": first(meta.get("author_mid"), manifest.get("author_mid")),
        "published": published,
        "date_source": date_source,
        "type": note_type,
        "source_type": source_type,
        "content_type": content_type,
        "bvid": bvid,
        "avid": avid,
        "dynamic_id": dynamic_id,
        "opus_id": dynamic_id,
        "model": model,
        "organize_model": organize_model,
        "organized_status": first(meta.get("organized_status"), manifest.get("organized_status"), meta.get("status"), manifest.get("status")),
        "status": first(meta.get("status"), manifest.get("status")),
        "source_hash": source_hash,
        "tags": _normalize_tags(meta.get("tags")),
        "headings": headings,
        "sections": sections,
        "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat(timespec="seconds"),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "index_text_available": readable,
    }
    entry["note_id"] = _stable_note_id(
        profile,
        relative_path,
        source_type,
        bvid,
        dynamic_id,
        source_url,
        source_path,
        source_hash,
    )
    missing, quality = _metadata_quality(entry)
    entry["missing_metadata"] = missing
    entry["metadata_quality"] = quality
    _links, assets = extract_assets(path, body)
    entry["links"] = _links
    entry["asset_count"] = len(assets)
    return sanitize_mapping(entry), assets


def extract_assets(note_path: pathlib.Path, body: str) -> tuple[list[str], list[dict[str, Any]]]:
    links: list[str] = []
    assets: list[dict[str, Any]] = []
    for match in re.finditer(r"\[\[([^\]]+)\]\]", body):
        links.append(match.group(1).split("|", 1)[0].strip())
    for match in re.finditer(r"(!)?\[([^\]]*)\]\(([^)]+)\)", body):
        target = match.group(3).strip().strip("<>")
        if target.casefold().startswith("data:"):
            continue
        links.append(target)
        if match.group(1) and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) and not target.startswith("#"):
            resolved = (note_path.parent / target).resolve(strict=False)
            assets.append({"note_path": str(note_path), "target": target[:1000], "exists": resolved.exists()})
    return sorted(set(links))[:500], assets


def _prefer_entry(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    def rank(item: dict[str, Any]) -> tuple[int, int, int]:
        status = normalize_text(item.get("organized_status") or item.get("status"))
        complete = 2 if status in {"organized", "completed", "reviewed"} else 1 if status in {"converted", "draft"} else 0
        quality = int((item.get("metadata_quality") or {}).get("score") or 0)
        return complete, quality, int(item.get("mtime_ns") or 0)
    candidate_rank = rank(candidate)
    current_rank = rank(current)
    return candidate_rank > current_rank or (
        candidate_rank == current_rank
        and str(candidate.get("note_path") or "") < str(current.get("note_path") or "")
    )


def _atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def rebuild_profile_index(profile: AutomationProfile) -> dict[str, Any]:
    fingerprint, paths = _scan_signature(profile)
    manifest_lookup = load_manifest_lookup(profile)
    by_id: dict[str, dict[str, Any]] = {}
    assets: list[dict[str, Any]] = []
    unreadable = 0
    for path in paths:
        entry, note_assets = build_note_entry(profile, path, manifest_lookup.get(str(path), {}))
        if not entry.get("index_text_available"):
            unreadable += 1
        existing = by_id.get(entry["note_id"])
        if existing is None or _prefer_entry(entry, existing):
            by_id[entry["note_id"]] = entry
        assets.extend(note_assets)
    notes = sorted(by_id.values(), key=lambda item: str(item["note_path"]))
    generated_at = utc_now()
    note_payload = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "index_type": "local-notes-read-index",
        "generated_at": generated_at,
        "profile_id": profile.id,
        "notes_dir": str(profile.output_dir.resolve()),
        "scan_fingerprint": fingerprint,
        "source_note_count": len(paths),
        "note_count": len(notes),
        "deduplicated_count": len(paths) - len(notes),
        "oversize_note_count": unreadable,
        "notes": notes,
    }
    missing_assets = sum(1 for item in assets if not item.get("exists"))
    asset_payload = {
        "schema_version": ASSET_INDEX_SCHEMA_VERSION,
        "index_type": "local-notes-asset-index",
        "generated_at": generated_at,
        "profile_id": profile.id,
        "asset_count": len(assets),
        "missing_count": missing_assets,
        "assets": assets,
    }
    _atomic_json(profile_index_path(profile), note_payload)
    _atomic_json(profile_asset_index_path(profile), asset_payload)
    return {
        "profile_id": profile.id,
        "index_path": str(profile_index_path(profile)),
        "asset_index_path": str(profile_asset_index_path(profile)),
        "source_note_count": len(paths),
        "note_count": len(notes),
        "deduplicated_count": len(paths) - len(notes),
        "oversize_note_count": unreadable,
        "asset_count": len(assets),
        "missing_asset_count": missing_assets,
    }


def rebuild_index_result(profile: AutomationProfile, caller: str = "agent") -> dict[str, Any]:
    started = utc_now()
    try:
        detail = rebuild_profile_index(profile)
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": new_run_id(),
            "caller": caller,
            "task": "rebuild-index",
            "status": "completed",
            "started_at": started,
            "finished_at": utc_now(),
            "source_ref": profile.id,
            "output_dir": str(profile.output_dir),
            "counts": {"discovered": detail["source_note_count"], "created": detail["note_count"], "updated": 0, "skipped": detail["deduplicated_count"], "failed": 0},
            "outputs": [detail["index_path"], detail["asset_index_path"]],
            "deliveries": [],
            "manifest_path": detail["index_path"],
            "warnings": [],
            "details": {"note_index": detail},
            "error": None,
            "retryable": False,
        }
    except BaseException as exc:
        code = exc.error_code if isinstance(exc, AutomationError) else "NOTE_READ_FAILED"
        return _error_result("rebuild-index", code, "Note index rebuild failed", caller=caller, started_at=started, retryable=True)


def _validate_index_payload(profile: AutomationProfile, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise NoteRetrievalError("Note index schema is invalid", "NOTE_INDEX_CORRUPT", False)
    if payload.get("profile_id") != profile.id or not isinstance(payload.get("notes"), list):
        raise NoteRetrievalError("Note index profile contract is invalid", "NOTE_INDEX_CORRUPT", False)
    identifiers: set[str] = set()
    for item in payload["notes"]:
        if not isinstance(item, dict) or not item.get("note_id") or not item.get("note_path"):
            raise NoteRetrievalError("Note index entry is invalid", "NOTE_INDEX_CORRUPT", False)
        if item["note_id"] in identifiers:
            raise NoteRetrievalError("Note index contains duplicate IDs", "NOTE_INDEX_CORRUPT", False)
        identifiers.add(item["note_id"])
    fingerprint, _paths = _scan_signature(profile)
    if payload.get("scan_fingerprint") != fingerprint:
        raise NoteRetrievalError("Note index is stale; run rebuild-index explicitly", "NOTE_INDEX_STALE", True)
    return payload


def load_profile_index(profile: AutomationProfile) -> dict[str, Any]:
    path = profile_index_path(profile)
    if not path.is_file():
        raise NoteRetrievalError("Note index is unavailable; run rebuild-index explicitly", "NOTE_INDEX_UNAVAILABLE", True)
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise NoteRetrievalError("Note index exceeds the safe size", "NOTE_INDEX_CORRUPT", False)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except NoteRetrievalError:
        raise
    except (OSError, ValueError) as exc:
        raise NoteRetrievalError("Note index cannot be read", "NOTE_INDEX_CORRUPT", False) from exc
    return _validate_index_payload(profile, payload)


def load_available_indexes() -> tuple[list[tuple[AutomationProfile, dict[str, Any]]], list[str]]:
    profiles = load_profiles(include_disabled=False)
    if not profiles:
        raise NoteRetrievalError("No enabled Profiles are available", "NOTE_INDEX_UNAVAILABLE", True)
    loaded: list[tuple[AutomationProfile, dict[str, Any]]] = []
    warnings: list[str] = []
    failures: list[NoteRetrievalError] = []
    for profile in profiles.values():
        try:
            loaded.append((profile, load_profile_index(profile)))
        except NoteRetrievalError as exc:
            failures.append(exc)
            warnings.append(f"{exc.error_code}: Profile {profile.id} index was excluded")
    if not loaded:
        priority = {"NOTE_INDEX_CORRUPT": 3, "NOTE_INDEX_STALE": 2, "NOTE_INDEX_UNAVAILABLE": 1}
        selected = max(failures, key=lambda item: priority.get(item.error_code, 0))
        raise selected
    return loaded, warnings


def _safe_indexed_path(profile: AutomationProfile, entry: dict[str, Any]) -> pathlib.Path:
    raw = str(entry.get("note_path") or "")
    if not raw or "\x00" in raw:
        raise NoteRetrievalError("Indexed note path is invalid", "NOTE_PATH_NOT_ALLOWED", False)
    candidate = pathlib.Path(raw)
    if not candidate.is_absolute():
        raise NoteRetrievalError("Indexed note path is invalid", "NOTE_PATH_NOT_ALLOWED", False)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise NoteRetrievalError("Indexed note is missing", "NOTE_NOT_FOUND", False) from exc
    output_dir = profile.output_dir.resolve()
    if candidate.is_symlink() or resolved != candidate or not path_is_within(resolved, output_dir):
        raise NoteRetrievalError("Indexed note path is outside the Profile", "NOTE_PATH_NOT_ALLOWED", False)
    if not any(path_is_within(resolved, root) for root in profile.allowed_output_roots) or not resolved.is_file():
        raise NoteRetrievalError("Indexed note path is outside the allowlist", "NOTE_PATH_NOT_ALLOWED", False)
    return resolved


def _read_entry(profile: AutomationProfile, entry: dict[str, Any]) -> tuple[str, list[str]]:
    path = _safe_indexed_path(profile, entry)
    try:
        size = path.stat().st_size
        if size > MAX_NOTE_FILE_BYTES:
            raise NoteRetrievalError("Indexed note exceeds the safe read size", "INPUT_LIMIT_EXCEEDED", False)
        markdown = path.read_text(encoding="utf-8", errors="replace")
    except NoteRetrievalError:
        raise
    except OSError as exc:
        raise NoteRetrievalError("Indexed note cannot be read", "NOTE_READ_FAILED", True) from exc
    return markdown, markdown.splitlines()


def _date_value(value: Any) -> dt.date | None:
    normalized = _normalize_iso(value)
    if not normalized:
        return None
    try:
        return dt.date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _date_range(value: Any) -> tuple[dt.date | None, dt.date | None]:
    if value in (None, ""):
        return None, None
    if not isinstance(value, dict) or set(value) - {"start", "end"}:
        raise NoteRetrievalError("date_range must contain only start and end", "INVALID_DATE_RANGE", False)
    if not value.get("start") and not value.get("end"):
        raise NoteRetrievalError("date_range requires start or end", "INVALID_DATE_RANGE", False)
    start = _date_value(value.get("start")) if value.get("start") else None
    end = _date_value(value.get("end")) if value.get("end") else None
    if (value.get("start") and not start) or (value.get("end") and not end) or (start and end and start > end):
        raise NoteRetrievalError("date_range is invalid", "INVALID_DATE_RANGE", False)
    return start, end


def _bounded_int(value: Any, default: int, minimum: int, maximum: int, label: str = "limit") -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise NoteRetrievalError(f"{label} exceeds the safe range", "INPUT_LIMIT_EXCEEDED", False)
    return value


def _query(value: Any, label: str = "query") -> str:
    if not isinstance(value, str) or not value.strip():
        raise NoteRetrievalError(f"{label} is required", "INVALID_QUERY", False)
    normalized = unicodedata.normalize("NFKC", value).strip()
    if len(normalized) > MAX_QUERY_CHARS or "\x00" in normalized:
        raise NoteRetrievalError(f"{label} exceeds the safe length", "INPUT_LIMIT_EXCEEDED", False)
    return normalized


def _result_fields(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get(key)
        for key in (
            "note_id",
            "profile_id",
            "note_path",
            "source_path",
            "source_url",
            "title",
            "author",
            "published",
            "date_source",
            "bvid",
            "avid",
            "dynamic_id",
            "opus_id",
            "model",
            "organize_model",
            "source_type",
            "content_type",
            "missing_metadata",
            "metadata_quality",
        )
    }


def _section_text(lines: list[str], section: dict[str, Any]) -> str:
    start = max(0, int(section.get("start_line") or 1) - 1)
    end = max(start, int(section.get("end_line") or start + 1))
    return "\n".join(lines[start:end])


def _snippet(text: str, needle: str, max_chars: int = 320) -> str:
    clean = re.sub(r"\s+", " ", _sanitize_note_text(text)).strip()
    normalized = normalize_text(clean)
    index = normalized.find(needle)
    if index < 0:
        index = 0
    start = max(0, index - max_chars // 3)
    end = min(len(clean), start + max_chars)
    prefix = "…" if start else ""
    suffix = "…" if end < len(clean) else ""
    return prefix + clean[start:end] + suffix


def _score_entry(entry: dict[str, Any], lines: list[str], query: str) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    needle = normalize_text(query)
    score = 0
    snippets: list[dict[str, Any]] = []
    matched_sections: list[dict[str, Any]] = []
    title = normalize_text(entry.get("title"))
    if title == needle:
        score += 1200
        snippets.append({"heading": entry.get("title"), "layer": "title", "provenance": "deterministic_metadata", "text": str(entry.get("title"))[:320]})
    elif needle in title:
        score += 900
        snippets.append({"heading": entry.get("title"), "layer": "title", "provenance": "deterministic_metadata", "text": _snippet(str(entry.get("title")), needle)})
    metadata_values = [entry.get("author"), *(entry.get("tags") or []), entry.get("bvid"), entry.get("avid"), entry.get("dynamic_id")]
    for value in metadata_values:
        normalized = normalize_text(value)
        if not normalized:
            continue
        if normalized == needle:
            score += 750
        elif needle in normalized:
            score += 550
        else:
            continue
        if not any(item.get("layer") == "metadata" for item in snippets):
            snippets.append(
                {
                    "heading": "metadata",
                    "layer": "metadata",
                    "provenance": "deterministic_metadata",
                    "text": _snippet(str(value), needle),
                }
            )
    for section in entry.get("sections") or []:
        heading = normalize_text(section.get("heading"))
        if int(section.get("level") or 0) == 1 and heading == title:
            # The H1 repeats the already-scored title and would otherwise crowd
            # source/LLM evidence out of the bounded snippet list.
            continue
        heading_match = heading == needle or needle in heading
        text = _section_text(lines, section)
        body_match = needle in normalize_text(text)
        if not heading_match and not body_match:
            continue
        layer = "heading" if heading_match else "body"
        score += 500 if heading == needle else 400 if heading_match else 120
        provenance = str(section.get("provenance") or "unknown")
        matched = {"heading": section.get("heading"), "provenance": provenance, "layer": layer}
        if matched not in matched_sections and len(matched_sections) < MAX_MATCHED_SECTIONS:
            matched_sections.append(matched)
        if len(snippets) < MAX_MATCHED_SECTIONS:
            snippets.append(
                {
                    "heading": section.get("heading"),
                    "layer": layer,
                    "provenance": provenance,
                    "line_start": section.get("start_line"),
                    "line_end": section.get("end_line"),
                    "text": _snippet(text, needle),
                }
            )
    selected: list[dict[str, Any]] = []
    for layer in ("title", "metadata"):
        candidate = next((item for item in snippets if item.get("layer") == layer), None)
        if candidate and candidate not in selected:
            selected.append(candidate)
    for provenance in ("source_text", "transcript", "llm_organized", "llm_visual_analysis", "deterministic_metadata", "unknown"):
        candidate = next((item for item in snippets if item.get("provenance") == provenance), None)
        if candidate and candidate not in selected:
            selected.append(candidate)
    for candidate in snippets:
        if candidate not in selected:
            selected.append(candidate)
    return score, matched_sections, selected[:5]


def _sort_timestamp(value: Any) -> float:
    normalized = _normalize_iso(value)
    if not normalized:
        return -1.0
    try:
        parsed = dt.datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except ValueError:
        try:
            return dt.datetime.combine(dt.date.fromisoformat(normalized[:10]), dt.time(), tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            return -1.0


def search_notes(arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    query = _query(arguments.get("query"))
    author = arguments.get("author")
    if author is not None and (not isinstance(author, str) or len(author) > MAX_AUTHOR_CHARS):
        raise NoteRetrievalError("author filter is invalid", "INVALID_QUERY", False)
    start, end = _date_range(arguments.get("date_range"))
    limit = _bounded_int(arguments.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _bounded_int(arguments.get("offset"), 0, 0, MAX_OFFSET, "offset")
    max_chars = _bounded_int(arguments.get("max_chars"), 20000, 5000, MAX_RETURN_CHARS, "max_chars")
    indexes, warnings = load_available_indexes()
    matches: list[dict[str, Any]] = []
    scanned_bytes = 0
    for profile, payload in indexes:
        for entry in payload["notes"]:
            if author and normalize_text(author) not in normalize_text(entry.get("author")):
                continue
            if start or end:
                if entry.get("date_source") not in TRUSTED_DATE_SOURCES:
                    continue
                published = _date_value(entry.get("published"))
                if not published or (start and published < start) or (end and published > end):
                    continue
            size = int(entry.get("size") or 0)
            if size > MAX_NOTE_FILE_BYTES or scanned_bytes + size > MAX_SEARCH_TOTAL_BYTES:
                if "INPUT_LIMIT_EXCEEDED: some oversized note bodies were excluded" not in warnings:
                    warnings.append("INPUT_LIMIT_EXCEEDED: some oversized note bodies were excluded")
                continue
            markdown, lines = _read_entry(profile, entry)
            scanned_bytes += len(markdown.encode("utf-8", errors="ignore"))
            score, matched_sections, snippets = _score_entry(entry, lines, query)
            if score <= 0:
                continue
            item = _result_fields(entry)
            item.update(
                score=score,
                provenance=sorted(
                    {snippet["provenance"] for snippet in snippets}
                    | {section["provenance"] for section in matched_sections}
                ),
                matched_sections=matched_sections,
                snippets=snippets,
            )
            matches.append(item)
    deduplicated: dict[str, dict[str, Any]] = {}
    for item in matches:
        identity = str(item.get("note_id") or item.get("note_path"))
        current = deduplicated.get(identity)
        item_rank = (int(item.get("score") or 0), int((item.get("metadata_quality") or {}).get("score") or 0))
        current_rank = (
            int(current.get("score") or 0),
            int((current.get("metadata_quality") or {}).get("score") or 0),
        ) if current else (-1, -1)
        if current is None or item_rank > current_rank or (
            item_rank == current_rank and str(item.get("note_path")) < str(current.get("note_path"))
        ):
            deduplicated[identity] = item
    matches = list(deduplicated.values())
    matches.sort(key=lambda item: (-int(item["score"]), -_sort_timestamp(item.get("published")), str(item.get("note_path"))))
    selected: list[dict[str, Any]] = []
    used_chars = 0
    for item in matches[offset:]:
        encoded = json.dumps(item, ensure_ascii=False)
        if not selected and len(encoded) > max_chars:
            compact = dict(item)
            compact["matched_sections"] = list(item.get("matched_sections") or [])[:10]
            compact["snippets"] = list(item.get("snippets") or [])[:3]
            encoded = json.dumps(compact, ensure_ascii=False)
            while len(encoded) > max_chars and len(compact["snippets"]) > 1:
                compact["snippets"].pop()
                encoded = json.dumps(compact, ensure_ascii=False)
            while len(encoded) > max_chars and compact["matched_sections"]:
                compact["matched_sections"].pop()
                encoded = json.dumps(compact, ensure_ascii=False)
            item = compact
            warnings.append("INPUT_LIMIT_EXCEEDED: first result evidence was truncated by max_chars")
        if selected and used_chars + len(encoded) > max_chars:
            warnings.append("INPUT_LIMIT_EXCEEDED: results were truncated by max_chars")
            break
        selected.append(item)
        used_chars += len(encoded)
        if len(selected) >= limit:
            break
    details = {"query": query, "author": author or None, "date_range": arguments.get("date_range"), "offset": offset, "limit": limit, "total_matches": len(matches), "returned": len(selected), "truncated": offset + len(selected) < len(matches)}
    return selected, warnings, details


def _find_entry(path_or_id: str) -> tuple[AutomationProfile, dict[str, Any], list[str]]:
    if not isinstance(path_or_id, str) or not path_or_id.strip() or len(path_or_id) > 4096 or "\x00" in path_or_id:
        raise NoteRetrievalError("path_or_id is invalid", "INVALID_QUERY", False)
    value = path_or_id.strip()
    if ".." in pathlib.PurePath(value).parts:
        raise NoteRetrievalError("Path traversal is not allowed", "NOTE_PATH_NOT_ALLOWED", False)
    indexes, warnings = load_available_indexes()
    candidates: list[tuple[AutomationProfile, dict[str, Any]]] = []
    for profile, payload in indexes:
        for entry in payload["notes"]:
            if value == entry.get("note_id") or value == entry.get("note_path") or value == entry.get("relative_path"):
                candidates.append((profile, entry))
    if not candidates:
        if pathlib.Path(value).is_absolute():
            raise NoteRetrievalError("Absolute path is not present in an enabled Profile index", "NOTE_PATH_NOT_ALLOWED", False)
        raise NoteRetrievalError("Indexed note was not found", "NOTE_NOT_FOUND", False)
    if len(candidates) > 1:
        if value.startswith("note_") and all(item.get("note_id") == value for _profile, item in candidates):
            candidates.sort(
                key=lambda pair: (
                    -int((pair[1].get("metadata_quality") or {}).get("score") or 0),
                    -_sort_timestamp(pair[1].get("published")),
                    str(pair[1].get("note_path")),
                )
            )
            return candidates[0][0], candidates[0][1], warnings
        raise NoteRetrievalError("Note identifier is ambiguous; use note_id", "INVALID_QUERY", False)
    return candidates[0][0], candidates[0][1], warnings


def get_note(arguments: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    profile, entry, warnings = _find_entry(arguments.get("path_or_id"))
    offset = _bounded_int(arguments.get("offset"), 0, 0, MAX_OFFSET, "offset")
    max_chars = _bounded_int(arguments.get("max_chars"), DEFAULT_MAX_CHARS, 1, MAX_RETURN_CHARS, "max_chars")
    section_name = arguments.get("section")
    if section_name is not None and (not isinstance(section_name, str) or len(section_name) > 300):
        raise NoteRetrievalError("section is invalid", "INVALID_QUERY", False)
    markdown, lines = _read_entry(profile, entry)
    _meta, body, body_start_line = parse_frontmatter(markdown)
    selected_text = body
    selected_start_line = body_start_line
    selected_end_line = len(lines)
    selected_base_offset = sum(len(line) + 1 for line in lines[: max(0, body_start_line - 1)])
    all_sections = entry.get("sections") or []
    selected_sections = all_sections[:MAX_SECTION_CATALOG]
    if section_name:
        needle = normalize_text(section_name)
        matches = [item for item in all_sections if normalize_text(item.get("heading")) == needle or normalize_text(item.get("provenance")) == needle]
        if not matches:
            raise NoteRetrievalError("Requested section was not found", "NOTE_NOT_FOUND", False)
        selected_start_line = min(int(item["start_line"]) for item in matches)
        selected_end_line = max(int(item["end_line"]) for item in matches)
        selected_text = "\n".join(lines[selected_start_line - 1 : selected_end_line])
        selected_base_offset = sum(len(line) + 1 for line in lines[: selected_start_line - 1])
    if offset > len(selected_text):
        raise NoteRetrievalError("offset exceeds the selected note text", "INPUT_LIMIT_EXCEEDED", False)
    content = selected_text[offset : offset + max_chars]
    absolute_start = selected_base_offset + offset
    line_start = markdown.count("\n", 0, max(0, absolute_start)) + 1
    line_end = line_start + content.count("\n")
    item = _result_fields(entry)
    item.update(
        headings=(entry.get("headings") or [])[:MAX_SECTION_CATALOG],
        sections=selected_sections,
        section_catalog_truncated=len(all_sections) > len(selected_sections),
        content=_sanitize_note_text(content),
        truncated=offset + len(content) < len(selected_text),
        offset=offset,
        next_offset=offset + len(content) if offset + len(content) < len(selected_text) else None,
        char_count=len(selected_text),
        line_start=max(selected_start_line, line_start),
        line_end=min(selected_end_line, line_end),
        selected_section=section_name or None,
    )
    return item, warnings, {"returned_chars": len(content), "max_chars": max_chars}


def list_recent(arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    author = arguments.get("author")
    content_type = arguments.get("content_type")
    if author is not None and (not isinstance(author, str) or len(author) > MAX_AUTHOR_CHARS):
        raise NoteRetrievalError("author filter is invalid", "INVALID_QUERY", False)
    if content_type is not None and (not isinstance(content_type, str) or len(content_type) > 100):
        raise NoteRetrievalError("content_type filter is invalid", "INVALID_QUERY", False)
    days = _bounded_int(arguments.get("days"), 30, 0, 36500, "days")
    limit = _bounded_int(arguments.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _bounded_int(arguments.get("offset"), 0, 0, MAX_OFFSET, "offset")
    today = dt.datetime.now().astimezone().date()
    cutoff = today - dt.timedelta(days=days)
    indexes, warnings = load_available_indexes()
    results: list[dict[str, Any]] = []
    for _profile, payload in indexes:
        for entry in payload["notes"]:
            if entry.get("date_source") not in TRUSTED_DATE_SOURCES:
                continue
            published = _date_value(entry.get("published"))
            if not published or published < cutoff or published > today:
                continue
            if author and normalize_text(author) not in normalize_text(entry.get("author")):
                continue
            if content_type and normalize_text(content_type) != normalize_text(entry.get("content_type")) and normalize_text(content_type) != normalize_text(entry.get("source_type")):
                continue
            results.append(_result_fields(entry))
    deduplicated: dict[str, dict[str, Any]] = {}
    for item in results:
        identity = str(item.get("note_id") or item.get("note_path"))
        current = deduplicated.get(identity)
        if current is None or _sort_timestamp(item.get("published")) > _sort_timestamp(current.get("published")) or (
            _sort_timestamp(item.get("published")) == _sort_timestamp(current.get("published"))
            and str(item.get("note_path")) < str(current.get("note_path"))
        ):
            deduplicated[identity] = item
    results = list(deduplicated.values())
    results.sort(key=lambda item: (-_sort_timestamp(item.get("published")), str(item.get("note_path"))))
    selected = results[offset : offset + limit]
    return selected, warnings, {"author": author or None, "content_type": content_type or None, "days": days, "cutoff_date": cutoff.isoformat(), "as_of_date": today.isoformat(), "total_matches": len(results), "returned": len(selected), "offset": offset, "truncated": offset + len(selected) < len(results)}


def _explicit_valid_until(text: str) -> str | None:
    match = re.search(r"(?:有效期(?:至|到)|有效(?:至|到)|截止(?:至|到)?)\s*[：:]?\s*(\d{4}-\d{1,2}-\d{1,2})", text)
    if not match:
        return None
    return _normalize_iso(match.group(1))


def get_viewpoints(arguments: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str], dict[str, Any]]:
    topic = _query(arguments.get("symbol_or_topic"), "symbol_or_topic")
    raw_as_of = arguments.get("as_of_date")
    if not isinstance(raw_as_of, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_as_of):
        raise NoteRetrievalError("as_of_date must be YYYY-MM-DD", "INVALID_DATE_RANGE", False)
    try:
        as_of = dt.date.fromisoformat(raw_as_of)
    except ValueError as exc:
        raise NoteRetrievalError("as_of_date is invalid", "INVALID_DATE_RANGE", False) from exc
    limit = _bounded_int(arguments.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT)
    search_results, warnings, search_detail = search_notes({"query": topic, "limit": MAX_LIMIT, "offset": 0, "max_chars": MAX_RETURN_CHARS})
    groups: dict[str, list[dict[str, Any]]] = {
        "methodology_candidates": [],
        "dated_viewpoints": [],
        "historical_or_stale_candidates": [],
        "undated_candidates": [],
    }
    excluded_future = 0
    for result in search_results:
        trusted = result.get("date_source") in TRUSTED_DATE_SOURCES
        published = _date_value(result.get("published")) if trusted else None
        if published and published > as_of:
            excluded_future += 1
            continue
        for snippet in result.get("snippets") or []:
            evidence = {**_result_fields(result), "score": result.get("score"), "snippet": snippet}
            text = f"{snippet.get('heading') or ''} {snippet.get('text') or ''}"
            valid_until = _explicit_valid_until(text)
            evidence["explicit_valid_until"] = valid_until
            if any(marker in normalize_text(text) for marker in METHODOLOGY_MARKERS):
                evidence["classification_basis"] = "methodology marker matched; age does not imply invalidity"
                groups["methodology_candidates"].append(evidence)
            elif not published:
                evidence["classification_basis"] = "no trusted published timestamp"
                groups["undated_candidates"].append(evidence)
            else:
                age_days = (as_of - published).days
                evidence["age_days_as_of"] = age_days
                if age_days <= 7:
                    evidence["freshness_bucket"] = "recent"
                    evidence["classification_basis"] = "0-7 days before as_of_date"
                    groups["dated_viewpoints"].append(evidence)
                elif age_days <= 30:
                    evidence["freshness_bucket"] = "aging"
                    evidence["classification_basis"] = "8-30 days before as_of_date"
                    groups["dated_viewpoints"].append(evidence)
                else:
                    evidence["freshness_bucket"] = "historical"
                    evidence["classification_basis"] = "more than 30 days before as_of_date; this does not assert factual invalidity"
                    groups["historical_or_stale_candidates"].append(evidence)
    for key in groups:
        groups[key] = groups[key][:limit]
    details = {
        "symbol_or_topic": topic,
        "as_of_date": as_of.isoformat(),
        "classification_policy": {"recent_days": [0, 7], "aging_days": [8, 30], "historical_days": ">30", "methodology_age_rule": "never stale solely because of age"},
        "future_evidence_excluded": excluded_future,
        "service_behavior": "deterministic evidence grouping only; no investment advice or server-side viewpoint summary",
        "search_total_matches": search_detail["total_matches"],
    }
    return groups, warnings, details


def _base_result(task: str, caller: str, warnings: Iterable[str]) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": new_run_id(),
        "caller": caller,
        "task": task,
        "status": "completed",
        "started_at": now,
        "finished_at": now,
        "source_ref": "",
        "output_dir": "",
        "counts": {"discovered": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0},
        "outputs": [],
        "deliveries": [],
        "manifest_path": "",
        "warnings": list(warnings),
        "details": {},
        "error": None,
        "retryable": False,
    }


def _error_result(task: str, code: str, message: str, caller: str = "mcp", started_at: str | None = None, retryable: bool = False) -> dict[str, Any]:
    result = _base_result(task, caller, [])
    result.update(status="failed", started_at=started_at or result["started_at"], counts={"discovered": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 1}, error={"error_code": code, "message": redact_text(message)}, retryable=retryable)
    return result


def run_read_action(action: str, arguments: dict[str, Any], caller: str = "mcp") -> dict[str, Any]:
    task_names = {
        "search": "notes-search",
        "get": "notes-get",
        "list-recent": "notes-list-recent",
        "get-viewpoints": "notes-get-viewpoints",
    }
    task = task_names.get(action, action)
    try:
        if action == "search":
            results, warnings, details = search_notes(arguments)
            result = _base_result(task, caller, warnings)
            result["results"] = results
            result["counts"]["discovered"] = len(results)
        elif action == "get":
            note, warnings, details = get_note(arguments)
            result = _base_result(task, caller, warnings)
            result["note"] = note
            result["counts"]["discovered"] = 1
        elif action == "list-recent":
            results, warnings, details = list_recent(arguments)
            result = _base_result(task, caller, warnings)
            result["results"] = results
            result["counts"]["discovered"] = len(results)
        elif action == "get-viewpoints":
            groups, warnings, details = get_viewpoints(arguments)
            result = _base_result(task, caller, warnings)
            result["groups"] = groups
            result["counts"]["discovered"] = sum(len(items) for items in groups.values())
        else:
            raise NoteRetrievalError("Unsupported read action", "INVALID_QUERY", False)
        result["details"] = details
        return sanitize_mapping(result)
    except NoteRetrievalError as exc:
        return _error_result(task, exc.error_code, str(exc), caller=caller, retryable=exc.retryable)
    except AutomationError as exc:
        return _error_result(task, exc.error_code, "Profile configuration is unavailable", caller=caller, retryable=exc.retryable)
    except BaseException:
        return _error_result(task, "NOTE_READ_FAILED", "Note retrieval failed", caller=caller, retryable=True)
