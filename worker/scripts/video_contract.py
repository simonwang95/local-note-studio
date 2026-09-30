"""Shared completion and review contracts for generated video notes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal


NUMBER_PATTERN = r"(?<!\d)(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?"
UNIT_PATTERN = r"美元|人民币|百分点|平方米|公斤|千克|兆瓦|克|小时|季度|元|块|股|手|%|％|点|倍|片|吨|人|台|年|月|天|秒|米|瓦"
NUMBER_UNIT_RE = re.compile(rf"{NUMBER_PATTERN}\s*(?:万|亿|千|百)?\s*(?:{UNIT_PATTERN})?")

try:
    from .transcript_quality import compact_text, proofread_errors
except ImportError:  # direct script execution adds worker/scripts to sys.path
    from transcript_quality import compact_text, proofread_errors


PLACEHOLDER_RE = re.compile(r"【AI待处理[^】]*】")
SECTION_NAMES = {
    "一句话概括": ("一句话概括",),
    "速读摘要": ("速读摘要", "结构化摘要"),
    "思维导图": ("思维导图",),
    "结构化正文": ("结构化正文",),
    "金句/重要原话": ("金句/重要原话", "金句与重要原话"),
    "可复习清单": ("可复习清单",),
    "术语与概念": ("术语与概念",),
    "校对正文": ("校对正文", "AI校对"),
}


@dataclass(frozen=True)
class VideoValidation:
    status: str
    errors: tuple[str, ...]
    completed_sections: tuple[str, ...]
    missing_sections: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.status in {"completed", "no_changes"}


def section_text(markdown: str, names: tuple[str, ...]) -> str:
    for name in names:
        match = re.search(
            rf"(?ms)^##\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s+|\Z)",
            markdown,
        )
        if match:
            return re.sub(r"(?m)^\s*---\s*$", "", match.group(1)).strip()
    return ""


def _timestamp_at_position(source: str, position: int) -> tuple[float, float] | None:
    patterns = (
        re.compile(r"(?ms)(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)\s*\n(.*?)(?=\n\s*\n|\Z)"),
        re.compile(r"(?m)^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+(.+)$"),
    )
    for index, pattern in enumerate(patterns):
        for match in pattern.finditer(source):
            text_start = match.start(3) if index == 0 else match.start(2)
            text = match.group(3) if index == 0 else match.group(2)
            if text_start <= position <= text_start + len(text):
                def seconds(value: str) -> float:
                    chunks = value.replace(",", ".").split(":")
                    values = [float(part) for part in chunks]
                    return values[0] * 3600 + values[1] * 60 + values[2] if len(values) == 3 else values[0] * 60 + values[1] if len(values) == 2 else values[0]
                start = seconds(match.group(1))
                end = seconds(match.group(2)) if index == 0 else start + 8
                return start, end
    return None


def raw_transcript(markdown: str) -> str:
    patterns = (
        r"(?ms)<details>\s*<summary>📄\s*(?:原始字幕|完整原文)</summary>\s*(.+?)\s*</details>",
        r"(?ms)^##\s+(?:原始字幕|完整原文)\s*$\n(.+?)(?=^##\s+|\Z)",
    )
    for pattern in patterns:
        match = re.search(pattern, markdown)
        if match:
            return match.group(1).strip()
    return ""


def validate_video_note(
    markdown: str,
    *,
    transcription_only: bool = False,
    raw_transcript_override: str = "",
) -> VideoValidation:
    """Validate the shared full-note contract or the explicit transcript-only contract."""
    errors: list[str] = []
    raw = raw_transcript(markdown) or raw_transcript_override
    if not raw and transcription_only:
        errors.append("缺少可恢复的原始转写")
    if transcription_only:
        if not raw:
            errors.append("仅转写模式必须保留原始转写")
        return VideoValidation(
            "completed" if not errors else "failed",
            tuple(dict.fromkeys(errors)),
            ("原始转写",) if raw else (),
            (),
        )

    completed: list[str] = []
    missing: list[str] = []
    for canonical, names in SECTION_NAMES.items():
        value = section_text(markdown, names)
        if not value or PLACEHOLDER_RE.search(value) or not compact_text(value):
            missing.append(canonical)
            errors.append(f"缺少完整栏目：{canonical}")
            continue
        completed.append(canonical)
        if canonical == "校对正文":
            errors.extend(f"校对正文：{error}" for error in proofread_errors(value, raw))
    # Catch orphaned placeholders in template/legacy sections as well.
    if PLACEHOLDER_RE.search(markdown):
        errors.append("笔记仍含 AI 待处理占位符")
    status = "completed" if not errors else "failed"
    return VideoValidation(status, tuple(dict.fromkeys(errors)), tuple(completed), tuple(missing))


def review_transcript_changes(source: str, corrected: str) -> list[dict[str, object]]:
    """Return conservative human-review flags for meaning-bearing token changes."""
    findings: list[dict[str, object]] = []
    patterns = {
        "否定词": re.compile(r"不|没|未|无|非|不能|不会|并非"),
        "方向词": re.compile(r"买入|卖出|买|卖|看多|看空|加仓|减仓|做多|做空|上涨|下跌"),
        "数字或单位": NUMBER_UNIT_RE,
        "证券代码": re.compile(r"(?<!\d)[036]\d{5}(?:\.(?:SH|SZ))?(?!\d)", re.I),
        "公司/主体名称": re.compile(r"[A-Z][A-Za-z0-9.&-]{1,30}|[\u4e00-\u9fff]{2,12}(?:股份有限公司|有限责任公司|集团|证券|银行|股份|控股|科技|公司)"),
    }
    for kind, pattern in patterns.items():
        left_matches, right_matches = list(pattern.finditer(source)), list(pattern.finditer(corrected))
        left, right = [item.group(0) for item in left_matches], [item.group(0) for item in right_matches]
        def canonical(token: str):
            if kind != "数字或单位":
                return token
            match = re.fullmatch(rf"\s*([\d,，]+(?:\.\d+)?)\s*(万|亿|千|百)?\s*({UNIT_PATTERN})?\s*", token)
            if not match:
                return token
            number = Decimal(match.group(1).replace(",", "").replace("，", ""))
            multiplier = {"万": 10000, "亿": 100000000, "千": 1000, "百": 100}.get(match.group(2), 1)
            dimension = {"块": "元", "％": "%"}.get(match.group(3), match.group(3) or "")
            return (number * multiplier, dimension)

        if kind == "数字或单位":
            if [canonical(item) for item in left] == [canonical(item) for item in right]:
                continue
        elif left == right:
            continue
        differing_index = next((index for index in range(max(len(left), len(right)))
                                if index >= len(left) or index >= len(right)
                                or canonical(left[index]) != canonical(right[index])), 0)
        source_match = left_matches[differing_index] if differing_index < len(left_matches) else None
        corrected_match = right_matches[differing_index] if differing_index < len(right_matches) else None
        source_token = source_match.group(0) if source_match else ""
        corrected_token = corrected_match.group(0) if corrected_match else ""
        source_pos = source_match.start() if source_match else -1
        corrected_pos = corrected_match.start() if corrected_match else -1
        timestamp_range = _timestamp_at_position(source, source_pos) if source_pos >= 0 else None
        findings.append({
            "kind": kind,
            "source": left[:20],
            "corrected": right[:20],
            "source_position": source_pos + 1 if source_pos >= 0 else None,
            "corrected_position": corrected_pos + 1 if corrected_pos >= 0 else None,
            "source_context": source[max(0, source_pos - 48):source_pos + len(source_token) + 48] if source_pos >= 0 else "",
            "corrected_context": corrected[max(0, corrected_pos - 48):corrected_pos + len(corrected_token) + 48] if corrected_pos >= 0 else "",
            "timestamp_range": timestamp_range,
            "status": "needs_review",
            "message": "原文与校对结果中的关键内容不同，请对照上下文复核。",
        })
    # A-share codes are never accepted as model-added facts.
    source_codes = set(patterns["证券代码"].findall(source))
    added_codes = sorted(set(patterns["证券代码"].findall(corrected)) - source_codes)
    if added_codes:
        findings.append({
            "kind": "新增证券代码",
            "source": [],
            "corrected": added_codes,
            "source_position": None,
            "corrected_position": corrected.find(added_codes[0]) + 1 if added_codes[0] in corrected else None,
            "timestamp_range": None,
            "status": "needs_review",
            "message": "校对正文包含原文未出现的证券代码；请人工核验，不能视作原话。",
        })
    return findings
