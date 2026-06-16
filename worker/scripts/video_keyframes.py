#!/usr/bin/env python3
"""Extract key frames and insert an image-text section into video notes."""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v"}


def _run(command: list[str], cwd: pathlib.Path | None = None) -> tuple[int, str]:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout


def _ffprobe_duration(path: pathlib.Path) -> float:
    code, output = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if code != 0:
        raise RuntimeError(output.strip() or f"ffprobe failed for {path}")
    return max(0.0, float((output or "0").strip() or "0"))


def _scene_timestamps(path: pathlib.Path, max_frames: int) -> list[float]:
    code, output = _run(
        [
            "ffmpeg",
            "-i",
            str(path),
            "-filter:v",
            "select='gt(scene,0.32)',showinfo",
            "-vsync",
            "vfr",
            "-f",
            "null",
            "-",
        ]
    )
    if code != 0 and "showinfo" not in output:
        raise RuntimeError(output.strip() or f"ffmpeg scene detection failed for {path}")
    raw_times = [float(match.group(1)) for match in re.finditer(r"pts_time:([0-9.]+)", output)]
    deduped: list[float] = []
    for value in raw_times:
        if not deduped or value - deduped[-1] >= 6.0:
            deduped.append(value)
        if len(deduped) >= max_frames * 3:
            break
    return deduped


def _fallback_timestamps(duration: float, max_frames: int) -> list[float]:
    if duration <= 0 or max_frames <= 0:
        return []
    step = duration / float(max_frames + 1)
    return [round(step * index, 2) for index in range(1, max_frames + 1)]


def _choose_timestamps(duration: float, candidates: list[float], max_frames: int) -> list[float]:
    if duration <= 0:
        return []
    selected: list[float] = []
    min_gap = max(8.0, duration / max(max_frames + 1, 2) * 0.6)
    for value in candidates:
        if value < 3.0 or value > max(3.0, duration - 3.0):
            continue
        if selected and value - selected[-1] < min_gap:
            continue
        selected.append(round(value, 2))
        if len(selected) >= max_frames:
            return selected
    if len(selected) < max_frames:
        for value in _fallback_timestamps(duration, max_frames):
            if all(abs(value - old) >= min_gap for old in selected):
                selected.append(value)
            if len(selected) >= max_frames:
                break
    return sorted(selected[:max_frames])


def _extract_frame(video_path: pathlib.Path, timestamp: float, out_path: pathlib.Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    code, output = _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp:.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out_path),
        ]
    )
    if code != 0 or not out_path.exists():
        raise RuntimeError(output.strip() or f"ffmpeg frame extraction failed at {timestamp:.2f}s")


def _extract_transcript_text(markdown: str) -> str:
    for title in ("原始字幕", "完整原文"):
        details_match = re.search(rf"(?ms)<details>\s*<summary>📄 {title}</summary>\s*(.+?)\s*</details>", markdown)
        if details_match:
            return details_match.group(1).strip()
        full_match = re.search(rf"(?ms)^##\s+{title}\s*$\n(.+)\Z", markdown)
        if full_match:
            return full_match.group(1).strip()
    return ""


def _snippet_for_timestamp(text: str, timestamp: float, duration: float, max_chars: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    chunks = [chunk.strip() for chunk in re.split(r"[。！？\n]+", cleaned) if chunk.strip()]
    if not chunks:
        return cleaned[:max_chars]
    index = int((timestamp / max(duration, 1.0)) * len(chunks))
    index = max(0, min(len(chunks) - 1, index))
    snippet = chunks[index]
    return snippet[:max_chars].rstrip("，,、 ")


def _format_timestamp(seconds: float) -> str:
    total = int(round(seconds))
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"


def _keyframe_section(asset_dir_name: str, frames: list[dict[str, str]]) -> str:
    lines = [
        "## 关键帧图文笔记",
        "> 关键帧按画面切换和内容位置抽取，用来快速回看讲解节奏；摘录为按全文位置估算的就近文本，不等同于严格时间轴字幕。",
    ]
    for frame in frames:
        lines.extend(
            [
                "",
                f"### {frame['title']}",
                "",
                f"![{frame['title']}](assets/{asset_dir_name}/{frame['file_name']})",
                "",
                f"> 相关摘录：{frame['snippet'] or '该位置未抽取到可用文本。'}",
            ]
        )
    return "\n".join(lines).strip()


def _replace_or_insert_keyframe_section(markdown: str, section: str) -> str:
    pattern = r"(?ms)^##\s+关键帧图文笔记\s*$\n.+?(?=^##\s+|<details>|\Z)"
    if re.search(pattern, markdown):
        return re.sub(pattern, section + "\n\n", markdown, count=1)
    for marker in ("\n<details>", "\n## 原始字幕", "\n## 完整原文"):
        index = markdown.find(marker)
        if index != -1:
            return markdown[:index].rstrip() + "\n\n" + section + "\n\n" + markdown[index:].lstrip()
    return markdown.rstrip() + "\n\n" + section + "\n"


def _download_bilibili_video(source_url: str, work_dir: pathlib.Path, cookie_file: str = "") -> pathlib.Path:
    output_tpl = work_dir / "source.%(ext)s"
    command = ["yt-dlp", "-f", "b[height<=480]/bv*[height<=480]+ba/b", "--merge-output-format", "mp4", "-o", str(output_tpl)]
    cookie_path = cookie_file.strip()
    if cookie_path:
        command.extend(["--cookies", cookie_path])
    command.append(source_url)
    code, output = _run(command, cwd=work_dir)
    if code != 0:
        raise RuntimeError(output.strip() or f"yt-dlp download failed for {source_url}")
    for file in sorted(work_dir.glob("source.*")):
        if file.suffix.lower() in VIDEO_EXTS:
            return file
    raise RuntimeError(f"downloaded video not found for {source_url}")


def resolve_video_source(note_meta: dict[str, Any], cookie_file: str = "") -> pathlib.Path | None:
    source_path = str(note_meta.get("source_path") or "").strip()
    if source_path:
        path = pathlib.Path(source_path).expanduser()
        if path.exists() and path.suffix.lower() in VIDEO_EXTS:
            return path
        return None
    source_url = str(note_meta.get("source_url") or "").strip()
    if "bilibili.com" not in source_url:
        return None
    temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="local-note-bili-video-"))
    return _download_bilibili_video(source_url, temp_dir, cookie_file)


def add_keyframes_to_note(
    note_path: pathlib.Path,
    note_meta: dict[str, Any],
    *,
    max_frames: int = 4,
    cookie_file: str = "",
) -> dict[str, Any]:
    video_path = resolve_video_source(note_meta, cookie_file)
    if video_path is None:
        return {"enabled": True, "status": "skipped", "reason": "video source unavailable or audio-only", "assets": []}

    try:
        markdown = note_path.read_text(encoding="utf-8", errors="replace")
        transcript_text = _extract_transcript_text(markdown)
        duration = _ffprobe_duration(video_path)
        timestamps = _choose_timestamps(duration, _scene_timestamps(video_path, max_frames), max_frames)
        if not timestamps:
            return {"enabled": True, "status": "skipped", "reason": "no keyframe timestamps detected", "assets": []}

        asset_dir = note_path.parent / "assets" / f"{note_path.stem}-keyframes"
        frames: list[dict[str, str]] = []
        for index, timestamp in enumerate(timestamps, start=1):
            file_name = f"frame-{index:02d}-{int(round(timestamp)):04d}.jpg"
            frame_path = asset_dir / file_name
            _extract_frame(video_path, timestamp, frame_path)
            frames.append(
                {
                    "title": f"关键帧 {index} · {_format_timestamp(timestamp)}",
                    "file_name": file_name,
                    "snippet": _snippet_for_timestamp(transcript_text, timestamp, duration),
                    "timestamp": _format_timestamp(timestamp),
                }
            )

        markdown = _replace_or_insert_keyframe_section(markdown, _keyframe_section(asset_dir.name, frames))
        note_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")

        return {
            "enabled": True,
            "status": "generated",
            "assets": [f"assets/{asset_dir.name}/{frame['file_name']}" for frame in frames],
            "timestamps": [frame["timestamp"] for frame in frames],
        }
    finally:
        if video_path.parent.name.startswith("local-note-bili-video-"):
            shutil.rmtree(video_path.parent, ignore_errors=True)
