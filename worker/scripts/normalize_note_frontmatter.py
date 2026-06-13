#!/usr/bin/env python3
"""Normalize YAML frontmatter for Markdown notes.

The script is intentionally conservative: it preserves existing metadata,
keeps the Markdown body unchanged, and only adds missing standard fields or
reorders known fields into the project style-guide order.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]

DEFAULTS = {
    "NOTES_DIR": "notes",
    "DEFAULT_LLM_MODEL": "qwen3.6-35b-a3b-nvfp4",
}

FIELD_ORDER = [
    "title",
    "type",
    "source_type",
    "source_path",
    "source_url",
    "bvid",
    "avid",
    "author",
    "published",
    "duration",
    "transcript_source",
    "transcribed_at",
    "created",
    "updated",
    "status",
    "model",
    "tags",
    "source_hash",
]

VIDEO_FIELD_PATTERNS = {
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


def config() -> dict[str, str]:
    values = dict(DEFAULTS)
    values.update(load_env_file(ROOT / "env.local"))
    for key in DEFAULTS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def today() -> str:
    return dt.date.today().isoformat()


def rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_scalar(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return text.strip('"').strip("'")
    if loaded is None:
        return ""
    return str(loaded)


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---", 4)
    if end == -1:
        return {}, markdown

    raw = markdown[4:end].strip().splitlines()
    body = markdown[end + len("\n---") :].lstrip("\n")
    data: dict[str, Any] = {}
    current_key = ""

    for line in raw:
        if line.startswith("  - ") and current_key:
            if not isinstance(data.get(current_key), list):
                data[current_key] = []
            data[current_key].append(clean_scalar(line[4:]))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        data[current_key] = clean_scalar(value)

    return data, body


def yaml_scalar(value: Any) -> str:
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


def frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    ordered_keys = [key for key in FIELD_ORDER if key in data]
    ordered_keys.extend(key for key in data if key not in FIELD_ORDER)
    for key in ordered_keys:
        value = data[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def first_heading(body: str) -> str:
    match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def title_from_path(path: pathlib.Path) -> str:
    title = path.stem
    title = re.sub(r"^(PDF|DOCX|CHAT|WEB|WECHAT)-", "", title)
    title = title.replace("_", " ").strip()
    return title or "未命名笔记"


def parse_video_metadata(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, pattern in VIDEO_FIELD_PATTERNS.items():
        match = re.search(pattern, body, flags=re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    url = fields.get("source_url", "")
    bvid_match = re.search(r"\b(BV[0-9A-Za-z]+)\b", url) or re.search(r"\b(BV[0-9A-Za-z]+)\b", body[:500])
    if bvid_match:
        fields["bvid"] = bvid_match.group(1)
    return fields


def infer_source_type(path: pathlib.Path, body: str, meta: dict[str, Any], video_meta: dict[str, str]) -> str:
    existing = str(meta.get("source_type") or "").strip()
    if existing:
        return existing

    url = video_meta.get("source_url") or str(meta.get("source_url") or "")
    filename = path.name
    rel_path = rel(path)
    upper_name = filename.upper()

    if "bilibili.com/video/" in url or re.search(r"\bBV[0-9A-Za-z]+\b", filename):
        return "bilibili"
    if url.startswith("file://") or "转录来源" in body[:800]:
        return "local-video"
    if upper_name.startswith("PDF-"):
        return "pdf"
    if upper_name.startswith("DOCX-"):
        return "docx"
    if upper_name.startswith("CHAT-"):
        return "lmstudio-conversation"
    if upper_name.startswith("WECHAT-") or "mp.weixin.qq.com" in url:
        return "wechat-article"
    if upper_name.startswith("WEB-") or url.startswith(("http://", "https://")):
        return "webpage"
    if "/VibeCoding-Yihui/" in rel_path:
        return "course"
    return "markdown"


def infer_type(path: pathlib.Path, source_type: str, body: str, meta: dict[str, Any]) -> str:
    existing = str(meta.get("type") or "").strip()
    if existing:
        return existing
    if source_type in {"bilibili", "local-video", "video"}:
        return "video-note"
    if source_type in {"pdf", "docx", "lmstudio-conversation", "webpage", "wechat-article"}:
        return "source-conversion"
    if source_type == "course" or "/VibeCoding-Yihui/" in rel(path):
        return "course-note"
    if "## 视频摘要" in body[:2000] or "## 完整转录" in body[:3000]:
        return "video-note"
    return "note"


def infer_created(path: pathlib.Path, meta: dict[str, Any], video_meta: dict[str, str]) -> str:
    for key in ("created", "published"):
        value = str(meta.get(key) or video_meta.get(key) or "").strip()
        match = re.search(r"\d{4}-\d{2}-\d{2}", value)
        if match:
            return match.group(0)

    name_match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if name_match:
        return name_match.group(1)

    return today()


def normalize_tags(tags: Any, note_type: str, source_type: str, status: str) -> list[str]:
    if isinstance(tags, str):
        values = [tags]
    elif isinstance(tags, list):
        values = [str(item).strip() for item in tags if str(item).strip()]
    else:
        values = []

    inferred: list[str] = []
    if note_type == "video-note":
        inferred.append("video")
    if note_type == "course-note":
        inferred.append("course")
    if source_type and source_type != "markdown":
        if source_type == "wechat-article":
            inferred.extend(["source/web", "source/wechat"])
        else:
            inferred.append(f"source/{source_type}")
    if status:
        inferred.append(f"status/{status}")

    seen: set[str] = set()
    normalized: list[str] = []
    for tag in values + inferred:
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def normalize_note(path: pathlib.Path, cfg: dict[str, str]) -> bool:
    original = path.read_text(encoding="utf-8-sig")
    meta, body = parse_frontmatter(original)
    video_meta = parse_video_metadata(body)
    source_type = infer_source_type(path, body, meta, video_meta)
    note_type = infer_type(path, source_type, body, meta)
    status = str(meta.get("status") or "draft")

    for key, value in video_meta.items():
        meta.setdefault(key, value)

    source_url = str(meta.get("source_url") or "")
    source_path = str(meta.get("source_path") or "")
    if source_url.startswith("file://") and not source_path:
        source_path = source_url.removeprefix("file://")
        source_url = ""

    meta["title"] = str(meta.get("title") or first_heading(body) or title_from_path(path))
    meta["type"] = note_type
    meta["source_type"] = source_type
    meta["source_path"] = source_path
    meta["source_url"] = source_url
    meta["created"] = str(meta.get("created") or infer_created(path, meta, video_meta))
    meta["updated"] = today()
    meta["status"] = status
    meta["model"] = str(meta.get("model") or cfg["DEFAULT_LLM_MODEL"])
    meta["tags"] = normalize_tags(meta.get("tags"), note_type, source_type, status)
    meta["source_hash"] = str(meta.get("source_hash") or sha256_text(body))

    rendered = frontmatter(meta) + "\n\n" + body.rstrip() + "\n"
    if rendered == original:
        return False
    path.write_text(rendered, encoding="utf-8")
    return True


def markdown_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize YAML frontmatter for Markdown notes.")
    parser.add_argument("--notes-dir", default="", help="Notes directory. Defaults to NOTES_DIR from env.local.")
    parser.add_argument("--dry-run", action="store_true", help="Report files that would change without writing.")
    args = parser.parse_args()

    cfg = config()
    notes_dir = pathlib.Path(args.notes_dir or cfg["NOTES_DIR"])
    if not notes_dir.is_absolute():
        notes_dir = ROOT / notes_dir
    if not notes_dir.exists():
        raise SystemExit(f"notes directory not found: {notes_dir}")

    changed: list[pathlib.Path] = []
    files = markdown_files(notes_dir)
    for path in files:
        original = path.read_text(encoding="utf-8-sig")
        meta, body = parse_frontmatter(original)
        video_meta = parse_video_metadata(body)
        source_type = infer_source_type(path, body, meta, video_meta)
        note_type = infer_type(path, source_type, body, meta)
        status = str(meta.get("status") or "draft")
        preview = dict(meta)
        for key, value in video_meta.items():
            preview.setdefault(key, value)
        source_url = str(preview.get("source_url") or "")
        source_path = str(preview.get("source_path") or "")
        if source_url.startswith("file://") and not source_path:
            source_path = source_url.removeprefix("file://")
            source_url = ""
        preview.update(
            {
                "title": str(preview.get("title") or first_heading(body) or title_from_path(path)),
                "type": note_type,
                "source_type": source_type,
                "source_path": source_path,
                "source_url": source_url,
                "created": str(preview.get("created") or infer_created(path, preview, video_meta)),
                "updated": today(),
                "status": status,
                "model": str(preview.get("model") or cfg["DEFAULT_LLM_MODEL"]),
                "tags": normalize_tags(preview.get("tags"), note_type, source_type, status),
                "source_hash": str(preview.get("source_hash") or sha256_text(body)),
            }
        )
        rendered = frontmatter(preview) + "\n\n" + body.rstrip() + "\n"
        if rendered != original:
            changed.append(path)
            if not args.dry_run:
                path.write_text(rendered, encoding="utf-8")

    action = "would update" if args.dry_run else "updated"
    print(f"scanned={len(files)} {action}={len(changed)} notes_dir={rel(notes_dir)}")
    for path in changed[:25]:
        print(f"- {rel(path)}")
    if len(changed) > 25:
        print(f"... {len(changed) - 25} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
