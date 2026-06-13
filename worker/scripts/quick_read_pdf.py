#!/usr/bin/env python3
"""Generate quick-read Markdown notes for PDF papers with a local Qwen model."""

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

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - handled at runtime
    PdfReader = None


ROOT = pathlib.Path(__file__).resolve().parents[1]

DEFAULTS = {
    "SOURCE_DIR": "source",
    "INDEX_DIR": "indexes",
    "AI_PAPER_QUICKREAD_DIR": "notes/AI/_quickread/AI_paper",
    "DEFAULT_LLM_API_BASE": "http://127.0.0.1:1234/v1",
    "DEFAULT_LLM_API_KEY": "lm-studio",
    "DEFAULT_LLM_MODEL": "qwen3.6-35b-a3b-nvfp4",
    "QWEN_QUICKREAD_MAX_CHARS": "90000",
    "QWEN_QUICKREAD_MAX_TOKENS": "80000",
    "QWEN_QUICKREAD_TIMEOUT_SECONDS": "1200",
    "QWEN_QUICKREAD_MAX_RETRIES": "2",
    "QWEN_QUICKREAD_RETRY_DELAY": "5",
    "QWEN_QUICKREAD_COOLDOWN_DELAY": "",
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
    if not values.get("QWEN_QUICKREAD_COOLDOWN_DELAY"):
        values["QWEN_QUICKREAD_COOLDOWN_DELAY"] = values.get("COOLDOWN_DELAY", "0")
    return values


def rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def month_value() -> str:
    return dt.date.today().strftime("%Y-%m")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(text: str, fallback: str = "untitled") -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", "-", text).strip(" .-_")
    return text[:120] or fallback


def yaml_scalar(value: Any) -> str:
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


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_manifest(manifest: dict[str, Any], item: dict[str, Any]) -> None:
    items = manifest.setdefault("items", [])
    for index, old in enumerate(items):
        same_source = old.get("source_path") == item.get("source_path")
        same_mode = old.get("mode") == item.get("mode")
        if same_source and same_mode:
            items[index] = {**old, **item}
            return
    items.append(item)


def pdf_metadata_title(reader: Any, path: pathlib.Path) -> str:
    metadata = getattr(reader, "metadata", None) or {}
    title = str(metadata.get("/Title") or "").strip()
    if title and title.lower() not in {"untitled", "none"}:
        return title
    return path.stem


def extract_pdf(path: pathlib.Path) -> tuple[str, int, str]:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed; run with the project Python runtime.")
    reader = PdfReader(str(path))
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - depends on PDF internals
            text = f"[Page {index} extraction failed: {exc}]"
        if text.strip():
            pages.append(f"<!-- page: {index} -->\n{text.strip()}")
    title = pdf_metadata_title(reader, path)
    return title, len(reader.pages), clean_text("\n\n".join(pages))


def build_quickread_prompt(title: str, source_path: str, page_count: int, text: str) -> str:
    return f"""请基于下面论文内容生成一篇中文速读笔记。

论文标题：{title}
来源路径：{source_path}
页数：{page_count}

要求：
1. 用中文输出 Markdown，不要输出思考过程。
2. 先做“翻译式速读”：保留论文问题、方法、实验、结论、局限和关键术语。
3. 必须包含这些二级标题：`## 中文速读`、`## 一句话概括`、`## 速读摘要`、`## 思维导图`、`## 值得精读的理由`、`## 待核验`、`## 全文翻译`。
4. `## 思维导图` 使用 Mermaid mindmap 代码块。
5. `## 全文翻译` 必须放在文件末尾，按原文顺序保留可读中文翻译；公式、表格、图示或引用不确定时，用 `[公式待核验]`、`[表格待核验]`、`[图示待核验]` 标注，不要编造。
6. 如果输入内容疑似被截断，在 `## 待核验` 和 `## 全文翻译` 开头说明。

论文文本：

```text
{text}
```"""


def build_manual_prompt(title: str, source_path: str) -> str:
    return f"""# LM Studio 直读 PDF 速读提示词

在 LM Studio Chat 中上传原始 PDF 后，粘贴下面提示词。

```text
请直接阅读这篇 PDF，输出中文速读笔记。

论文标题：{title}
来源路径：{source_path}

要求：
1. 用中文输出 Markdown，不要输出思考过程。
2. 先做翻译式速读，保留论文核心问题、方法、实验、结论、局限和关键术语。
3. 必须包含这些二级标题：## 中文速读、## 一句话概括、## 速读摘要、## 思维导图、## 值得精读的理由、## 待核验、## 全文翻译。
4. ## 思维导图 使用 Mermaid mindmap 代码块。
5. ## 全文翻译 必须放在文件末尾，按原文顺序保留可读中文翻译；公式、表格、图示或引用不确定时，用 [公式待核验]、[表格待核验]、[图示待核验] 标注，不要编造。
```"""


def is_retryable_http_status(status_code: int) -> bool:
    return status_code in (408, 409, 425, 429, 502, 503, 504) or status_code >= 500


def call_chat_completion(cfg: dict[str, str], prompt: str) -> str:
    url = f"{cfg['DEFAULT_LLM_API_BASE'].rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg["DEFAULT_LLM_MODEL"],
        "messages": [
            {
                "role": "system",
                "content": "你是论文速读助手。请把论文材料整理为清晰、可靠、适合 Obsidian 保存的中文 Markdown。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    max_tokens = int(cfg.get("QWEN_QUICKREAD_MAX_TOKENS") or 0)
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = cfg.get("DEFAULT_LLM_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = int(cfg["QWEN_QUICKREAD_TIMEOUT_SECONDS"])
    retry_count = max(0, int(cfg.get("QWEN_QUICKREAD_MAX_RETRIES") or 0))
    retry_delay = max(0.0, float(cfg.get("QWEN_QUICKREAD_RETRY_DELAY") or 0))
    last_error: Exception | None = None

    for attempt in range(1, retry_count + 2):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
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


def output_dir_for(cfg: dict[str, str], month: str, output_dir: str | None) -> pathlib.Path:
    if output_dir:
        return (ROOT / output_dir).resolve() if not pathlib.Path(output_dir).is_absolute() else pathlib.Path(output_dir)
    return ROOT / cfg["AI_PAPER_QUICKREAD_DIR"] / month


def write_quickread(
    source: pathlib.Path,
    out_dir: pathlib.Path,
    cfg: dict[str, str],
    *,
    overwrite: bool,
    prompt_only: bool,
) -> pathlib.Path:
    source = source.resolve()
    title, page_count, text = extract_pdf(source)
    slug = slugify(title or source.stem)
    prefix = "PROMPT" if prompt_only else "QR"
    out_path = out_dir / f"{prefix}-{slug}.md"
    if out_path.exists() and not overwrite:
        print(f"skip existing: {rel(out_path)}")
        return out_path

    out_dir.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(source)
    truncated = False
    input_chars = len(text)
    max_chars = int(cfg["QWEN_QUICKREAD_MAX_CHARS"])
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        truncated = True
    if not text.strip() and not prompt_only:
        raise RuntimeError(f"no extractable text: {source}")

    mode = "manual-direct-pdf-prompt" if prompt_only else "extracted-text"
    if prompt_only:
        body = build_manual_prompt(title, rel(source))
        status = "prompt-only"
    else:
        prompt = build_quickread_prompt(title, rel(source), page_count, text)
        body = call_chat_completion(cfg, prompt)
        status = "quickread"

    meta = {
        "title": title,
        "type": "paper-quickread",
        "source_type": "pdf",
        "source_path": rel(source),
        "source_hash": source_hash,
        "created": now_iso(),
        "updated": now_iso(),
        "status": status,
        "quickread_mode": mode,
        "model": cfg["DEFAULT_LLM_MODEL"],
        "page_count": page_count,
        "input_chars": input_chars,
        "used_chars": len(text),
        "truncated": truncated,
        "tags": ["quickread/qwen", "source/pdf", "domain/ai"],
    }
    content = f"{frontmatter(meta)}\n\n# {title}\n\n"
    if truncated and not prompt_only:
        content += f"> 速读输入已从 {input_chars} 字符截断到 {len(text)} 字符，正式引用请回看原 PDF。\n\n"
    content += body.strip() + "\n"
    out_path.write_text(content, encoding="utf-8")

    manifest_path = ROOT / cfg["INDEX_DIR"] / "quickread-manifest.json"
    manifest = load_manifest(manifest_path)
    update_manifest(
        manifest,
        {
            "source_path": rel(source),
            "source_hash": source_hash,
            "output_path": rel(out_path),
            "title": title,
            "mode": mode,
            "status": status,
            "model": cfg["DEFAULT_LLM_MODEL"],
            "created": meta["created"],
            "updated": meta["updated"],
            "page_count": page_count,
            "input_chars": input_chars,
            "used_chars": len(text),
            "truncated": truncated,
        },
    )
    save_manifest(manifest_path, manifest)
    print(f"wrote: {rel(out_path)}")
    return out_path


def resolve_sources(args: argparse.Namespace, cfg: dict[str, str]) -> list[pathlib.Path]:
    sources = [pathlib.Path(item) for item in args.source]
    if args.all:
        source_dir = pathlib.Path(args.source_dir or cfg["SOURCE_DIR"])
        if not source_dir.is_absolute():
            source_dir = ROOT / source_dir
        sources.extend(sorted(source_dir.glob("*.pdf")))
    if not sources:
        raise SystemExit("No PDF source provided. Use --source FILE or --all --source-dir DIR.")
    return [(ROOT / path).resolve() if not path.is_absolute() else path.resolve() for path in sources]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", default=[], help="PDF file to quick-read. Repeatable.")
    parser.add_argument("--source-dir", default="source/AI_paper", help="PDF directory used with --all.")
    parser.add_argument("--all", action="store_true", help="Process all PDFs in --source-dir.")
    parser.add_argument("--month", default=month_value(), help="Output month, default: current YYYY-MM.")
    parser.add_argument("--output-dir", default="", help="Override output directory.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing quick-read notes.")
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Write a prompt for LM Studio direct-PDF chat instead of calling the API.",
    )
    args = parser.parse_args(argv)

    cfg = config()
    out_dir = output_dir_for(cfg, args.month, args.output_dir or None)
    sources = resolve_sources(args, cfg)
    cooldown = max(0.0, float(cfg.get("QWEN_QUICKREAD_COOLDOWN_DELAY") or 0))
    for index, source in enumerate(sources, start=1):
        write_quickread(source, out_dir, cfg, overwrite=args.overwrite, prompt_only=args.prompt_only)
        if cooldown and index < len(sources):
            print(f"cooldown {cooldown:g}s")
            time.sleep(cooldown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
