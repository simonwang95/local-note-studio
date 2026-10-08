"""Conservative, local quality gates and private transcript diagnostics.

These checks detect mechanical failures, not factual accuracy. Never invent
missing speech or automatically turn an uncertain proper noun into a fact.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

if __package__:
    from .task_diagnostics import clean_source_ref, incognito, source_identity, utc_now
else:
    from task_diagnostics import clean_source_ref, incognito, source_identity, utc_now


CACHE_CATEGORIES = {
    "source": "source_evidence", "source-by-note": "source_evidence",
    "review": "source_evidence", "asr": "asr_diagnostics",
    "proofread-checkpoints": "success_checkpoints",
    "proofread-rejected": "failed_recovery",
}


def _note_source(note_path):
    """Read only source metadata; never copy a note body into telemetry."""
    if not note_path:
        return ""
    try:
        with Path(note_path).open(encoding="utf-8") as handle:
            header = handle.read(16384)
    except (OSError, UnicodeError):
        return ""
    for pattern in (r"(?m)^source_url:\s*(.+)$", r"(?m)^source_path:\s*(.+)$",
                    r"(?m)^>\s*\*\*链接\*\*[：:]\s*(.+)$"):
        match = re.search(pattern, header)
        if match:
            value = match.group(1).strip().strip('"\'`')
            if value.startswith("file://"):
                from urllib.parse import unquote, urlsplit
                value = unquote(urlsplit(value).path)
            return clean_source_ref(value)
    return ""


def compact_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()


PHRASE_REPEAT_RE = re.compile(r"([\u4e00-\u9fff]{2,12})(?:[\s，,。]*\1){4,}")


@dataclass(frozen=True)
class ShortRepetition:
    start: int
    end: int
    phrase: str
    count: int
    before: str
    after: str


def short_repetition_candidates(text: str) -> list[ShortRepetition]:
    """Locate bounded emphasis candidates; this alone never approves a repeat."""
    candidates = []
    for match in PHRASE_REPEAT_RE.finditer(text):
        run = compact_text(match.group())
        # Use the shortest period: ten repetitions of a two-character word must
        # not masquerade as five repetitions of a four-character phrase.
        phrase = next((run[:size] for size in range(1, min(12, len(run)) + 1)
                       if len(run) % size == 0 and run[:size] * (len(run) // size) == run), run)
        count = len(run) // len(phrase)
        if not (2 <= len(phrase) <= 4 and 5 <= count <= 6 and len(run) <= 24):
            continue
        candidates.append(ShortRepetition(
            match.start(), match.end(), phrase, count,
            compact_text(text[:match.start()])[-48:],
            compact_text(text[match.end():])[:48],
        ))
    return candidates


def _context_agrees(left: str, right: str) -> bool:
    if min(len(left), len(right)) < 12:
        return False
    if PHRASE_REPEAT_RE.search(left) or PHRASE_REPEAT_RE.search(right):
        return False
    alignment = difflib.SequenceMatcher(None, left, right, autojunk=False)
    return alignment.ratio() >= 0.8 and max(block.size for block in alignment.get_matching_blocks()) >= 12


def match_source_short_repetitions(text: str, source: str, *, require_position: bool = True) -> list[tuple[ShortRepetition, ShortRepetition]]:
    """Match individual repeats with complete nearby source context, one to one."""
    available = short_repetition_candidates(source)
    matches = []
    blocks = difflib.SequenceMatcher(None, compact_text(source), compact_text(text), autojunk=False).get_matching_blocks() if require_position else []
    for candidate in short_repetition_candidates(text):
        for index, original in enumerate(available):
            if require_position:
                original_start = len(compact_text(source[:original.start]))
                candidate_start = len(compact_text(text[:candidate.start]))
                run_length = len(candidate.phrase) * candidate.count
                if not any(block.a <= original_start and original_start + run_length <= block.a + block.size
                           and block.b + original_start - block.a == candidate_start for block in blocks):
                    continue
            if (candidate.phrase == original.phrase and candidate.count == original.count
                    and _context_agrees(candidate.before, original.before)
                    and _context_agrees(candidate.after, original.after)):
                matches.append((candidate, original))
                available.pop(index)
                break
    return matches


def repetition_errors(text: str) -> list[str]:
    errors = []
    if re.search(r"[。.!！?？]{8,}", text):
        errors.append("连续异常标点")
    if PHRASE_REPEAT_RE.search(text):
        errors.append("短语循环重复")
    paragraphs = [compact_text(p) for p in re.split(r"\n\s*\n", text)]
    paragraphs = [p for p in paragraphs if len(p) >= 120]
    for previous, current in zip(paragraphs, paragraphs[1:]):
        if difflib.SequenceMatcher(None, previous, current, autojunk=False).ratio() >= 0.88:
            errors.append("相邻大段内容重复")
            break
    return errors


def proofread_errors(text: str, source: str = "") -> list[str]:
    errors = repetition_errors(text)
    if "短语循环重复" in errors and source:
        supported = {(candidate.start, candidate.end) for candidate, _ in match_source_short_repetitions(text, source)}
        if all(match.span() in supported for match in PHRASE_REPEAT_RE.finditer(text)):
            errors.remove("短语循环重复")
    if not compact_text(text):
        return errors + ["校对正文为空"]
    # Spaces are not punctuation; otherwise spaced ASR words evade this gate.
    for span in re.split(r"[，。！？；：,.!?;:\n]", text):
        if len(re.findall(r"[\u4e00-\u9fff]", span)) > 180:
            errors.append("超过180字的中文片段没有标点")
            break
    original, corrected = compact_text(source), compact_text(text)
    if len(original) >= 300:
        if len(corrected) < len(original) * 0.65:
            errors.append("校对正文过度缩写，可能漏文")
        elif len(corrected) > len(original) * 1.5:
            errors.append("校对正文异常扩写")
        # Local coverage catches a missing middle/tail even if total length is OK.
        matcher = difflib.SequenceMatcher(None, original, corrected, autojunk=False)
        blocks = matcher.get_matching_blocks()
        for start in range(0, len(original), 240):
            end = min(len(original), start + 240)
            if end - start < 100:
                continue
            covered = sum(max(0, min(end, b.a + b.size) - max(start, b.a)) for b in blocks)
            if covered / (end - start) < 0.45:
                errors.append(f"原文第{start + 1}–{end}字覆盖不足，可能漏段")
                break
    return errors


def _diagnostic_root(kind: str) -> Path | None:
    if incognito():
        ephemeral = os.environ.get("LOCAL_NOTE_STUDIO_EPHEMERAL_SOURCE_DIR", "")
        return Path(ephemeral) / kind if kind == "source-by-note" and ephemeral else None
    state = Path(os.environ.get("LOCAL_NOTE_STUDIO_STATE_DIR") or
                 Path.home() / "Library/Application Support/Local Note Studio/state")
    return Path(os.environ.get("TRANSCRIPT_CACHE_DIR") or state / "transcripts") / kind


def save_transcript_diagnostic(kind: str, identity: str, payload: dict) -> str:
    """Atomic private cache; incognito permits only Worker-owned temporary source."""
    root = _diagnostic_root(kind)
    if root is None:
        return ""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    path = root / f"{digest}.json"
    previous = {}
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(old, dict) and isinstance(old.get("cache_metadata"), dict):
            previous = old["cache_metadata"]
    except (OSError, ValueError):
        pass
    run_id = os.environ.get("LOCAL_NOTE_STUDIO_RUN_ID") or None
    source = clean_source_ref(payload.get("source_ref") or _note_source(payload.get("note_path")) or
                              os.environ.get("LOCAL_NOTE_STUDIO_SOURCE_REF"))
    source_id = source_identity(source)
    references = [row for row in previous.get("references", []) if isinstance(row, dict)]
    for reference in ({"type": "run", "id": run_id}, {"type": "source", "id": source_id}):
        if reference["id"] and reference not in references:
            references.append(reference)
    note_path = payload.get("note_path")
    stage_root = os.environ.get("VIDEO_TRANSACTION_STAGE_DIR")
    final_root = os.environ.get("VIDEO_TRANSACTION_FINAL_DIR")
    if note_path and stage_root and final_root:
        try:
            note_path = str(Path(final_root) / Path(note_path).relative_to(Path(stage_root)))
        except ValueError:
            pass
    status = "failed" if payload.get("errors") else "completed" if kind in {"asr", "proofread-checkpoints"} else "available"
    timestamp = utc_now()
    saved = dict(payload)
    saved["cache_metadata"] = {
        "schema_version": 1, "kind": kind,
        "category": CACHE_CATEGORIES.get(kind, "disposable_cache"),
        "run_id": run_id, "task": os.environ.get("LOCAL_NOTE_STUDIO_TASK") or None,
        "source_ref": source or None, "source_identity": source_id,
        "note_path": note_path or None, "status": status,
        "created_at": previous.get("created_at") or timestamp, "updated_at": timestamp,
        "references": references,
    }
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(saved, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def load_transcript_diagnostic(kind: str, identity: str) -> dict | None:
    """Read a private cache or current ephemeral source, never old incognito data."""
    root = _diagnostic_root(kind)
    if root is None:
        return None
    path = root / f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None
