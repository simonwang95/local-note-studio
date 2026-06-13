#!/usr/bin/env python3
"""Convert local source files into Markdown drafts.

The first implementation supports PDF files through pypdf and LM Studio
conversation JSON exports. It intentionally produces traceable drafts rather
than final organized notes; Qwen can refine these drafts later.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as html_lib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - handled at runtime
    PdfReader = None

try:
    from lxml import html as lxml_html
except Exception:  # pragma: no cover - handled at runtime
    lxml_html = None


ROOT = pathlib.Path(__file__).resolve().parents[1]

DOCX_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


DEFAULTS = {
    "NOTES_DIR": "notes",
    "SOURCE_DIR": "source",
    "SAMPLE_OUTPUT_DIR": "notes/_samples/source-conversion",
    "INDEX_DIR": "indexes",
    "DEFAULT_LLM_API_BASE": "http://127.0.0.1:1234/v1",
    "DEFAULT_LLM_API_KEY": "lm-studio",
    "DEFAULT_LLM_MODEL": "qwen3.6-35b-a3b-nvfp4",
    "SOURCE_CONVERSION_SAMPLE_LIMIT_PDF": "2",
    "SOURCE_CONVERSION_SAMPLE_LIMIT_JSON": "2",
    "SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX": "2",
    "SOURCE_CONVERSION_MAX_PDF_PAGES": "8",
    "QWEN_PDF_POLISH_MAX_CHARS": "18000",
    "QWEN_PDF_POLISH_TIMEOUT_SECONDS": "180",
    "QWEN_PDF_POLISH_COOLDOWN_DELAY": "",
    "QWEN_PDF_POLISH_OVERLAP_PAGES": "1",
    "WEB_FETCH_TIMEOUT_SECONDS": "30",
    "WEB_DOWNLOAD_ASSETS": "true",
    "WEB_ASSET_MAX_BYTES": str(50 * 1024 * 1024),
    "WEB_USER_AGENT": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def config() -> dict[str, str]:
    values = dict(DEFAULTS)
    values.update(load_env_file(ROOT / "env.local"))
    for key in DEFAULTS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    if not values.get("QWEN_PDF_POLISH_COOLDOWN_DELAY"):
        values["QWEN_PDF_POLISH_COOLDOWN_DELAY"] = values.get("COOLDOWN_DELAY", "0")
    return values


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today() -> str:
    return dt.date.today().isoformat()


def ms_to_iso(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    try:
        return dt.datetime.fromtimestamp(value / 1000).replace(microsecond=0).isoformat()
    except Exception:
        return ""


def unix_seconds_to_iso(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    try:
        return dt.datetime.fromtimestamp(timestamp).replace(microsecond=0).isoformat()
    except Exception:
        return ""


def model_label(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("indexedModelIdentifier") or value.get("identifier") or "")
    return ""


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_manifest(manifest: dict[str, Any], item: dict[str, Any]) -> None:
    items = manifest.setdefault("items", [])
    source_path = item.get("source_path", "")
    source_url = item.get("source_url", "")
    for index, old in enumerate(items):
        same_path = bool(source_path) and old.get("source_path") == source_path
        same_url = bool(source_url) and old.get("source_url") == source_url
        if same_path or same_url:
            items[index] = {**old, **item}
            return
    items.append(item)


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def call_chat_completion(cfg: dict[str, str], messages: list[dict[str, str]]) -> str:
    api_base = cfg["DEFAULT_LLM_API_BASE"].rstrip("/")
    url = f"{api_base}/chat/completions"
    payload = {
        "model": cfg["DEFAULT_LLM_MODEL"],
        "messages": messages,
        "temperature": 0.1,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = cfg.get("DEFAULT_LLM_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    timeout = int(cfg["QWEN_PDF_POLISH_TIMEOUT_SECONDS"])
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM connection failed: {exc.reason}") from exc

    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected LLM response: {data}") from exc


def chunk_pages(page_texts: list[tuple[int, str]], max_chars: int, overlap_pages: int = 0) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    current_pages: list[tuple[str, str]] = []
    current_size = 0
    for page_number, text in page_texts:
        part = f"### Page {page_number}\n\n{text or '本页未抽取到可读文本。'}"
        if current_pages and current_size + len(part) > max_chars:
            chunks.append(
                (
                    ", ".join(label for label, _ in current_pages),
                    "\n\n".join(page_part for _, page_part in current_pages),
                )
            )
            overlap = current_pages[-overlap_pages:] if overlap_pages > 0 else []
            current_pages = list(overlap)
            current_size = sum(len(page_part) for _, page_part in current_pages)
        current_pages.append((str(page_number), part))
        current_size += len(part)
    if current_pages:
        chunks.append(
            (
                ", ".join(label for label, _ in current_pages),
                "\n\n".join(page_part for _, page_part in current_pages),
            )
        )
    return chunks


def clean_qwen_markdown(content: str) -> str:
    text = content.replace("\x00", "").strip()
    if not text:
        return text
    text = re.sub(r"^\s*<answer>\s*", "", text, flags=re.I)
    leak_markers = [
        "Self-Correction",
        "Mental Refinement",
        "Draft Generation",
        "Output Generation",
        "I will ",
        "I'll ",
        "Let's ",
    ]
    head = text[:2500]
    if any(marker in head for marker in leak_markers):
        match = re.search(r"(?m)^(#{1,4}\s+\S.*)$", text)
        if match:
            text = text[match.start() :].strip()
    return text


def qwen_polish_pdf(title: str, path: pathlib.Path, page_texts: list[tuple[int, str]], cfg: dict[str, str]) -> str:
    system = (
        "你是严谨的论文 PDF 转 Markdown 助手。你的任务是基于 PDF 文本抽取结果，"
        "整理为 Obsidian 兼容 Markdown。必须保留原意，不要编造抽取文本中没有的信息。"
        "公式只在文本足够明确时转为 LaTeX；如果公式、表格或符号因 PDF 抽取损坏而无法确定，"
        "用 `[公式待核验]`、`[表格待核验]` 或 `[符号待核验]` 标注。"
        "禁止输出你的分析、计划、自我检查、草稿过程或任何非正文说明。"
    )
    chunks = chunk_pages(
        page_texts,
        int(cfg["QWEN_PDF_POLISH_MAX_CHARS"]),
        int(cfg.get("QWEN_PDF_POLISH_OVERLAP_PAGES") or 0),
    )
    cooldown_delay = float(cfg.get("QWEN_PDF_POLISH_COOLDOWN_DELAY") or 0)
    polished: list[str] = []
    for index, (labels, chunk) in enumerate(chunks, 1):
        user = f"""请把下面论文 PDF 抽取文本整理成清晰 Markdown。

论文标题：{title}
源文件：{rel(path)}
页码范围：{labels}

输出要求：
- 只输出 Markdown 正文，不要解释你的工作过程。
- 保留章节层级、关键术语、公式含义、实验数据和引用线索。
- 公式可确定时使用 `$...$` 或 `$$...$$`。
- 不确定的公式、表格、符号必须显式标注待核验，不要猜。
- 不要把页眉页脚、版权水印和明显重复噪声当作正文重点。

PDF 抽取文本：

{chunk}
"""
        content = clean_qwen_markdown(call_chat_completion(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}]))
        polished.append(f"### Qwen 分块 {index}（Page {labels}）\n\n{content}")
        if cooldown_delay > 0 and index < len(chunks):
            print(f"cooldown {cooldown_delay:g}s before next PDF chunk", file=sys.stderr)
            time.sleep(cooldown_delay)
    return "\n\n".join(polished)


def tag_name(node: Any) -> str:
    tag = getattr(node, "tag", "")
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def compact_text(text: str) -> str:
    text = html_lib.unescape(text or "").replace("\xa0", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def text_content(node: Any) -> str:
    try:
        return compact_text(" ".join(part for part in node.itertext() if part))
    except Exception:
        return ""


def meta_content(doc: Any, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for meta in doc.xpath("//meta[@content]"):
        keys = [
            str(meta.get("name") or "").lower(),
            str(meta.get("property") or "").lower(),
            str(meta.get("itemprop") or "").lower(),
        ]
        if any(key in wanted for key in keys):
            return compact_text(str(meta.get("content") or ""))
    return ""


def first_xpath_text(doc: Any, expressions: list[str]) -> str:
    for expression in expressions:
        for node in doc.xpath(expression):
            value = text_content(node) if hasattr(node, "itertext") else compact_text(str(node))
            if value:
                return value
    return ""


def decode_js_literal(value: str) -> str:
    if "\\" not in value:
        return value
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except Exception:
        return value


def regex_group(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.S)
    if not match:
        return ""
    return compact_text(decode_js_literal(match.group(1)))


def fetch_webpage(url: str, cfg: dict[str, str]) -> tuple[str, bytes, str]:
    headers = {
        "User-Agent": cfg["WEB_USER_AGENT"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    request = urllib.request.Request(url, headers=headers)
    timeout = int(cfg["WEB_FETCH_TIMEOUT_SECONDS"])
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.geturl()
    return body.decode(charset, errors="replace"), body, final_url


def parse_html(raw_html: str) -> Any:
    if lxml_html is None:
        raise RuntimeError("lxml is not available in the current Python environment")
    return lxml_html.fromstring(raw_html)


def embedded_wechat_content(raw_html: str) -> Any | None:
    if lxml_html is None:
        return None
    match = re.search(r"content_noencode:\s*'((?:\\.|[^'])*)'", raw_html, flags=re.S)
    if not match:
        return None
    decoded = decode_js_literal(match.group(1)).replace("\\/", "/")
    try:
        return lxml_html.fragment_fromstring(decoded, create_parent="div")
    except Exception:
        return None


def is_wechat_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host.endswith("mp.weixin.qq.com")


def select_article_node(doc: Any, raw_html: str, url: str) -> Any:
    if is_wechat_url(url):
        nodes = doc.xpath("//*[@id='js_content']")
        if nodes and len(text_content(nodes[0])) > 100:
            return nodes[0]
        embedded = embedded_wechat_content(raw_html)
        if embedded is not None and len(text_content(embedded)) > 100:
            return embedded

    candidates: list[Any] = []
    for expression in [
        "//article",
        "//main",
        "//*[@role='main']",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' article ')]",
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' content ')]",
        "//*[contains(concat(' ', normalize-space(@id), ' '), ' content ')]",
        "//body",
    ]:
        candidates.extend(node for node in doc.xpath(expression) if hasattr(node, "itertext"))
    if not candidates:
        return doc
    return max(candidates, key=lambda node: len(text_content(node)))


def absolute_url(raw: str, base_url: str) -> str:
    value = html_lib.unescape(raw or "").strip()
    if not value or value.startswith(("data:", "javascript:")):
        return ""
    if value.startswith("//"):
        return "https:" + value
    return urllib.parse.urljoin(base_url, value)


def hidden_node(node: Any) -> bool:
    tag = tag_name(node)
    if tag in {"script", "style", "noscript", "svg", "canvas", "iframe", "mp-common-profile"}:
        return True
    if str(getattr(node, "get", lambda _key, _default=None: "")("id", "") or "") == "js_content":
        return False
    style = str(getattr(node, "get", lambda _key, _default=None: "")("style", "") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def image_markdown(node: Any, base_url: str) -> str:
    src = ""
    for attr in ["data-src", "data-original", "src"]:
        src = absolute_url(str(node.get(attr) or ""), base_url)
        if src:
            break
    if not src:
        return ""
    alt = compact_text(str(node.get("alt") or node.get("title") or "image"))
    return f"![{alt}]({src})"


def inline_markdown(node: Any, base_url: str) -> str:
    tag = tag_name(node)
    if hidden_node(node):
        return ""
    if tag == "br":
        return "\n"
    if tag == "img":
        return image_markdown(node, base_url)

    parts = [node.text or ""]
    for child in node:
        parts.append(inline_markdown(child, base_url))
        if child.tail:
            parts.append(child.tail)
    content = html_lib.unescape("".join(parts)).replace("\xa0", " ")
    content = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", content)

    if tag in {"strong", "b"}:
        label = compact_text(content)
        if label and not label.startswith("!["):
            return f"**{label}**"
    if tag in {"em", "i"}:
        label = compact_text(content)
        if label and not label.startswith("!["):
            return f"*{label}*"
    if tag == "code":
        label = compact_text(content).replace("`", "\\`")
        return f"`{label}`" if label else ""
    if tag == "a":
        href = absolute_url(str(node.get("href") or ""), base_url)
        label = compact_text(content)
        if href and label:
            return f"[{label}]({href})"
    return content


BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "body",
    "div",
    "figure",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}


def has_block_child(node: Any) -> bool:
    return any(tag_name(child) in BLOCK_TAGS for child in node)


def paragraph_markdown(node: Any, base_url: str) -> str:
    content = inline_markdown(node, base_url)
    content = re.sub(r"(\))(?=!\[)", r"\1\n\n", content)
    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return compact_text(content)


def table_markdown(node: Any) -> str:
    rows: list[list[str]] = []
    for tr in node.xpath(".//tr"):
        cells = []
        for cell in tr.xpath("./th|./td"):
            value = text_content(cell).replace("|", "\\|")
            cells.append(value)
        if cells:
            rows.append(cells)
    if not rows:
        return text_content(node)
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    body_rows = rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body_rows)
    return "\n".join(lines)


def block_markdown(node: Any, base_url: str) -> list[str]:
    if hidden_node(node):
        return []
    tag = tag_name(node)

    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = min(int(tag[1]), 6)
        text = paragraph_markdown(node, base_url)
        return [f"{'#' * level} {text}"] if text else []

    if tag == "img":
        text = image_markdown(node, base_url)
        return [text] if text else []

    if tag == "pre":
        text = text_content(node)
        return [f"```text\n{text}\n```"] if text else []

    if tag == "table":
        text = table_markdown(node)
        return [text] if text else []

    if tag in {"ul", "ol"}:
        blocks = []
        ordered = tag == "ol"
        for index, li in enumerate([child for child in node if tag_name(child) == "li"], 1):
            item_blocks = block_markdown(li, base_url)
            item_text = "\n".join(item_blocks) if item_blocks else paragraph_markdown(li, base_url)
            item_lines = [line for line in item_text.splitlines() if line.strip()]
            if not item_lines:
                continue
            prefix = f"{index}." if ordered else "-"
            first, rest = item_lines[0], item_lines[1:]
            block = [f"{prefix} {first}"]
            block.extend(f"  {line}" for line in rest)
            blocks.append("\n".join(block))
        return blocks

    if tag == "li":
        if has_block_child(node):
            blocks = []
            leading = compact_text(node.text or "")
            if leading:
                blocks.append(leading)
            for child in node:
                blocks.extend(block_markdown(child, base_url))
                tail = compact_text(child.tail or "")
                if tail:
                    blocks.append(tail)
            return blocks
        text = paragraph_markdown(node, base_url)
        return [text] if text else []

    if tag == "blockquote":
        child_blocks = []
        for child in node:
            child_blocks.extend(block_markdown(child, base_url))
        if not child_blocks:
            text = paragraph_markdown(node, base_url)
            child_blocks = [text] if text else []
        quoted = []
        for block in child_blocks:
            quoted.append("\n".join(f"> {line}" if line else ">" for line in block.splitlines()))
        return quoted

    if tag in {"p", "figcaption"} or not has_block_child(node):
        text = paragraph_markdown(node, base_url)
        return [text] if text else []

    blocks = []
    leading = compact_text(node.text or "")
    if leading:
        blocks.append(leading)
    for child in node:
        blocks.extend(block_markdown(child, base_url))
        tail = compact_text(child.tail or "")
        if tail:
            blocks.append(tail)
    return blocks


def normalize_markdown(markdown: str) -> str:
    markdown = markdown.replace("\xa0", " ")
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def webpage_markdown(article_node: Any, base_url: str) -> str:
    return normalize_markdown("\n\n".join(block_markdown(article_node, base_url)))


IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")


def markdown_image_urls(markdown: str) -> list[str]:
    urls = []
    for match in IMAGE_LINK_RE.finditer(markdown):
        url = match.group(2).strip()
        if urllib.parse.urlparse(url).scheme in {"http", "https"}:
            urls.append(url)
    return urls


def asset_extension(url: str, content_type: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ["wx_fmt", "format"]:
        value = (query.get(key) or [""])[0].lower().split(";", 1)[0]
        if value:
            if value == "jpeg":
                value = "jpg"
            if re.fullmatch(r"[a-z0-9]+", value):
                return f".{value}"
    suffix = pathlib.PurePosixPath(parsed.path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{2,6}", suffix):
        return ".jpg" if suffix == ".jpeg" else suffix
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
    }
    return mapping.get(content_type.lower().split(";", 1)[0], ".bin")


def fetch_asset(url: str, referer: str, cfg: dict[str, str]) -> tuple[bytes, str, str]:
    headers = {
        "User-Agent": cfg["WEB_USER_AGENT"],
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": referer,
    }
    request = urllib.request.Request(url, headers=headers)
    timeout = int(cfg["WEB_FETCH_TIMEOUT_SECONDS"])
    max_bytes = int(cfg["WEB_ASSET_MAX_BYTES"])
    chunks = []
    total = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        final_url = response.geturl()
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"asset exceeds WEB_ASSET_MAX_BYTES ({max_bytes}): {url}")
            chunks.append(chunk)
    return b"".join(chunks), content_type, final_url


def download_markdown_assets(
    markdown: str,
    out_path: pathlib.Path,
    referer: str,
    cfg: dict[str, str],
    enabled: bool,
) -> tuple[str, list[dict[str, Any]]]:
    if not enabled:
        return markdown, []

    asset_dir_rel = pathlib.Path("assets") / slugify(out_path.stem, "web-assets")
    asset_dir = out_path.parent / asset_dir_rel
    seen: dict[str, str] = {}
    assets: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1)
        url = match.group(2).strip()
        if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
            return match.group(0)
        if url in seen:
            return f"![{alt}]({seen[url]})"

        asset_index = len([item for item in assets if item.get("status") == "downloaded"]) + 1
        try:
            data, content_type, final_url = fetch_asset(url, referer, cfg)
            digest = sha256_bytes(data)
            ext = asset_extension(final_url or url, content_type)
            filename = f"image-{asset_index:03d}-{digest[:12]}{ext}"
            asset_dir.mkdir(parents=True, exist_ok=True)
            asset_path = asset_dir / filename
            asset_path.write_bytes(data)
            markdown_path = (asset_dir_rel / filename).as_posix()
            seen[url] = markdown_path
            assets.append(
                {
                    "source_url": url,
                    "final_url": final_url,
                    "path": rel(asset_path),
                    "markdown_path": markdown_path,
                    "content_type": content_type,
                    "size": len(data),
                    "hash": digest,
                    "status": "downloaded",
                    "error": "",
                }
            )
            return f"![{alt}]({markdown_path})"
        except Exception as exc:
            assets.append(
                {
                    "source_url": url,
                    "final_url": "",
                    "path": "",
                    "markdown_path": "",
                    "content_type": "",
                    "size": 0,
                    "hash": "",
                    "status": "failed",
                    "error": str(exc),
                }
            )
            return match.group(0)

    return IMAGE_LINK_RE.sub(replace, markdown), assets


def webpage_metadata(doc: Any, raw_html: str, url: str, final_url: str) -> dict[str, str]:
    title = (
        first_xpath_text(doc, ["//*[@id='activity-name']", "//h1[1]"])
        or meta_content(doc, "og:title", "twitter:title", "title")
        or regex_group(r"msg_title\s*=\s*'((?:\\.|[^'])*)'", raw_html)
        or first_xpath_text(doc, ["//title"])
        or urllib.parse.urlparse(final_url or url).netloc
    )
    author = (
        first_xpath_text(doc, ["//*[@id='js_name']"])
        or meta_content(doc, "author", "og:article:author", "article:author")
    )
    if not author:
        profile_nodes = doc.xpath("//mp-common-profile[@data-nickname]/@data-nickname")
        author = compact_text(str(profile_nodes[0])) if profile_nodes else ""
    published = (
        meta_content(doc, "article:published_time", "publishdate", "pubdate", "date")
        or regex_group(r"var\s+ct\s*=\s*\"([0-9]+)\"", raw_html)
        or regex_group(r"ct\s*=\s*\"([0-9]+)\"", raw_html)
    )
    if published.isdigit():
        published = unix_seconds_to_iso(published)
    return {
        "title": title,
        "author": author,
        "published": published,
        "description": meta_content(doc, "description", "og:description"),
        "final_url": final_url or url,
    }


def should_skip_url(
    manifest: dict[str, Any],
    url: str,
    output: pathlib.Path,
    source_hash: str,
    overwrite: bool,
    require_assets: bool = False,
) -> bool:
    if overwrite or not output.exists():
        return False
    for item in manifest.get("items", []):
        if item.get("source_url") == url and item.get("source_hash") == source_hash:
            if require_assets and not manifest_assets_present(item):
                return False
            return True
    return False


def manifest_assets_present(item: dict[str, Any]) -> bool:
    if not item.get("assets_downloaded"):
        return False
    assets = item.get("assets")
    if not isinstance(assets, list):
        return int(item.get("asset_count") or 0) == 0
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("status") != "downloaded":
            continue
        path = str(asset.get("path") or "")
        if not path or not (ROOT / path).exists():
            return False
    return True


def convert_webpage(
    url: str,
    output_dir: pathlib.Path,
    model: str,
    cfg: dict[str, str],
    manifest: dict[str, Any],
    overwrite: bool,
    download_assets: bool,
) -> tuple[pathlib.Path, dict[str, Any], bool]:
    raw_html, body, final_url = fetch_webpage(url, cfg)
    doc = parse_html(raw_html)
    article_node = select_article_node(doc, raw_html, final_url)
    metadata = webpage_metadata(doc, raw_html, url, final_url)
    title = metadata["title"]
    source_type = "wechat-article" if is_wechat_url(final_url) or is_wechat_url(url) else "webpage"
    extracted = webpage_markdown(article_node, final_url)
    if not extracted:
        raise RuntimeError("未能从网页中抽取到正文")
    source_bytes = extracted.encode("utf-8")
    source_hash = sha256_bytes(source_bytes)
    source_size = len(source_bytes)
    prefix = "WECHAT" if source_type == "wechat-article" else "WEB"
    out_path = output_dir / f"{prefix}-{slugify(title)}.md"
    has_remote_assets = bool(markdown_image_urls(extracted))
    if should_skip_url(manifest, url, out_path, source_hash, overwrite, download_assets and has_remote_assets):
        return out_path, {}, True

    extracted, assets = download_markdown_assets(extracted, out_path, final_url, cfg, download_assets)
    downloaded_assets = [item for item in assets if item.get("status") == "downloaded"]
    failed_assets = [item for item in assets if item.get("status") == "failed"]
    assets_downloaded = bool(download_assets) and not failed_assets

    tags = ["source/web", "status/draft"]
    if source_type == "wechat-article":
        tags.insert(1, "source/wechat")
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": source_type,
        "source_path": "",
        "source_url": url,
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": tags,
        "source_hash": source_hash,
        "assets_downloaded": assets_downloaded,
        "asset_count": len(downloaded_assets),
        "asset_failed": len(failed_assets),
    }
    asset_dir = ""
    if downloaded_assets:
        first_asset = pathlib.Path(str(downloaded_assets[0]["path"]))
        asset_dir = first_asset.parent.as_posix()
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 网页链接：{url}",
        f"- 最终链接：{metadata['final_url']}",
        f"- 来源类型：{source_type}",
        f"- 作者/账号：{metadata['author'] or '未知'}",
        f"- 发布时间：{metadata['published'] or '未知'}",
        f"- 描述：{metadata['description'] or '无'}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`lxml` + `convert_sources_to_md.py`",
        f"- 图片资产：{'已下载' if assets_downloaded else ('部分失败' if failed_assets else '未下载')}",
        f"- 资产目录：`{asset_dir or '无'}`",
        "",
        "## 待整理区",
        "",
        "> 这是网页转换草稿，尚未经过 Qwen 深度整理。正式入库前建议核对网页内容、图片链接、外部链接和发布时间。",
        "",
        "## 原文抽取",
        "",
        extracted,
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": "",
        "source_url": url,
        "final_url": metadata["final_url"],
        "source_type": source_type,
        "source_hash": source_hash,
        "source_size": source_size,
        "raw_source_size": len(body),
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "title": title,
        "author": metadata["author"],
        "published": metadata["published"],
        "assets_downloaded": assets_downloaded,
        "asset_count": len(downloaded_assets),
        "asset_failed": len(failed_assets),
        "assets": assets,
        "error": "",
    }
    return out_path, item, False


def pdf_title(reader: Any, path: pathlib.Path) -> str:
    metadata = getattr(reader, "metadata", None)
    title = ""
    if metadata:
        title = str(getattr(metadata, "title", "") or metadata.get("/Title", "") or "").strip()
    return title or path.stem


def convert_pdf(
    path: pathlib.Path,
    output_dir: pathlib.Path,
    model: str,
    max_pages: int | None,
    cfg: dict[str, str],
    qwen_polish: bool = False,
) -> tuple[pathlib.Path, dict[str, Any]]:
    if PdfReader is None:
        raise RuntimeError("pypdf is not available in the current Python environment")
    reader = PdfReader(str(path))
    title = pdf_title(reader, path)
    source_hash = sha256_file(path)
    page_count = len(reader.pages)
    pages_to_extract = page_count if not max_pages else min(page_count, max_pages)
    body: list[str] = []
    page_texts: list[tuple[int, str]] = []
    for page_number in range(pages_to_extract):
        text = clean_text(reader.pages[page_number].extract_text() or "")
        page_texts.append((page_number + 1, text))
        body.append(f"## Page {page_number + 1}\n\n{text or '> 本页未抽取到可读文本。'}")

    if pages_to_extract < page_count:
        body.append(f"## 转换截断说明\n\n样稿仅抽取前 {pages_to_extract} 页，全文共 {page_count} 页。正式转换可设置更大的 `SOURCE_CONVERSION_MAX_PDF_PAGES` 或使用 `--all-pages`。")

    qwen_section = ""
    qwen_error = ""
    if qwen_polish:
        try:
            qwen_output = qwen_polish_pdf(title, path, page_texts, cfg)
            qwen_section = "\n\n".join(
                [
                    "## Qwen 整理草稿",
                    f"> 由 `{model}` 基于 `pypdf` 抽取文本整理。公式、表格和符号以原 PDF 为准；标注待核验的内容需要人工回看原文。",
                    "",
                    qwen_output,
                ]
            )
        except Exception as exc:
            qwen_error = str(exc)
            qwen_section = "\n\n".join(
                [
                    "## Qwen 整理草稿",
                    f"> Qwen 整理失败，已保留 `pypdf` 原文抽取供后续补跑。错误：`{qwen_error}`",
                ]
            )

    out_name = f"PDF-{slugify(title, path.stem)}.md"
    out_path = output_dir / out_name
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "pdf",
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/pdf", "domain/ai", "status/draft"],
        "source_hash": source_hash,
    }
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 源文件：`{rel(path)}`",
        f"- 文件类型：PDF",
        f"- 页数：{page_count}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`pypdf`",
        "",
        "## 待整理区",
        "",
        "> 这是源文件转换草稿。若启用 `--qwen-polish-pdf`，下方会包含 Qwen 基于抽取文本生成的整理草稿；正式使用前仍应核对公式、表格和关键实验数据。",
        "",
        qwen_section,
        "",
        "## 原文抽取",
        "",
        *body,
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_type": "pdf",
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "llm_polished": bool(qwen_polish and not qwen_error),
        "llm_error": qwen_error,
        "error": "",
    }
    return out_path, item


def docx_relationships(docx: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    rel_path = "word/_rels/document.xml.rels"
    if rel_path not in docx.namelist():
        return {}
    root = ET.fromstring(docx.read(rel_path))
    relationships = {}
    for rel in root.findall("rel:Relationship", DOCX_NS):
        rid = rel.attrib.get("Id", "")
        if rid:
            relationships[rid] = {
                "target": rel.attrib.get("Target", ""),
                "type": rel.attrib.get("Type", ""),
                "mode": rel.attrib.get("TargetMode", ""),
            }
    return relationships


def docx_styles(docx: zipfile.ZipFile) -> dict[str, str]:
    if "word/styles.xml" not in docx.namelist():
        return {}
    root = ET.fromstring(docx.read("word/styles.xml"))
    styles = {}
    for style in root.findall("w:style", DOCX_NS):
        sid = style.attrib.get(f"{{{DOCX_NS['w']}}}styleId", "")
        name = style.find("w:name", DOCX_NS)
        if sid and name is not None:
            styles[sid] = name.attrib.get(f"{{{DOCX_NS['w']}}}val", sid)
    return styles


def docx_core_properties(docx: zipfile.ZipFile) -> dict[str, str]:
    if "docProps/core.xml" not in docx.namelist():
        return {}
    root = ET.fromstring(docx.read("docProps/core.xml"))
    values = {}
    for child in root:
        key = child.tag.rsplit("}", 1)[-1]
        values[key] = clean_text(child.text or "")
    return values


def docx_rel_target(target: str) -> str:
    if target.startswith("media/"):
        return "word/" + target
    if target.startswith("../"):
        return target[3:]
    return "word/" + target


def docx_copy_image(docx: zipfile.ZipFile, rels: dict[str, dict[str, str]], rid: str, assets_dir: pathlib.Path) -> str:
    rel = rels.get(rid)
    if not rel:
        return ""
    src = docx_rel_target(rel.get("target", ""))
    if src not in docx.namelist():
        return ""
    data = docx.read(src)
    digest = sha256_bytes(data)
    suffix = pathlib.Path(src).suffix.lower() or ".png"
    name = f"image-{digest[:12]}{suffix}"
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_path = assets_dir / name
    if not out_path.exists():
        out_path.write_bytes(data)
    return f"assets/{assets_dir.name}/{name}"


def docx_inline_text(node: ET.Element, docx: zipfile.ZipFile, rels: dict[str, dict[str, str]], assets_dir: pathlib.Path) -> str:
    tag = node.tag
    if tag == f"{{{DOCX_NS['w']}}}t":
        return node.text or ""
    if tag == f"{{{DOCX_NS['w']}}}tab":
        return "\t"
    if tag == f"{{{DOCX_NS['w']}}}br":
        return "\n"
    if tag == f"{{{DOCX_NS['w']}}}hyperlink":
        rid = node.attrib.get(f"{{{DOCX_NS['r']}}}id", "")
        anchor = node.attrib.get(f"{{{DOCX_NS['w']}}}anchor", "")
        label = "".join(docx_inline_text(child, docx, rels, assets_dir) for child in list(node)).strip()
        target = rels.get(rid, {}).get("target", "") if rid else ""
        if target and label:
            return f"[{label}]({target})"
        if anchor and label:
            return f"[{label}](#{anchor})"
        return label
    if tag == f"{{{DOCX_NS['a']}}}blip":
        rid = node.attrib.get(f"{{{DOCX_NS['r']}}}embed", "") or node.attrib.get(f"{{{DOCX_NS['r']}}}link", "")
        image_path = docx_copy_image(docx, rels, rid, assets_dir) if rid else ""
        return f"\n\n![image]({image_path})\n\n" if image_path else ""
    return "".join(docx_inline_text(child, docx, rels, assets_dir) for child in list(node))


def docx_paragraph_style(paragraph: ET.Element, styles: dict[str, str]) -> str:
    ppr = paragraph.find("w:pPr", DOCX_NS)
    if ppr is None:
        return ""
    pstyle = ppr.find("w:pStyle", DOCX_NS)
    if pstyle is None:
        return ""
    sid = pstyle.attrib.get(f"{{{DOCX_NS['w']}}}val", "")
    return styles.get(sid, sid)


def docx_paragraph_level(paragraph: ET.Element) -> int:
    ppr = paragraph.find("w:pPr", DOCX_NS)
    if ppr is None:
        return 0
    ilvl = ppr.find("w:numPr/w:ilvl", DOCX_NS)
    if ilvl is None:
        return 0
    try:
        return int(ilvl.attrib.get(f"{{{DOCX_NS['w']}}}val", "0"))
    except ValueError:
        return 0


def docx_is_list(paragraph: ET.Element) -> bool:
    return paragraph.find("w:pPr/w:numPr", DOCX_NS) is not None


def docx_table_markdown(table: ET.Element, docx: zipfile.ZipFile, rels: dict[str, dict[str, str]], assets_dir: pathlib.Path) -> str:
    rows = []
    for tr in table.findall("w:tr", DOCX_NS):
        cells = []
        for tc in tr.findall("w:tc", DOCX_NS):
            parts = [
                docx_inline_text(paragraph, docx, rels, assets_dir).strip()
                for paragraph in tc.findall("w:p", DOCX_NS)
            ]
            cells.append("<br>".join(part for part in parts if part).replace("|", "\\|"))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def docx_body_markdown(path: pathlib.Path, assets_dir: pathlib.Path) -> tuple[str, dict[str, str], int, int]:
    with zipfile.ZipFile(path) as docx:
        if "word/document.xml" not in docx.namelist():
            raise RuntimeError("invalid docx: missing word/document.xml")
        root = ET.fromstring(docx.read("word/document.xml"))
        rels = docx_relationships(docx)
        styles = docx_styles(docx)
        props = docx_core_properties(docx)
        body = root.find("w:body", DOCX_NS)
        if body is None:
            return "", props, 0, 0

        blocks = []
        image_before = len(list(assets_dir.glob("*"))) if assets_dir.exists() else 0
        for child in list(body):
            if child.tag == f"{{{DOCX_NS['w']}}}p":
                text = clean_text(docx_inline_text(child, docx, rels, assets_dir))
                if not text:
                    continue
                style = docx_paragraph_style(child, styles).lower()
                heading_match = re.search(r"heading\s*([1-6])|标题\s*([1-6])", style)
                if heading_match:
                    level = int(heading_match.group(1) or heading_match.group(2))
                    blocks.append(f"{'#' * min(level + 1, 6)} {text}")
                elif docx_is_list(child) or "list" in style or "列表" in style:
                    indent = "  " * docx_paragraph_level(child)
                    blocks.append(f"{indent}- {text}")
                else:
                    blocks.append(text)
            elif child.tag == f"{{{DOCX_NS['w']}}}tbl":
                table = docx_table_markdown(child, docx, rels, assets_dir)
                if table:
                    blocks.append(table)
        image_after = len(list(assets_dir.glob("*"))) if assets_dir.exists() else 0
        return normalize_markdown("\n\n".join(blocks)), props, image_after - image_before, image_after


def convert_docx(path: pathlib.Path, output_dir: pathlib.Path, model: str) -> tuple[pathlib.Path, dict[str, Any]]:
    source_hash = sha256_file(path)
    out_name = f"DOCX-{slugify(path.stem)}.md"
    out_path = output_dir / out_name
    assets_dir = output_dir / "assets" / out_path.stem
    body, props, new_images, image_count = docx_body_markdown(path, assets_dir)
    title = props.get("title") or path.stem
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "docx",
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/docx", "status/draft"],
        "source_hash": source_hash,
        "asset_count": image_count,
    }
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 源文件：`{rel(path)}`",
        "- 文件类型：DOCX",
        f"- 作者：{props.get('creator') or '未知'}",
        f"- 创建时间：{props.get('created') or '未知'}",
        f"- 修改时间：{props.get('modified') or '未知'}",
        f"- 图片资产：{image_count} 个",
        f"- 新增图片：{new_images} 个",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`zipfile` + `ElementTree`",
        "",
        "## 待整理区",
        "",
        "> 这是 DOCX 转换草稿。正式入库前建议核对标题层级、列表、表格和图片顺序。",
        "",
        "## 原文抽取",
        "",
        body or "> 未抽取到正文。",
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_type": "docx",
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "title": title,
        "asset_count": image_count,
        "error": "",
    }
    return out_path, item


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(parts)
    return ""


def selected_version(message: dict[str, Any]) -> dict[str, Any]:
    versions = message.get("versions")
    if not isinstance(versions, list) or not versions:
        return message
    selected = message.get("currentlySelected", 0)
    if not isinstance(selected, int) or selected < 0 or selected >= len(versions):
        selected = 0
    return versions[selected]


def extract_message(version: dict[str, Any]) -> tuple[str, str, str]:
    role = str(version.get("role") or version.get("preprocessed", {}).get("role") or "unknown")
    sender = ""
    text = text_from_content(version.get("content"))
    if not text and isinstance(version.get("preprocessed"), dict):
        text = text_from_content(version["preprocessed"].get("content"))
    if not text and isinstance(version.get("steps"), list):
        parts = []
        for step in version["steps"]:
            if not isinstance(step, dict):
                continue
            parts.append(text_from_content(step.get("content")))
            gen_info = step.get("genInfo") or {}
            if isinstance(gen_info, dict) and not sender:
                sender = model_label(gen_info)
        text = "\n\n".join(part for part in parts if part)
    sender_info = version.get("senderInfo") or {}
    if isinstance(sender_info, dict):
        sender = str(sender_info.get("senderName") or sender)
    return role, sender, clean_text(text)


def convert_conversation(path: pathlib.Path, output_dir: pathlib.Path, model: str) -> tuple[pathlib.Path, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    title = str(data.get("name") or path.stem).strip() or path.stem
    source_hash = sha256_file(path)
    messages = data.get("messages") if isinstance(data.get("messages"), list) else []
    rows = []
    for index, message in enumerate(messages, 1):
        version = selected_version(message if isinstance(message, dict) else {})
        role, sender, text = extract_message(version)
        heading = f"### {index}. {role}"
        if sender:
            heading += f" ({sender})"
        rows.append(f"{heading}\n\n{text or '> 空消息或暂未解析到文本。'}")

    out_name = f"CHAT-{slugify(title, path.stem)}.md"
    out_path = output_dir / out_name
    last_model = model_label(data.get("lastUsedModel")) or model
    conversation_body = "\n\n".join(rows) if rows else "> 未解析到消息。"
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "lmstudio-conversation",
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/lmstudio", "ai-chat", "status/draft"],
        "source_hash": source_hash,
    }
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 源文件：`{rel(path)}`",
        "- 文件类型：LM Studio conversation JSON",
        f"- 对话创建时间：{ms_to_iso(data.get('createdAt')) or '未知'}",
        f"- 最近用户消息：{ms_to_iso(data.get('userLastMessagedAt')) or '未知'}",
        f"- 最近助手消息：{ms_to_iso(data.get('assistantLastMessagedAt')) or '未知'}",
        f"- 最后使用模型：`{last_model}`",
        f"- 消息数：{len(messages)}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "",
        "## 待整理区",
        "",
        "> 这是对话 JSON 转换草稿，尚未经过 Qwen 深度整理。后续整理时建议提炼问题、结论、待核验信息和可沉淀为长期知识的要点。",
        "",
        "## 对话正文",
        "",
        conversation_body,
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_type": "lmstudio-conversation",
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "error": "",
    }
    return out_path, item


def should_skip(
    manifest: dict[str, Any],
    source: pathlib.Path,
    output: pathlib.Path,
    source_hash: str,
    overwrite: bool,
    require_llm_polished: bool = False,
) -> bool:
    if overwrite or not output.exists():
        return False
    for item in manifest.get("items", []):
        if item.get("source_path") == rel(source) and item.get("source_hash") == source_hash:
            if require_llm_polished and not item.get("llm_polished"):
                return False
            return True
    return False


def discover_sources(source_dir: pathlib.Path, sample: bool, cfg: dict[str, str]) -> list[pathlib.Path]:
    pdfs = sorted(source_dir.glob("AI_paper/*.pdf"))
    conversations = sorted(source_dir.glob("AI-Chat/LM-Studio/**/*.conversation.json"))
    docx_files = sorted(source_dir.glob("**/*.docx"))
    if sample:
        pdfs = pdfs[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_PDF"])]
        conversations = conversations[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_JSON"])]
        docx_files = docx_files[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX"])]
    return [*pdfs, *conversations, *docx_files]


def main() -> int:
    cfg = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="convert a small configured sample set")
    parser.add_argument("--all-pages", action="store_true", help="extract all PDF pages")
    parser.add_argument("--qwen-polish-pdf", action="store_true", help="ask the configured local Qwen model to polish PDF extraction into Markdown")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing outputs")
    parser.add_argument("--source", action="append", help="specific source path to convert")
    parser.add_argument("--url", action="append", help="web page URL to convert, including WeChat public-account articles")
    asset_group = parser.add_mutually_exclusive_group()
    asset_group.add_argument(
        "--download-assets",
        dest="download_assets",
        action="store_true",
        default=None,
        help="download web images/assets into a local assets directory",
    )
    asset_group.add_argument(
        "--no-download-assets",
        dest="download_assets",
        action="store_false",
        help="keep remote web image URLs instead of downloading assets",
    )
    parser.add_argument("--output-dir", default=cfg["SAMPLE_OUTPUT_DIR"], help="Markdown output directory")
    args = parser.parse_args()

    source_dir = (ROOT / cfg["SOURCE_DIR"]).resolve()
    output_dir = (ROOT / args.output_dir).resolve()
    index_dir = (ROOT / cfg["INDEX_DIR"]).resolve()
    manifest_path = index_dir / "source-manifest.json"
    manifest = load_manifest(manifest_path)
    model = cfg["DEFAULT_LLM_MODEL"]
    max_pages = None if args.all_pages else int(cfg["SOURCE_CONVERSION_MAX_PDF_PAGES"])
    download_assets = parse_bool(cfg["WEB_DOWNLOAD_ASSETS"]) if args.download_assets is None else args.download_assets

    urls = args.url or []
    if args.source:
        sources = [(ROOT / item).resolve() for item in args.source]
    elif urls:
        sources = []
    else:
        sources = discover_sources(source_dir, args.sample, cfg)

    converted = 0
    skipped = 0
    failed = 0
    pdf_cooldown_delay = float(cfg.get("QWEN_PDF_POLISH_COOLDOWN_DELAY") or 0)
    for source_index, source in enumerate(sources, start=1):
        try:
            if not source.exists():
                raise FileNotFoundError(source)
            source_hash = sha256_file(source)
            if source.suffix.lower() == ".pdf":
                probe_reader = PdfReader(str(source)) if PdfReader is not None else None
                title = pdf_title(probe_reader, source) if probe_reader is not None else source.stem
                output_path = output_dir / f"PDF-{slugify(title, source.stem)}.md"
                if should_skip(manifest, source, output_path, source_hash, args.overwrite, args.qwen_polish_pdf):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_pdf(source, output_dir, model, max_pages, cfg, args.qwen_polish_pdf)
            elif source.name.endswith(".conversation.json"):
                title = str(json.loads(source.read_text(encoding="utf-8")).get("name") or source.stem)
                output_path = output_dir / f"CHAT-{slugify(title, source.stem)}.md"
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_conversation(source, output_dir, model)
            elif source.suffix.lower() == ".docx":
                output_path = output_dir / f"DOCX-{slugify(source.stem)}.md"
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_docx(source, output_dir, model)
            else:
                raise ValueError(f"unsupported source type: {source}")
            update_manifest(manifest, item)
            converted += 1
            print(f"converted {rel(source)} -> {rel(out_path)}")
            if args.qwen_polish_pdf and source.suffix.lower() == ".pdf" and pdf_cooldown_delay > 0 and source_index < len(sources):
                print(f"cooldown {pdf_cooldown_delay:g}s before next PDF source", file=sys.stderr)
                time.sleep(pdf_cooldown_delay)
        except Exception as exc:
            failed += 1
            error_item = {
                "source_path": rel(source),
                "source_type": source.suffix.lower().lstrip(".") or "unknown",
                "source_hash": sha256_file(source) if source.exists() else "",
                "source_size": source.stat().st_size if source.exists() else 0,
                "output_path": "",
                "status": "failed",
                "converted_at": now_iso(),
                "model": model,
                "error": str(exc),
            }
            update_manifest(manifest, error_item)
            print(f"failed {rel(source)}: {exc}", file=sys.stderr)
    for url in urls:
        try:
            out_path, item, did_skip = convert_webpage(url, output_dir, model, cfg, manifest, args.overwrite, download_assets)
            if did_skip:
                skipped += 1
                print(f"skip {url}")
                continue
            update_manifest(manifest, item)
            converted += 1
            print(f"converted {url} -> {rel(out_path)}")
        except Exception as exc:
            failed += 1
            error_item = {
                "source_path": "",
                "source_url": url,
                "source_type": "webpage",
                "source_hash": "",
                "source_size": 0,
                "output_path": "",
                "status": "failed",
                "converted_at": now_iso(),
                "model": model,
                "error": str(exc),
            }
            update_manifest(manifest, error_item)
            print(f"failed {url}: {exc}", file=sys.stderr)
    save_manifest(manifest_path, manifest)
    print(f"done converted={converted} skipped={skipped} failed={failed} manifest={rel(manifest_path)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
