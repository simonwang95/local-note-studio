#!/usr/bin/env python3
"""Batch-prepend a publication-date prefix to existing note file names.

This is the standalone "文件名补充日期" (add date to file names) operation. It
scans a directory of Markdown notes, reads each note's ``published`` frontmatter
value, and renames files to ``YYYY-MM-DD-<original name>``. The operation is
idempotent: files that already start with a date prefix are left untouched, and
files without a parseable ``published`` value are skipped and reported rather
than guessed.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

from note_filename import has_date_prefix, parse_published_date

ROOT = pathlib.Path(__file__).resolve().parents[1]

_PUBLISHED_RE = re.compile(r"(?m)^published:\s*(.+?)\s*$")


def published_from_markdown(markdown: str) -> str:
    """Return the raw ``published`` value from a note's frontmatter, or ``""``."""
    if not markdown.startswith("---"):
        return ""
    end = markdown.find("\n---", 3)
    if end == -1:
        return ""
    frontmatter = markdown[3:end]
    match = _PUBLISHED_RE.search(frontmatter)
    return match.group(1).strip() if match else ""


def rename_directory(output_dir: pathlib.Path, dry_run: bool) -> int:
    if not output_dir.exists():
        print(f"输出目录不存在：{output_dir}")
        return 1
    files = sorted(output_dir.glob("*.md"))
    renamed = 0
    already_prefixed = 0
    no_date = 0
    for path in files:
        if has_date_prefix(path.name):
            already_prefixed += 1
            continue
        try:
            markdown = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            no_date += 1
            print(f"[跳过] 无法读取：{path.name}")
            continue
        date = parse_published_date(published_from_markdown(markdown))
        if not date:
            no_date += 1
            print(f"[跳过] 缺少可解析的发布时间：{path.name}")
            continue
        new_path = path.with_name(f"{date}-{path.name}")
        if new_path.exists():
            no_date += 1
            print(f"[跳过] 目标文件已存在：{new_path.name}")
            continue
        if dry_run:
            print(f"[预览] {path.name} -> {new_path.name}")
        else:
            path.rename(new_path)
            print(f"[重命名] {path.name} -> {new_path.name}")
        renamed += 1
    print(
        f"{'预览' if dry_run else '完成'}：共 {len(files)} 个文件，"
        f"{'将' if dry_run else ''}重命名 {renamed}，已有日期前缀 {already_prefixed}，"
        f"跳过（无日期/冲突）{no_date}。"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="directory of Markdown notes to rename")
    parser.add_argument("--dry-run", action="store_true", help="preview renames without writing")
    args = parser.parse_args()
    output_dir = (ROOT / args.output_dir).resolve()
    return rename_directory(output_dir, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
