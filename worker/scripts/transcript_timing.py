"""Timestamp evidence passed with the current transcript, independent of visible subtitles."""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import tempfile
from typing import Any

try:
    from .transcript_quality import load_transcript_diagnostic, save_transcript_diagnostic
except ImportError:
    from transcript_quality import load_transcript_diagnostic, save_transcript_diagnostic

def _timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return 0.0
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] if numbers else 0.0


def _timestamped_transcript_segments(text: str) -> list[dict[str, Any]]:
    """Read SRT/VTT or inline timestamped text into source-backed intervals."""
    segments: list[dict[str, Any]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip().lstrip("\ufeff")
        match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)", line)
        if match:
            start, end = _timestamp_seconds(match.group(1)), _timestamp_seconds(match.group(2))
            index += 1
            payload = []
            while index < len(lines) and lines[index].strip():
                if not re.fullmatch(r"\d+", lines[index].strip()):
                    payload.append(lines[index].strip())
                index += 1
            value = re.sub(r"<[^>]+>", "", " ".join(payload)).strip()
            if value and end >= start:
                segments.append({"start": start, "end": end, "text": value})
        else:
            inline = re.match(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+(.+)$", line)
            if inline:
                segments.append({"start": _timestamp_seconds(inline.group(1)), "end": 0.0, "text": inline.group(2).strip()})
        index += 1
    for position, segment in enumerate(segments):
        if segment["end"] <= segment["start"]:
            segment["end"] = segments[position + 1]["start"] if position + 1 < len(segments) else segment["start"] + 8.0
    return segments


def valid_segments(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list):
        return []
    result = []
    previous = -1.0
    for row in rows:
        try:
            start, end = float(row["start"]), float(row["end"])
            text = str(row["text"]).strip()
        except (KeyError, TypeError, ValueError):
            return []
        if not text or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or start < previous:
            return []  # Do not advertise malformed/discontinuous ordering as exact alignment.
        result.append({"start": start, "end": end, "text": text})
        previous = start
    return result


def write_segments(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".lns-timing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "segments": valid_segments(rows)}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def timing_path(note: pathlib.Path) -> pathlib.Path:
    return note.with_name(f".{note.name}.lns-timing.json")


def load_note_timing(note: pathlib.Path) -> list[dict]:
    # The sidecar lives in the transaction staging area, also for incognito runs.
    try:
        data = json.loads(timing_path(note).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = load_transcript_diagnostic("timing-by-note", str(note.resolve())) or {}
    return valid_segments(data.get("segments", [])) if isinstance(data, dict) else []


def attach_timing(note: pathlib.Path, source: pathlib.Path) -> None:
    text = source.read_text(encoding="utf-8-sig")
    if source.suffix.lower() == ".json":
        data = json.loads(text)
        rows = data.get("segments", []) if isinstance(data, dict) else data
    else:
        rows = _timestamped_transcript_segments(text)
    write_segments(timing_path(note), rows)
    save_transcript_diagnostic("timing-by-note", str(note.resolve()), {
        "schema_version": 1, "note_path": str(note.resolve()), "segments": valid_segments(rows),
    })


def timestamp_for_position(source: str, position: int, rows: list[dict]) -> tuple[float, float] | None:
    cursor = 0
    for row in rows:
        start = source.find(row["text"], cursor)
        if start < 0:
            continue
        end = start + len(row["text"])
        if start <= position < end:
            return row["start"], row["end"]
        cursor = end
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--note", type=pathlib.Path, required=True)
    parser.add_argument("--source", type=pathlib.Path, required=True)
    args = parser.parse_args()
    attach_timing(args.note, args.source)
