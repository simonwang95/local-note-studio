#!/usr/bin/env python3
"""Organize converted Markdown drafts with a local Qwen model."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]

DEFAULTS = {
    "NOTES_DIR": "notes",
    "ORGANIZED_OUTPUT_DIR": "notes/_organized",
    "INDEX_DIR": "indexes",
    "DEFAULT_LLM_API_BASE": "http://127.0.0.1:1234/v1",
    "DEFAULT_LLM_API_KEY": "lm-studio",
    "DEFAULT_LLM_MODEL": "qwen3.6-35b-a3b-nvfp4",
    "QWEN_ORGANIZE_MAX_CHARS": "22000",
    "QWEN_ORGANIZE_OVERLAP_CHARS": "800",
    "QWEN_ORGANIZE_SYNTHESIS_MAX_CHARS": "28000",
    "QWEN_ORGANIZE_TIMEOUT_SECONDS": "300",
    "QWEN_ORGANIZE_MAX_RETRIES": "2",
    "QWEN_ORGANIZE_RETRY_DELAY": "3",
    "QWEN_ORGANIZE_COOLDOWN_DELAY": "",
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
    if not values.get("QWEN_ORGANIZE_COOLDOWN_DELAY"):
        values["QWEN_ORGANIZE_COOLDOWN_DELAY"] = values.get("COOLDOWN_DELAY", "0")
    return values


def rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def today() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(text: str, fallback: str = "untitled") -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text).strip(" .-_")
    return text[:120] or fallback


def yaml_scalar(value: Any) -> str:
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
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


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


def title_from(markdown: str, meta: dict[str, Any], path: pathlib.Path) -> str:
    if meta.get("title"):
        return str(meta["title"])
    match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    return match.group(1).strip() if match else path.stem


def note_type_for(source_type: str) -> str:
    if source_type == "pdf":
        return "paper-note"
    if source_type == "lmstudio-conversation":
        return "chat-note"
    if source_type == "docx":
        return "document-note"
    if source_type in {"webpage", "wechat-article"}:
        return "article-note"
    if source_type in {"video", "bilibili"}:
        return "video-note"
    return "organized-note"


def is_retryable_http_status(status_code: int) -> bool:
    return status_code in (408, 409, 425, 429) or status_code >= 500


def call_chat_completion(cfg: dict[str, str], messages: list[dict[str, str]]) -> str:
    url = f"{cfg['DEFAULT_LLM_API_BASE'].rstrip('/')}/chat/completions"
    payload = {
        "model": cfg["DEFAULT_LLM_MODEL"],
        "messages": messages,
        "temperature": 0.15,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = cfg.get("DEFAULT_LLM_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    timeout = int(cfg["QWEN_ORGANIZE_TIMEOUT_SECONDS"])
    retry_count = max(0, int(cfg.get("QWEN_ORGANIZE_MAX_RETRIES") or 0))
    retry_delay = max(0.0, float(cfg.get("QWEN_ORGANIZE_RETRY_DELAY") or 0))
    last_error: Exception | None = None
    for attempt in range(1, retry_count + 2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return str(data["choices"][0]["message"]["content"]).strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail}")
            if not is_retryable_http_status(exc.code) or attempt > retry_count:
                raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"LLM connection failed: {exc.reason}")
            if attempt > retry_count:
                raise last_error from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected LLM response: {data}") from exc
        wait = retry_delay * (2 ** (attempt - 1))
        print(f"LLM request failed ({attempt}/{retry_count + 1}): {last_error}", file=sys.stderr)
        print(f"retrying in {wait:g}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"LLM request failed after retries: {last_error}")


def chunk_text(text: str, max_chars: int, overlap_chars: int = 0) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    overlap_chars = max(0, min(overlap_chars, max_chars // 3))
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        hard_end = min(length, start + max_chars)
        end = hard_end
        if hard_end < length:
            min_boundary = start + max(max_chars // 2, max_chars - max(overlap_chars * 2, 1))
            candidates = [
                text.rfind("\n## ", min_boundary, hard_end),
                text.rfind("\n\n", min_boundary, hard_end),
                text.rfind("\n", min_boundary, hard_end),
                text.rfind("。", min_boundary, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        next_start = max(0, end - overlap_chars)
        if next_start <= start:
            next_start = end
        start = next_start
    return [chunk for chunk in chunks if chunk]


def normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def merge_duplicate_h2_sections(markdown: str) -> str:
    intro: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_title:
                sections.append((current_title, current_lines))
            elif current_lines:
                intro.extend(current_lines)
            current_title = line
            current_lines = []
        else:
            current_lines.append(line)
    if current_title:
        sections.append((current_title, current_lines))
    elif current_lines:
        intro.extend(current_lines)

    if not sections:
        return normalize_markdown(markdown)

    merged: dict[str, list[str]] = {}
    order: list[str] = []
    for title, lines in sections:
        body = normalize_markdown("\n".join(lines))
        if title not in merged:
            merged[title] = []
            order.append(title)
        if body and body not in merged[title]:
            merged[title].append(body)

    output: list[str] = []
    intro_text = normalize_markdown("\n".join(intro))
    if intro_text:
        output.append(intro_text)
    for title in order:
        output.append(title)
        output.append("\n\n".join(merged[title]).strip())
    return normalize_markdown("\n\n".join(part for part in output if part.strip()))


def organize_chunk(title: str, source_path: str, chunk: str, index: int, total: int, source_type: str, cfg: dict[str, str]) -> str:
    system = (
        "你是本地知识库整理助手。你负责把源文件转换草稿整理成 Obsidian 兼容 Markdown。"
        "必须忠于材料，不编造。需要区分原文观点、你的整理和待核验信息。"
        "遇到 PDF 公式、表格、符号缺损时，用 `[公式待核验]`、`[表格待核验]`、`[符号待核验]` 标注。"
    )
    pdf_translation_requirement = ""
    if source_type == "pdf":
        pdf_translation_requirement = (
            "\n- 这是论文材料，必须在当前分块整理末尾加入 `## 全文翻译`，"
            "按当前分块的论文正文或按页抽取文本顺序保留可读中文翻译；"
            "不要翻译转换说明、待整理提示、已有整理草稿等元信息，也不要把翻译压缩成摘要。"
        )
    user = f"""请整理下面的 Markdown 草稿分块。

标题：{title}
来源：{source_path}
来源类型：{source_type}
分块：{index}/{total}

输出要求：
- 只输出 Markdown 正文，不要包含 YAML frontmatter。
- 面向长期复习，保留关键概念、论证链条、数据、结论和待核验点。
- 结构建议：`## 速读摘要`、`## 核心观点`、`## 思维导图`、`## 结构化笔记`、`## 关键概念`、`## 待核验`。
- `## 思维导图` 使用 Markdown 缩进列表，覆盖当前分块的核心结构。
- 如果这是对话材料，提炼问题、结论、可沉淀知识和后续行动。
- 如果这是论文材料，提炼摘要、方法、实验、贡献、局限和公式线索。
- 分块之间可能包含少量重叠上下文；重叠部分仅用于衔接，不要重复沉淀为新信息。
{pdf_translation_requirement}

草稿分块：

{chunk}
"""
    return normalize_markdown(call_chat_completion(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}]))


def synthesize_text(title: str, source_path: str, joined: str, cfg: dict[str, str], source_type: str = "") -> str:
    system = (
        "你是本地知识库总编。请把多个分块整理综合成一篇不重复、层次清晰、"
        "适合 Obsidian 长期保存的正式 Markdown 笔记。不要编造来源中没有的信息。"
    )
    pdf_translation_requirement = ""
    if source_type == "pdf":
        pdf_translation_requirement = (
            "\n- 这是论文材料，最终文件末尾必须保留 `## 全文翻译`。"
            "请按分块顺序合并各分块的 `## 全文翻译`，对 overlap 重叠内容去重，"
            "但不要把全文翻译改写成摘要。"
        )
    user = f"""请综合下面的分块整理，生成一篇正式知识笔记。

标题：{title}
来源：{source_path}

输出要求：
- 只输出 Markdown 正文，不要 YAML frontmatter。
- 推荐结构：`## 一句话概括`、`## 速读摘要`、`## 核心观点`、`## 思维导图`、`## 结构化笔记`、`## 关键概念`、`## 待核验`、`## 复习清单`。
- `## 思维导图` 使用 Markdown 缩进列表，综合全文结构，合并分块导图并去重。
- 合并重复内容，保留关键数据、公式线索、结论和不确定性。
- 分块之间可能包含少量重叠上下文；请去重后综合，不要把重叠内容重复写入。
{pdf_translation_requirement}

分块整理：

{joined}
"""
    return normalize_markdown(call_chat_completion(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}]))


def synthesize_chunks(title: str, source_path: str, chunk_notes: list[str], cfg: dict[str, str], source_type: str = "", depth: int = 0) -> str:
    if len(chunk_notes) == 1:
        return normalize_markdown(chunk_notes[0])
    joined = "\n\n".join(
        f"## 分块整理 {index}\n\n{note}" for index, note in enumerate(chunk_notes, 1)
    )
    max_chars = int(cfg["QWEN_ORGANIZE_SYNTHESIS_MAX_CHARS"])
    overlap_chars = int(cfg.get("QWEN_ORGANIZE_OVERLAP_CHARS") or 0)
    if len(joined) <= max_chars:
        return synthesize_text(title, source_path, joined, cfg, source_type)
    batches = chunk_text(joined, max_chars, overlap_chars)
    partials = [
        synthesize_text(f"{title} - 综合批次 {index}/{len(batches)}", source_path, batch, cfg, source_type)
        for index, batch in enumerate(batches, 1)
    ]
    if depth >= 2:
        return normalize_markdown("\n\n".join(partials))
    return synthesize_chunks(title, source_path, partials, cfg, source_type, depth + 1)


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_manifest_item(manifest: dict[str, Any], draft_path: pathlib.Path) -> dict[str, Any] | None:
    draft_rel = rel(draft_path)
    for item in manifest.get("items", []):
        if item.get("output_path") == draft_rel or item.get("organized_output_path") == draft_rel:
            return item
    return None


def build_output_path(output_dir: pathlib.Path, title: str, source_type: str) -> pathlib.Path:
    prefix = {
        "pdf": "PAPER",
        "lmstudio-conversation": "CHAT",
        "docx": "DOCX",
        "webpage": "WEB",
        "wechat-article": "WECHAT",
        "video": "VIDEO",
        "bilibili": "BILI",
    }.get(source_type, "NOTE")
    return output_dir / f"{prefix}-{slugify(title)}.md"


def organize_file(draft_path: pathlib.Path, output_dir: pathlib.Path, cfg: dict[str, str]) -> tuple[pathlib.Path, dict[str, Any]]:
    markdown = draft_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(markdown)
    title = title_from(body, meta, draft_path)
    source_type = str(meta.get("source_type") or "markdown")
    source_path = str(meta.get("source_path") or "")
    source_url = str(meta.get("source_url") or "")
    source_ref = source_path or source_url or rel(draft_path)
    draft_hash = sha256_text(markdown)
    chunks = chunk_text(
        body,
        int(cfg["QWEN_ORGANIZE_MAX_CHARS"]),
        int(cfg.get("QWEN_ORGANIZE_OVERLAP_CHARS") or 0),
    )
    chunk_notes = [
        organize_chunk(title, source_ref, chunk, index, len(chunks), source_type, cfg)
        for index, chunk in enumerate(chunks, 1)
    ]
    organized_body = merge_duplicate_h2_sections(normalize_markdown(synthesize_chunks(title, source_ref, chunk_notes, cfg, source_type)))
    output_path = build_output_path(output_dir, title, source_type)
    organized_meta = {
        "title": title,
        "type": note_type_for(source_type),
        "source_type": source_type,
        "source_path": source_path,
        "source_url": source_url,
        "draft_path": rel(draft_path),
        "created": today(),
        "updated": today(),
        "status": "organized",
        "model": cfg["DEFAULT_LLM_MODEL"],
        "tags": ["organized/qwen", f"source/{source_type}"],
        "draft_hash": draft_hash,
        "source_hash": meta.get("source_hash", ""),
    }
    source_trace = "\n\n".join(
        [
            "## 来源追溯",
            f"- 草稿：`{rel(draft_path)}`",
            f"- 原始来源：`{source_ref}`",
        ]
    )
    if source_type == "pdf":
        output_parts = [frontmatter(organized_meta), f"# {title}", source_trace, organized_body]
    else:
        output_parts = [frontmatter(organized_meta), f"# {title}", organized_body, source_trace]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(output_parts).rstrip() + "\n", encoding="utf-8")
    item = {
        "organized_output_path": rel(output_path),
        "organized_status": "organized",
        "organized_at": now_iso(),
        "organize_model": cfg["DEFAULT_LLM_MODEL"],
        "organize_error": "",
        "draft_hash": draft_hash,
    }
    return output_path, item


def manifest_sources(manifest: dict[str, Any]) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for item in manifest.get("items", []):
        output_path = item.get("output_path")
        if item.get("status") == "converted" and output_path:
            paths.append((ROOT / output_path).resolve())
    return paths


def main() -> int:
    cfg = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", help="specific converted Markdown draft to organize")
    parser.add_argument("--from-manifest", action="store_true", help="organize converted drafts from source manifest")
    parser.add_argument("--limit", type=int, default=0, help="maximum number of drafts to organize")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing organized notes")
    parser.add_argument("--output-dir", default=cfg["ORGANIZED_OUTPUT_DIR"], help="organized note output directory")
    args = parser.parse_args()

    manifest_path = (ROOT / cfg["INDEX_DIR"] / "source-manifest.json").resolve()
    manifest = load_manifest(manifest_path)
    output_dir = (ROOT / args.output_dir).resolve()

    if args.source:
        sources = [(ROOT / source).resolve() for source in args.source]
    elif args.from_manifest:
        sources = manifest_sources(manifest)
    else:
        parser.error("provide --source or --from-manifest")

    if args.limit > 0:
        sources = sources[: args.limit]

    organized = 0
    skipped = 0
    failed = 0
    cooldown_delay = float(cfg.get("QWEN_ORGANIZE_COOLDOWN_DELAY") or 0)
    for index, draft_path in enumerate(sources, start=1):
        try:
            if not draft_path.exists():
                raise FileNotFoundError(draft_path)
            markdown = draft_path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(markdown)
            title = title_from(body, meta, draft_path)
            source_type = str(meta.get("source_type") or "markdown")
            planned_output = build_output_path(output_dir, title, source_type)
            manifest_item = find_manifest_item(manifest, draft_path)
            if planned_output.exists() and not args.overwrite:
                if manifest_item is not None and manifest_item.get("organized_status") == "organized":
                    skipped += 1
                    print(f"skip {rel(draft_path)}")
                    continue
            out_path, update = organize_file(draft_path, output_dir, cfg)
            if manifest_item is not None:
                manifest_item.update(update)
            else:
                manifest.setdefault("items", []).append(
                    {
                        "source_path": rel(draft_path),
                        "source_type": "markdown",
                        "source_hash": sha256_text(markdown),
                        "source_size": draft_path.stat().st_size,
                        "output_path": rel(draft_path),
                        "status": "converted",
                        **update,
                    }
                )
            organized += 1
            print(f"organized {rel(draft_path)} -> {rel(out_path)}")
            if cooldown_delay > 0 and index < len(sources):
                print(f"cooldown {cooldown_delay:g}s before next draft")
                time.sleep(cooldown_delay)
        except Exception as exc:
            failed += 1
            manifest_item = find_manifest_item(manifest, draft_path)
            if manifest_item is not None:
                manifest_item.update(
                    {
                        "organized_status": "failed",
                        "organized_at": now_iso(),
                        "organize_model": cfg["DEFAULT_LLM_MODEL"],
                        "organize_error": str(exc),
                    }
                )
            print(f"failed {rel(draft_path)}: {exc}", file=sys.stderr)
    save_manifest(manifest_path, manifest)
    print(f"done organized={organized} skipped={skipped} failed={failed} manifest={rel(manifest_path)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
