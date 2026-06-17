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

from video_keyframes import add_keyframes_to_note


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
    "DEFAULT_LLM_API_BASE": "http://127.0.0.1:1234/v1",
    "DEFAULT_LLM_API_KEY": "lm-studio",
    "DEFAULT_LLM_MODEL": "qwen3.6-35b-a3b-nvfp4",
    "CONDA_ENV": "course-whisper",
    "ASR_ENGINE": "whisper",
    "ASR_LOCAL_MODEL": "",
    "ASR_PROMPT": "以下是中文课程、AI、投资、摄影等学习材料音频，请尽量保留术语。",
    "FORCE_ASR": "false",
    "EXTRACT_KEYFRAMES": "false",
    "KEYFRAME_MAX_COUNT": "4",
    "KEEP_ORIGINAL_SUBTITLES": "true",
    "OVERWRITE_OUTPUT": "false",
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
        if os.environ.get(key):
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
        "KEEP_ORIGINAL_SUBTITLES": cfg["KEEP_ORIGINAL_SUBTITLES"],
        "OVERWRITE_OUTPUT": cfg["OVERWRITE_OUTPUT"],
    }
    for key, value in mappings.items():
        if value:
            env[key] = value
    return env


def python_command(cfg: dict[str, str], script: pathlib.Path) -> list[str]:
    if cfg.get("CONDA_ENV"):
        return ["conda", "run", "--no-capture-output", "-n", cfg["CONDA_ENV"], "python3", "-u", str(script)]
    return [sys.executable, "-u", str(script)]


def bash_command(cfg: dict[str, str], script: pathlib.Path, *args: str) -> list[str]:
    if cfg.get("CONDA_ENV"):
        return ["conda", "run", "--no-capture-output", "-n", cfg["CONDA_ENV"], "bash", str(script), *args]
    return ["bash", str(script), *args]


def stream_command(command: list[str], cwd: pathlib.Path, env: dict[str, str], timeout: int | None = None) -> tuple[int, str]:
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
    return process.wait(), "".join(output)


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
    paths: list[str] = []
    seen = set()
    for line in stdout.splitlines():
        for match in re.finditer(r"(/[^\s]+\.md)", line.strip()):
            path = match.group(1)
            if path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            paths.append(path)
    return paths


def load_manifest(path: pathlib.Path) -> dict[str, object]:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: pathlib.Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    path.write_text("\n\n".join([frontmatter(meta), body]).rstrip() + "\n", encoding="utf-8")

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
        key = path.as_posix()
        item = postprocess_video_note(path, cfg, (extras or {}).get(key))
        if manifest_enabled:
            upsert_manifest_item(manifest, item)
            print(f"manifest: {rel(path)}")
        else:
            print(f"frontmatter: {rel(path)}")
    if manifest_enabled and manifest_path is not None:
        save_manifest(manifest_path, manifest)


def append_processed(avid: str, cfg: dict[str, str]) -> None:
    state_dir = cfg.get("BILIBILI_STATE_DIR", str(ROOT / "indexes" / "bilibili-state"))
    if not pathlib.Path(state_dir).is_absolute():
        state_dir = str(ROOT / state_dir)
    state_dir = os.path.expanduser(os.path.expandvars(state_dir))
    pathlib.Path(state_dir).mkdir(parents=True, exist_ok=True)
    processed_file = pathlib.Path(state_dir) / "processed_videos.txt"
    with processed_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{avid}\n")


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

    code, output = stream_command(transcribe_command, project_dir, env, timeout=7200)
    if code != 0:
        return code

    paths = extract_markdown_paths(output)
    if not paths:
        print("未从输出中识别到 Markdown 路径，跳过 summary-only")
        return 0

    postprocess_video_notes(paths[-1:], cfg)

    failures = 0
    for path in paths[-1:]:
        summary_command = python_command(cfg, batch) + ["--summary-only", path]
        print("\nsummary:", " ".join(summary_command))
        summary_code, _summary_output = stream_command(summary_command, project_dir, env, timeout=7200)
        if summary_code != 0:
            failures += 1
    postprocess_video_notes(paths[-1:], cfg)
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

    code, output = stream_command(transcribe_command, project_dir, env, timeout=7200)
    if code != 0:
        return code

    paths = extract_markdown_paths(output)
    if not paths:
        print("未从输出中识别到 Markdown 路径，跳过 summary-only")
        return 0

    postprocess_video_notes(paths[-1:], cfg)

    summary_command = python_command(cfg, batch) + ["--summary-only", paths[-1]]
    print("\nsummary:", " ".join(summary_command))
    summary_code, _summary_output = stream_command(summary_command, project_dir, env, timeout=7200)
    postprocess_video_notes(paths[-1:], cfg)
    return summary_code


def run_favorite_limited(project_dir: pathlib.Path, cfg: dict[str, str], limit: int, dry_run: bool) -> int:
    env = project_env(cfg)
    script_dir = project_dir / "scripts" / "bilibili"
    scanner = script_dir / "bilibili_scanner.py"
    transcript = script_dir / "bilibili_transcript.sh"
    batch = script_dir / "batch_transcribe.py"
    scan_command = python_command(cfg, scanner)
    print("scan:", " ".join(scan_command))
    if dry_run:
        print(f"then process first {limit} new video(s)")
        return 0

    scan_code, scan_output = stream_command(scan_command, project_dir, env, timeout=120)
    if scan_code != 0:
        return scan_code
    videos = parse_scanner_output(scan_output)
    if not videos:
        print("没有新视频需要转录")
        return 0

    selected = videos[:limit]
    print(f"\n限量处理 {len(selected)}/{len(videos)} 个新视频")
    failures = 0
    processed_paths: list[str] = []
    extras: dict[str, dict[str, str]] = {}
    for index, video in enumerate(selected, 1):
        bvid = video.get("bvid", "")
        avid = video.get("avid", "")
        title = video.get("title", bvid)
        if not bvid:
            print(f"跳过无 BVID 条目: {video}", file=sys.stderr)
            failures += 1
            continue
        print(f"\n[{index}/{len(selected)}] {title} ({bvid})")
        transcribe_command = bash_command(cfg, transcript, f"https://www.bilibili.com/video/{bvid}/")
        code, output = stream_command(transcribe_command, project_dir, env, timeout=7200)
        if code != 0:
            failures += 1
            continue
        if avid:
            append_processed(avid, cfg)
        paths = extract_markdown_paths(output)
        if not paths:
            print("未从输出中识别到 Markdown 路径，跳过 summary-only")
            continue
        for path in paths[-1:]:
            extras[path] = {
                "avid": avid,
                "bvid": bvid,
                "title": title,
                "author": video.get("upper", ""),
                "duration": video.get("duration", ""),
            }
            postprocess_video_notes([path], cfg, extras)
            summary_command = python_command(cfg, batch) + ["--summary-only", path]
            print("\nsummary:", " ".join(summary_command))
            summary_code, _summary_output = stream_command(summary_command, project_dir, env, timeout=7200)
            if summary_code != 0:
                failures += 1
            processed_paths.append(path)
    postprocess_video_notes(processed_paths, cfg, extras)
    return 1 if failures else 0


def main() -> int:
    cfg = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--favorite", action="store_true", help="process configured Bilibili favorite list")
    parser.add_argument("--url", help="process one Bilibili video URL")
    parser.add_argument("--local-file", help="process one local video or audio file")
    parser.add_argument("--local-dir", help="process a local video directory")
    parser.add_argument("--recursive", action="store_true", help="recurse through local directory")
    parser.add_argument("--summary-only", action="store_true", help="fill summaries for existing Markdown outputs")
    parser.add_argument("--limit", type=int, default=0, help="in favorite mode, process only the first N new videos")
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

    selected = sum(bool(item) for item in [args.favorite, args.url, args.local_file, args.local_dir, args.summary_only])
    if selected != 1:
        parser.error("choose exactly one of --favorite, --url, --local-file, --local-dir, --summary-only")

    if args.favorite and args.limit > 0:
        return run_favorite_limited(project_dir, cfg, args.limit, args.dry_run)

    if args.url:
        if args.sync_env:
            print("--sync-env is no longer needed after migration; current project env.local is used directly.")
        return run_url(project_dir, cfg, args.url, args.dry_run, args.output_filename)

    if args.local_file:
        if args.sync_env:
            print("--sync-env is no longer needed after migration; current project env.local is used directly.")
        return run_local_file(project_dir, cfg, args.local_file, args.dry_run, args.output_filename)

    command = python_command(cfg, script_dir / "batch_transcribe.py")
    if args.local_dir:
        if args.output_filename:
            parser.error("--output-filename cannot be used with --local-dir")
        command.extend(["--local-dir", args.local_dir, "--output-dir", cfg["BILIBILI_OUTPUT_DIR"]])
        if args.recursive:
            command.append("--recursive")
    elif args.summary_only:
        command.append("--summary-only")

    env = project_env(cfg)
    if args.sync_env:
        print("--sync-env is no longer needed after migration; current project env.local is used directly.")
    print(" ".join(command))
    if args.dry_run:
        return 0
    code, output = stream_command(command, project_dir, env, timeout=7200)
    paths = extract_markdown_paths(output)
    postprocess_video_notes(paths, cfg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
