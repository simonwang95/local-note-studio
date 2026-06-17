#!/usr/bin/env python3
"""Export a Markdown note directory recursively to one EPUB file."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "indexes", "cache", "models"}


def rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def slugify(text: str, fallback: str = "notes") -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text).strip(" .-_")
    return text[:120] or fallback


def custom_epub_path(output_dir: pathlib.Path, output_filename: str, default_name: str) -> pathlib.Path:
    name = output_filename.strip() or default_name
    if pathlib.PurePath(name).name != name or "/" in name or "\\" in name:
        raise ValueError("--output-filename 只能是文件名，不能包含目录")
    if not name.lower().endswith(".epub"):
        name += ".epub"
    return output_dir / name


def markdown_files(source_dir: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for path in sorted(source_dir.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.relative_to(source_dir).parts[:-1]):
            continue
        files.append(path)
    return files


def render_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def build_command(source_dir: pathlib.Path, files: list[pathlib.Path], output_path: pathlib.Path, title: str) -> list[str]:
    resource_paths = {source_dir, output_path.parent}
    resource_paths.update(path.parent for path in files)
    return [
        "pandoc",
        *[str(path) for path in files],
        "--from",
        "markdown+yaml_metadata_block+pipe_tables+tex_math_dollars+fenced_divs",
        "--to",
        "epub3",
        "--standalone",
        "--toc",
        "--metadata",
        f"title={title}",
        "--resource-path",
        os.pathsep.join(str(path) for path in sorted(resource_paths)),
        "-o",
        str(output_path),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, help="Markdown note directory to export recursively.")
    parser.add_argument("--output-dir", required=True, help="Directory for generated EPUB.")
    parser.add_argument("--output-filename", default="", help="Custom EPUB file name; directory separators are not allowed.")
    parser.add_argument("--title", default="", help="EPUB title. Defaults to source directory name.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing EPUB.")
    parser.add_argument("--dry-run", action="store_true", help="Print the pandoc command without running.")
    args = parser.parse_args(argv)

    source_dir = pathlib.Path(args.source_dir).expanduser()
    if not source_dir.is_absolute():
        source_dir = source_dir.resolve()
    else:
        source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"source directory not found: {source_dir}")

    output_dir = pathlib.Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = output_dir.resolve()
    else:
        output_dir = output_dir.resolve()
    title = args.title.strip() or source_dir.name
    output_path = custom_epub_path(output_dir, args.output_filename, f"{slugify(title)}.epub")

    files = markdown_files(source_dir)
    if not files:
        raise RuntimeError(f"no Markdown files found under {source_dir}")
    if output_path.exists() and not args.overwrite:
        print(f"skip existing: {rel(output_path)}")
        print(str(output_path))
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(source_dir, files, output_path, title)
    pandoc = shutil.which("pandoc")
    if pandoc:
        command[0] = pandoc
    print(f"exporting {len(files)} Markdown files from {source_dir}")
    print(render_command(command))
    if args.dry_run:
        return 0
    if not pandoc:
        raise RuntimeError("pandoc is required for EPUB export. Install with `brew install pandoc` or `conda install -c conda-forge pandoc`.")
    result = subprocess.run(command, cwd=str(source_dir), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"pandoc failed ({result.returncode})")
    print(f"epub: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
