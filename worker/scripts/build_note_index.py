#!/usr/bin/env python3
"""Build a lightweight note index and asset report for Markdown notes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]

DEFAULTS = {
    "NOTES_DIR": "notes",
    "INDEX_DIR": "indexes",
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


def rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


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
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(line[4:].strip().strip('"').strip("'"))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        data[current_key] = value.strip('"').strip("'") if value else []
    return data, body


def title_from(body: str, meta: dict[str, Any], path: pathlib.Path) -> str:
    if meta.get("title"):
        return str(meta["title"])
    match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    return match.group(1).strip() if match else path.stem


def normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        if value.startswith("[") and value.endswith("]"):
            return [item.strip().strip('"').strip("'") for item in value[1:-1].split(",") if item.strip()]
        return [value.strip()]
    return []


def is_external(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)) or target.startswith("#")


def extract_links_and_assets(note_path: pathlib.Path, body: str) -> tuple[list[str], list[dict[str, Any]]]:
    links: list[str] = []
    assets: list[dict[str, Any]] = []
    for match in re.finditer(r"\[\[([^\]]+)\]\]", body):
        links.append(match.group(1).split("|", 1)[0].strip())
    for match in re.finditer(r"(!)?\[([^\]]*)\]\(([^)]+)\)", body):
        is_image = bool(match.group(1))
        target = match.group(3).strip().strip("<>")
        links.append(target)
        if is_image and not is_external(target):
            resolved = (note_path.parent / target).resolve()
            assets.append(
                {
                    "note_path": rel(note_path),
                    "target": target,
                    "resolved_path": rel(resolved),
                    "exists": resolved.exists(),
                }
            )
    return sorted(set(links)), assets


def build_indexes(notes_dir: pathlib.Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    notes: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    for note_path in sorted(notes_dir.rglob("*.md")):
        if ".obsidian" in note_path.parts:
            continue
        markdown = note_path.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(markdown)
        links, note_assets = extract_links_and_assets(note_path, body)
        stat = note_path.stat()
        notes.append(
            {
                "path": rel(note_path),
                "title": title_from(body, meta, note_path),
                "type": meta.get("type", ""),
                "source_type": meta.get("source_type", ""),
                "source_path": meta.get("source_path", ""),
                "status": meta.get("status", ""),
                "tags": normalize_tags(meta.get("tags")),
                "links": links,
                "asset_count": len(note_assets),
                "modified_at": dt.datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
                "size": stat.st_size,
            }
        )
        assets.extend(note_assets)
    return notes, assets


def main() -> int:
    cfg = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes-dir", default=cfg["NOTES_DIR"], help="notes directory to scan")
    parser.add_argument("--index-dir", default=cfg["INDEX_DIR"], help="directory for JSON indexes")
    parser.add_argument("--check", action="store_true", help="exit non-zero when missing assets exist")
    args = parser.parse_args()

    notes_dir = (ROOT / args.notes_dir).resolve()
    index_dir = (ROOT / args.index_dir).resolve()
    note_index_path = index_dir / "note-index.json"
    asset_index_path = index_dir / "asset-index.json"
    if not notes_dir.exists():
        raise FileNotFoundError(notes_dir)

    notes, assets = build_indexes(notes_dir)
    missing = [item for item in assets if not item["exists"]]
    index_dir.mkdir(parents=True, exist_ok=True)
    note_index_path.write_text(
        json.dumps(
            {
                "generated_at": now_iso(),
                "notes_dir": rel(notes_dir),
                "note_count": len(notes),
                "notes": notes,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    asset_index_path.write_text(
        json.dumps(
            {
                "generated_at": now_iso(),
                "notes_dir": rel(notes_dir),
                "asset_count": len(assets),
                "missing_count": len(missing),
                "assets": assets,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"notes={len(notes)} assets={len(assets)} missing={len(missing)}")
    print(f"note_index={rel(note_index_path)}")
    print(f"asset_index={rel(asset_index_path)}")
    return 1 if args.check and missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
