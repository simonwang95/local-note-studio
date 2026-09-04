"""Normalize and validate Markdown mind-map lists.

Model output can use tabs, full-width spaces, four-space nesting, or mixed list
markers.  Notes in this project use a canonical two-space ``-`` list so that
Obsidian renders the hierarchy consistently.
"""

from __future__ import annotations

import re


_LIST_ITEM_RE = re.compile(
    r"^([ \t\u3000]*)(?:[-+*\u2022\u00b7]|\d+[.)])(?:[ \t\u3000]+)(\S.*)$"
)
_MINDMAP_HEADING_RE = re.compile(r"(?m)^(#{2,6})\s+思维导图\s*$")

MINDMAP_HIERARCHY_INSTRUCTION = (
    "使用多层 Markdown 列表覆盖全文核心结构：顶层主题不缩进，子主题缩进 2 个空格，"
    "具体要点再缩进 2 个空格；每个顶层主题都必须形成“主题 → 子主题 → 具体要点”三级结构。"
    "只使用 `-` 列表符号，不要平铺。示例：`- 市场状态`、`  - 指数变化`、"
    "`    - 缩量回调`（三行分别占一行）。输出前检查所有顶层主题都有三级层级。"
)
MINDMAP_SECTION_START = "[[LNS_SECTION:mindmap]]"
MINDMAP_SECTION_END = "[[/LNS_SECTION:mindmap]]"


def _indent_width(whitespace: str) -> int:
    return sum(2 if char in {"\t", "\u3000"} else 1 for char in whitespace)


def _strip_surrounding_fence(markdown: str) -> str:
    lines = markdown.strip().splitlines()
    if len(lines) >= 2 and re.fullmatch(r"```(?:markdown|md)?\s*", lines[0], flags=re.IGNORECASE):
        if lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return markdown.strip()


def normalize_mindmap_list(markdown: str) -> str:
    """Return a canonical two-space, dash-marker version of a mind-map list."""
    markdown = _strip_surrounding_fence(markdown.replace("\r\n", "\n").replace("\r", "\n"))
    lines = markdown.splitlines()
    parsed: list[tuple[int, str] | None] = []
    widths: set[int] = set()
    for line in lines:
        match = _LIST_ITEM_RE.match(line)
        if not match:
            parsed.append(None)
            continue
        width = _indent_width(match.group(1))
        widths.add(width)
        parsed.append((width, match.group(2).rstrip()))

    if not widths:
        return markdown.strip()

    # Models disagree on whether a nested list should use 2 or 4 spaces and
    # occasionally mix the two. Preserve the relative hierarchy while mapping
    # every distinct indentation width to one canonical level.
    base_width = min(widths)
    level_for_width = {
        width: level for level, width in enumerate(sorted(width - base_width for width in widths))
    }
    output: list[str] = []
    for line, item in zip(lines, parsed):
        if item is None:
            output.append(line.rstrip())
            continue
        width, text = item
        level = level_for_width[width - base_width]
        output.append(f"{'  ' * level}- {text}")
    return "\n".join(output).strip()


def mindmap_item_texts(markdown: str) -> list[str]:
    """Return list-item text in source order, ignoring marker and indentation."""
    body = extract_mindmap_section(markdown) or markdown
    normalized = normalize_mindmap_list(body)
    items: list[str] = []
    for line in normalized.splitlines():
        match = _LIST_ITEM_RE.match(line)
        if match:
            items.append(match.group(2).strip())
    return items


def extract_mindmap_contract(response: str) -> str:
    """Extract and normalize a mind map from the strict LNS section contract."""
    pattern = re.compile(
        rf"{re.escape(MINDMAP_SECTION_START)}\s*(.*?)\s*{re.escape(MINDMAP_SECTION_END)}",
        flags=re.DOTALL,
    )
    match = pattern.search(response)
    if not match:
        return ""
    return normalize_mindmap_list(match.group(1))


def mindmap_has_required_hierarchy(markdown: str) -> bool:
    """Require every top-level branch to contain a child and a grandchild."""
    normalized = normalize_mindmap_list(markdown)
    levels: list[int] = []
    for line in normalized.splitlines():
        match = _LIST_ITEM_RE.match(line)
        if match:
            levels.append(len(match.group(1)) // 2)
    if len(levels) < 3 or not levels or levels[0] != 0:
        return False
    if any(level > previous + 1 for previous, level in zip(levels, levels[1:])):
        return False
    root_indexes = [index for index, level in enumerate(levels) if level == 0]
    for position, start in enumerate(root_indexes):
        end = root_indexes[position + 1] if position + 1 < len(root_indexes) else len(levels)
        if max(levels[start:end], default=0) < 2:
            return False
    return True


def extract_mindmap_section(markdown: str) -> str:
    """Extract a mind-map section body at Markdown heading levels 2 through 6."""
    bounds = _mindmap_section_bounds(markdown)
    if not bounds:
        return ""
    start, end = bounds
    return markdown[start:end].strip()


def _mindmap_section_bounds(markdown: str) -> tuple[int, int] | None:
    heading = _MINDMAP_HEADING_RE.search(markdown)
    if not heading:
        return None
    level = len(heading.group(1))
    start = heading.end()
    if markdown[start:start + 1] == "\n":
        start += 1
    next_heading = re.search(rf"(?m)^#{{2,{level}}}\s+", markdown[start:])
    end = start + next_heading.start() if next_heading else len(markdown)
    return start, end


def replace_mindmap_section(markdown: str, mindmap: str) -> str:
    """Replace the body of the first mind-map section."""
    bounds = _mindmap_section_bounds(markdown)
    if not bounds:
        return markdown
    start, end = bounds
    prefix = markdown[:start]
    suffix = markdown[end:].lstrip("\n")
    result = prefix + mindmap.strip()
    if suffix:
        result += "\n\n" + suffix
    return result.rstrip()


def normalize_mindmap_section(markdown: str) -> str:
    """Normalize only the ``## 思维导图`` section of a full Markdown note."""
    mindmap = extract_mindmap_section(markdown)
    if not mindmap:
        return markdown
    return replace_mindmap_section(markdown, normalize_mindmap_list(mindmap))
