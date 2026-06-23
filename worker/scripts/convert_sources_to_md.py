#!/usr/bin/env python3
"""Convert local source files into Markdown drafts.

The first implementation supports PDF files through pypdf and LM Studio
conversation JSON exports. It intentionally produces traceable drafts rather
than final organized notes; Qwen can refine these drafts later.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html as html_lib
import http.cookiejar
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
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

XLSX_NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

PPTX_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

IMAGE_SOURCE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".bmp", ".tif", ".tiff", ".gif"}


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
    "WEB_CAPTURE_MODE": "static",
    "BROWSER_EXECUTABLE": "",
    "BROWSER_PROFILE": "",
    "WEB_DOWNLOAD_ASSETS": "true",
    "WEB_ASSET_MAX_BYTES": str(50 * 1024 * 1024),
    "ENABLE_OCR": "false",
    "LOCAL_NOTE_STUDIO_INCOGNITO": "false",
    "WEB_USER_AGENT": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "BILIBILI_COOKIES_FILE": "",
    "BILI_COOKIE_FILE": "",
    "BILIBILI_OPUS_REQUEST_DELAY_SECONDS": "0.8",
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


def custom_output_path(output_dir: pathlib.Path, output_filename: str = "") -> pathlib.Path | None:
    name = output_filename.strip()
    if not name:
        return None
    if pathlib.PurePath(name).name != name or "/" in name or "\\" in name:
        raise ValueError("--output-filename 只能是文件名，不能包含目录")
    if not name.lower().endswith(".md"):
        name += ".md"
    return output_dir / name


def output_path_for(output_dir: pathlib.Path, default_name: str, output_filename: str = "") -> pathlib.Path:
    return custom_output_path(output_dir, output_filename) or (output_dir / default_name)


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
    if "last_action" not in item:
        item["last_action"] = "failed" if item.get("status") == "failed" or item.get("error") else "processed"
    item["last_checked_at"] = now_iso()
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


def compact_error_detail(value: Any, max_lines: int = 10) -> str:
    text = clean_text(str(value or ""))
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + "\n..."
    return text


def read_text_with_fallback(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


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


def image_to_data_url(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".heic": "image/heic",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def vision_ocr_image(cfg: dict[str, str], path: pathlib.Path) -> str:
    prompt = (
        "请对这张文档页面做 OCR 文本提取。"
        "要求：1. 尽量按阅读顺序逐行输出；2. 不要总结；3. 保留标题、列表、表格和数字；"
        "4. 无法确认的字符用 [待核验] 标记；5. 只输出正文，不要解释。"
    )
    return clean_qwen_markdown(
        call_chat_completion(
            cfg,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_to_data_url(path)}},
                    ],
                }
            ],
        )
    )


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
    if cfg.get("WEB_CAPTURE_MODE", "static").strip().lower() == "browser":
        return fetch_webpage_with_browser(url, cfg)
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


def chrome_executable(cfg: dict[str, str]) -> str:
    configured = cfg.get("BROWSER_EXECUTABLE", "").strip()
    candidates = [
        configured,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
    ]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).exists():
            return candidate
    raise RuntimeError("浏览器会话采集需要 Chrome/Chromium；请填写可执行文件路径")


def fetch_webpage_with_browser(url: str, cfg: dict[str, str]) -> tuple[str, bytes, str]:
    profile_value = cfg.get("BROWSER_PROFILE", "").strip()
    if not profile_value:
        raise RuntimeError("浏览器会话采集只会在明确选择后启用；请先选择 Chrome Profile")
    profile = pathlib.Path(profile_value).expanduser().resolve()
    if not profile.exists():
        raise RuntimeError(f"Chrome Profile 不存在: {profile}")
    if (profile / "Preferences").exists():
        user_data_dir = profile.parent
        profile_name = profile.name
    else:
        user_data_dir = profile
        profile_name = "Default"
    command = [
        chrome_executable(cfg),
        "--headless=new",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-default-apps",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_name}",
        "--dump-dom",
        url,
    ]
    print(f"[浏览器采集] 仅使用所选 Profile 访问当前 URL：{url}", flush=True)
    result = subprocess.run(
        command,
        capture_output=True,
        timeout=max(10, int(cfg["WEB_FETCH_TIMEOUT_SECONDS"])),
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace")
        if "already in use" in detail.lower() or "singleton" in detail.lower():
            detail += "\n所选 Chrome Profile 正被浏览器占用，请关闭对应 Chrome 窗口后重试。"
        raise RuntimeError(f"浏览器会话采集失败: {compact_error_detail(detail)}")
    body = bytes(result.stdout)
    return body.decode("utf-8", errors="replace"), body, url


def is_bilibili_opus_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc.lower().endswith("bilibili.com") and re.search(r"/opus/([0-9]+)", parsed.path) is not None


def bilibili_space_mid(value: str) -> str:
    candidate = value.strip()
    if candidate.isdigit():
        return candidate
    parsed = urllib.parse.urlparse(candidate)
    match = re.search(r"^/([0-9]+)(?:/|$)", parsed.path)
    if parsed.netloc.lower() == "space.bilibili.com" and match:
        return match.group(1)
    raise ValueError(f"不是有效的 B站 UP 主空间图文页或 UID: {value}")


def bilibili_opus_id(url: str) -> str:
    match = re.search(r"/opus/([0-9]+)", urllib.parse.urlparse(url).path)
    if not match:
        raise ValueError(f"not a Bilibili opus URL: {url}")
    return match.group(1)


def bilibili_cookie_path(cfg: dict[str, str]) -> pathlib.Path | None:
    raw = (cfg.get("BILIBILI_COOKIES_FILE") or cfg.get("BILI_COOKIE_FILE") or "").strip()
    if not raw:
        return None
    path = pathlib.Path(os.path.expanduser(os.path.expandvars(raw)))
    if not path.is_absolute():
        candidates = [ROOT.parent / path, ROOT / path]
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    return path


def build_cookie_opener(cookie_path: pathlib.Path | None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    if cookie_path:
        if not cookie_path.exists():
            raise FileNotFoundError(f"B站 Cookie 文件不存在: {cookie_path}")
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(str(cookie_path), ignore_discard=True, ignore_expires=True)
        mirror_bilibili_auth_cookies(jar)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


def mirror_bilibili_auth_cookies(jar: http.cookiejar.CookieJar) -> None:
    """Some exports store Bilibili auth cookies under .bilibili.cn.

    The web dynamic API lives on api.bilibili.com, so a strict CookieJar will not
    send .bilibili.cn cookies there. Mirroring these auth cookies in memory keeps
    yt-dlp/Chrome exports usable without rewriting the user's cookies.txt.
    """
    auth_names = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"}
    existing = {(cookie.name, cookie.domain) for cookie in jar}
    clones = []
    for cookie in jar:
        if cookie.name not in auth_names or not cookie.domain.endswith("bilibili.cn"):
            continue
        target_domain = ".bilibili.com"
        if (cookie.name, target_domain) in existing:
            continue
        clones.append(
            http.cookiejar.Cookie(
                version=cookie.version,
                name=cookie.name,
                value=cookie.value,
                port=cookie.port,
                port_specified=cookie.port_specified,
                domain=target_domain,
                domain_specified=True,
                domain_initial_dot=True,
                path=cookie.path or "/",
                path_specified=cookie.path_specified,
                secure=cookie.secure,
                expires=cookie.expires,
                discard=cookie.discard,
                comment=cookie.comment,
                comment_url=cookie.comment_url,
                rest=dict(cookie._rest),
                rfc2109=cookie.rfc2109,
            )
        )
    for cookie in clones:
        jar.set_cookie(cookie)


def fetch_json_with_cookies(url: str, referer: str, cfg: dict[str, str]) -> dict[str, Any]:
    opener = build_cookie_opener(bilibili_cookie_path(cfg))
    referer_url = urllib.parse.urlparse(referer)
    headers = {
        "User-Agent": cfg["WEB_USER_AGENT"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": f"{referer_url.scheme}://{referer_url.netloc}" if referer_url.netloc else "https://www.bilibili.com",
        "Referer": referer,
    }
    request = urllib.request.Request(url, headers=headers)
    timeout = int(cfg["WEB_FETCH_TIMEOUT_SECONDS"])
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 412:
            raise RuntimeError(
                "B站接口风控/HTTP 412：请求被拦截。请稍后再试；若持续出现，"
                "请从已登录 Chrome Profile 刷新 Cookie 后先验证登录态和目标权限。"
            ) from exc
        raise RuntimeError(f"B站接口 HTTP {exc.code}：{exc.reason}") from exc


def bilibili_nav_status(cfg: dict[str, str]) -> dict[str, Any]:
    return fetch_json_with_cookies(
        "https://api.bilibili.com/x/web-interface/nav",
        "https://www.bilibili.com/",
        cfg,
    )


def ensure_bilibili_cookie_login(cfg: dict[str, str]) -> None:
    try:
        payload = bilibili_nav_status(cfg)
    except Exception as exc:
        raise RuntimeError(f"B站 Cookie 登录态检查失败: {exc}") from exc
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if payload.get("code") != 0 or not data.get("isLogin"):
        raise RuntimeError(
            "B站 Cookie 未登录或已失效。请从已登录 Chrome Profile 刷新 Cookie，"
            "然后先运行登录态验证；这与账号缺少充电档位权限是两类不同问题。"
        )


def fetch_bilibili_space_opus_urls(source: str, cfg: dict[str, str], limit: int = 0) -> list[str]:
    mid = bilibili_space_mid(source)
    if bilibili_cookie_path(cfg) is None:
        raise RuntimeError("B站 UP 主图文批量整理需要配置 BILIBILI_COOKIES_FILE 或 BILI_COOKIE_FILE。")
    ensure_bilibili_cookie_login(cfg)

    referer = f"https://space.bilibili.com/{mid}/upload/opus"
    offset = ""
    seen_offsets: set[str] = set()
    seen_ids: set[str] = set()
    opus_urls: list[str] = []
    page = 0
    while True:
        page += 1
        params = {
            "host_mid": mid,
            "offset": offset,
            "timezone_offset": "-480",
            "platform": "web",
            "features": (
                "itemOpusStyle,onlyfansVote,decorationCard,forwardListHidden,"
                "ugcDelete,onlyfansAssetsV2,commentsNewVersion"
            ),
            "web_location": "333.1387",
        }
        api_url = (
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?"
            + urllib.parse.urlencode(params)
        )
        payload = fetch_json_with_cookies(api_url, referer, cfg)
        if payload.get("code") != 0:
            raise RuntimeError(
                f"B站空间图文列表接口失败 (code={payload.get('code')}): "
                f"{payload.get('message') or payload.get('msg') or '未知错误'}"
            )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        page_added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            modules = item.get("modules") if isinstance(item.get("modules"), dict) else {}
            dynamic = modules.get("module_dynamic") if isinstance(modules.get("module_dynamic"), dict) else {}
            major = dynamic.get("major") if isinstance(dynamic.get("major"), dict) else {}
            if major.get("type") != "MAJOR_TYPE_OPUS":
                continue
            opus_id = str(item.get("id_str") or item.get("id") or "").strip()
            if not opus_id or opus_id in seen_ids:
                continue
            seen_ids.add(opus_id)
            opus_urls.append(f"https://www.bilibili.com/opus/{opus_id}")
            page_added += 1
            if limit > 0 and len(opus_urls) >= limit:
                print(f"B站空间第 {page} 页：新增图文 {page_added} 条，达到处理上限 {limit} 条。")
                return opus_urls

        print(f"B站空间第 {page} 页：读取动态 {len(items)} 条，新增图文 {page_added} 条。")
        if not data.get("has_more"):
            break
        next_offset = str(data.get("offset") or "").strip()
        if not next_offset or next_offset == offset or next_offset in seen_offsets:
            print("B站空间分页 offset 未变化，停止继续读取。", file=sys.stderr)
            break
        seen_offsets.add(next_offset)
        offset = next_offset

    if not opus_urls:
        raise RuntimeError(f"未在 UP 主 {mid} 的空间动态中找到可整理的图文。")
    return opus_urls


def rich_text(nodes: Any) -> str:
    if not isinstance(nodes, list):
        return ""
    return "".join(str(node.get("text") or "") for node in nodes if isinstance(node, dict)).strip()


def text_value(value: Any) -> str:
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, dict):
        return compact_text(value.get("text") or rich_text(value.get("rich_text_nodes")) or value.get("summary") or "")
    if isinstance(value, list):
        return compact_text("\n".join(text_value(item) for item in value))
    return ""


def append_unique(lines: list[str], value: str) -> None:
    text = compact_text(value)
    if not text or text in {"充电可见", "充电专属", "该内容仅充电可见"}:
        return
    if text not in lines:
        lines.append(text)


def blocked_opus_message(major: dict[str, Any], url: str) -> str:
    blocked = major.get("blocked") if isinstance(major, dict) else {}
    blocked = blocked if isinstance(blocked, dict) else {}
    hint = text_value(blocked.get("hint_message"))
    button = blocked.get("button") if isinstance(blocked.get("button"), dict) else {}
    button_text = text_value(button.get("text"))
    jump_url = str(button.get("jump_url") or "").strip()
    detail = "；".join(part for part in [hint, button_text] if part)
    if detail:
        detail = f"：{detail}"
    jump = f" 解锁入口：{jump_url}" if jump_url else ""
    return (
        f"B站接口返回权限占位，当前 cookie 无法读取该动态正文{detail}。"
        f"{jump} 请确认导出 cookie 的浏览器账号已加入对应充电档位，并重新导出 cookie 后再试。动态链接：{url}"
    )


def collect_opus_images(value: Any) -> list[str]:
    urls: list[str] = []
    seen = set()

    def add(url: str) -> None:
        if url.startswith("//"):
            url = "https:" + url
        if not urllib.parse.urlparse(url).scheme or url in seen:
            return
        seen.add(url)
        urls.append(url)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, raw in node.items():
                if key in {"url", "src", "img_src", "cover"} and isinstance(raw, str):
                    parsed = urllib.parse.urlparse(raw if not raw.startswith("//") else "https:" + raw)
                    target = parsed.path + (("?" + parsed.query) if parsed.query else "")
                    if parsed.netloc and re.search(r"\.(jpg|jpeg|png|webp|gif|avif)(?:$|[?&])", target, re.I):
                        add(raw)
                else:
                    walk(raw)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return urls


def parse_bilibili_opus_payload(payload: dict[str, Any], url: str) -> dict[str, Any]:
    if payload.get("code") != 0:
        code = payload.get("code")
        message = str(payload.get("message") or payload.get("msg") or "")
        if str(code) == "-412":
            raise RuntimeError("B站接口风控/HTTP 412：请稍后再试，并在持续出现时刷新 Cookie。")
        if str(code) in {"-101", "-111"}:
            raise RuntimeError("B站 Cookie 未登录或已失效，请刷新 Cookie 后重新验证登录态。")
        if str(code) in {"-403", "11010", "11011"} or "权限" in message:
            raise RuntimeError("账号无对应充电权限：请确认当前账号已加入该内容要求的充电档位。")
        raise RuntimeError(f"B站动态接口返回错误: code={code} message={message}")
    item = ((payload.get("data") or {}).get("item") or {})
    if not isinstance(item, dict) or not item:
        raise RuntimeError("B站动态正文为空：接口未返回内容。请分别检查目标是否存在、账号权限，以及是否触发接口风控。")

    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    dynamic = modules.get("module_dynamic") or {}
    major = dynamic.get("major") or {}
    if isinstance(major, dict) and major.get("type") == "MAJOR_TYPE_BLOCKED":
        raise RuntimeError(blocked_opus_message(major, url))
    opus = major.get("opus") if isinstance(major, dict) else {}
    opus = opus if isinstance(opus, dict) else {}

    title = text_value(opus.get("title")) or text_value(dynamic.get("topic")) or f"B站动态 {bilibili_opus_id(url)}"
    lines: list[str] = []
    append_unique(lines, text_value(dynamic.get("desc")))
    append_unique(lines, text_value(opus.get("title")))
    append_unique(lines, text_value(opus.get("summary")))
    append_unique(lines, text_value(opus.get("content")))
    append_unique(lines, text_value(opus.get("paragraphs")))

    def collect_text_nodes(node: Any) -> None:
        if isinstance(node, dict):
            if "rich_text_nodes" in node:
                append_unique(lines, rich_text(node.get("rich_text_nodes")))
            for child in node.values():
                collect_text_nodes(child)
        elif isinstance(node, list):
            for child in node:
                collect_text_nodes(child)

    collect_text_nodes(item)
    content = "\n\n".join(lines).strip()
    if not content:
        module_keys = ", ".join(sorted(str(key) for key in modules.keys())) or "无"
        major_type = str(major.get("type") or "unknown") if isinstance(major, dict) else "unknown"
        raise RuntimeError(
            "B站动态正文为空。"
            f"接口模块：{module_keys}；动态类型：{major_type}。"
            "请确认 cookie 对该动态有权限；如果是充电动态，请确认账号已加入对应档位并重新导出 B站 cookie。"
        )

    pub_ts = author.get("pub_ts")
    published = unix_seconds_to_iso(str(pub_ts)) if str(pub_ts or "").isdigit() else str(author.get("pub_time") or "")
    return {
        "item": item,
        "title": title,
        "author": str(author.get("name") or author.get("uname") or ""),
        "author_mid": str(author.get("mid") or ""),
        "published": published,
        "content": content,
        "images": collect_opus_images(dynamic),
    }


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
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https", "file"}:
            return match.group(0)
        if url in seen:
            return f"![{alt}]({seen[url]})"

        asset_index = len([item for item in assets if item.get("status") == "downloaded"]) + 1
        try:
            if scheme == "file":
                local_path = pathlib.Path(urllib.request.url2pathname(parsed.path))
                if not local_path.exists():
                    raise FileNotFoundError(local_path)
                data = local_path.read_bytes()
                content_type = ""
                final_url = url
                ext = local_path.suffix.lower() or ".bin"
            else:
                data, content_type, final_url = fetch_asset(url, referer, cfg)
                ext = asset_extension(final_url or url, content_type)
            digest = sha256_bytes(data)
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
        if isinstance(item, dict) and item.get("source_url") == url:
            item["last_action"] = "skipped"
            item["last_checked_at"] = now_iso()
            break
    return True


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
    output_filename: str = "",
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
    out_path = output_path_for(output_dir, f"{prefix}-{slugify(title)}.md", output_filename)
    has_remote_assets = bool(markdown_image_urls(extracted))
    if should_skip_url(manifest, url, out_path, source_hash, overwrite, download_assets and has_remote_assets):
        return out_path, {}, True

    extracted, assets = download_markdown_assets(extracted, out_path, final_url, cfg, download_assets)
    downloaded_assets = [item for item in assets if item.get("status") == "downloaded"]
    failed_assets = [item for item in assets if item.get("status") == "failed"]
    assets_downloaded = bool(download_assets) and has_remote_assets and not failed_assets

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
        f"- 图片资产：{'无图片' if not has_remote_assets else ('已下载' if assets_downloaded else ('部分失败' if failed_assets else '未下载'))}",
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


def convert_bilibili_opus(
    url: str,
    output_dir: pathlib.Path,
    model: str,
    cfg: dict[str, str],
    manifest: dict[str, Any],
    overwrite: bool,
    download_assets: bool,
    output_filename: str = "",
) -> tuple[pathlib.Path, dict[str, Any], bool]:
    opus_id = bilibili_opus_id(url)
    if bilibili_cookie_path(cfg) is None:
        raise RuntimeError("B站动态/充电动态需要配置 BILIBILI_COOKIES_FILE 或 BILI_COOKIE_FILE。")
    ensure_bilibili_cookie_login(cfg)
    api_url = (
        "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?"
        + urllib.parse.urlencode({"id": opus_id, "features": "itemOpusStyle"})
    )
    payload = fetch_json_with_cookies(api_url, url, cfg)
    parsed = parse_bilibili_opus_payload(payload, url)
    source_hash = sha256_bytes(json.dumps(parsed["item"], ensure_ascii=False, sort_keys=True).encode("utf-8"))
    title = parsed["title"]
    out_path = output_path_for(output_dir, f"BILI-OPUS-{slugify(title, opus_id)}_{opus_id}.md", output_filename)
    if should_skip_url(manifest, url, out_path, source_hash, overwrite, download_assets and bool(parsed["images"])):
        return out_path, {}, True

    body_lines = [parsed["content"]]
    for index, image_url in enumerate(parsed["images"], 1):
        body_lines.extend(["", f"![动态图片 {index}]({image_url})"])
    extracted = "\n".join(body_lines).strip()
    extracted, assets = download_markdown_assets(extracted, out_path, url, cfg, download_assets)
    downloaded_assets = [item for item in assets if item.get("status") == "downloaded"]
    failed_assets = [item for item in assets if item.get("status") == "failed"]
    assets_downloaded = bool(download_assets) and bool(parsed["images"]) and not failed_assets
    asset_dir = ""
    if downloaded_assets:
        first_asset = pathlib.Path(str(downloaded_assets[0]["path"]))
        asset_dir = first_asset.parent.as_posix()

    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "bilibili-opus",
        "source_path": "",
        "source_url": url,
        "dynamic_id": opus_id,
        "author": parsed["author"],
        "author_mid": parsed["author_mid"],
        "published": parsed["published"],
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/bilibili", "source/bilibili-opus", "status/draft"],
        "source_hash": source_hash,
        "assets_downloaded": assets_downloaded,
        "asset_count": len(downloaded_assets),
        "asset_failed": len(failed_assets),
    }
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 动态链接：{url}",
        f"- 动态 ID：`{opus_id}`",
        f"- 作者/账号：{parsed['author'] or '未知'}",
        f"- 作者 MID：`{parsed['author_mid'] or '未知'}`",
        f"- 发布时间：{parsed['published'] or '未知'}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`Bilibili dynamic API` + `convert_sources_to_md.py`",
        f"- 图片资产：{'无图片' if not parsed['images'] else ('已下载' if assets_downloaded else ('部分失败' if failed_assets else '未下载'))}",
        f"- 资产目录：`{asset_dir or '无'}`",
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
        "source_type": "bilibili-opus",
        "source_hash": source_hash,
        "source_size": len(extracted.encode("utf-8")),
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "assets_downloaded": assets_downloaded,
        "asset_count": len(downloaded_assets),
        "asset_failed": len(failed_assets),
        "assets": assets,
        "error": "",
    }
    return out_path, item, False


def convert_local_html(
    path: pathlib.Path,
    output_dir: pathlib.Path,
    model: str,
    cfg: dict[str, str],
    manifest: dict[str, Any],
    overwrite: bool,
    download_assets: bool,
    output_filename: str = "",
) -> tuple[pathlib.Path, dict[str, Any], bool]:
    raw_html = read_text_with_fallback(path)
    source_hash = sha256_file(path)
    doc = parse_html(raw_html)
    final_url = path.resolve().as_uri()
    article_node = select_article_node(doc, raw_html, final_url)
    metadata = webpage_metadata(doc, raw_html, final_url, final_url)
    title = metadata["title"] or path.stem
    extracted = webpage_markdown(article_node, final_url)
    if not extracted:
        raise RuntimeError("未能从本地 HTML 中抽取到正文")
    source_bytes = extracted.encode("utf-8")
    out_path = output_path_for(output_dir, f"HTML-{slugify(title, path.stem)}.md", output_filename)
    has_assets = "file://" in extracted or bool(markdown_image_urls(extracted))
    if should_skip(manifest, path, out_path, source_hash, overwrite, download_assets and has_assets):
        return out_path, {}, True

    extracted, assets = download_markdown_assets(extracted, out_path, final_url, cfg, download_assets)
    downloaded_assets = [item for item in assets if item.get("status") == "downloaded"]
    failed_assets = [item for item in assets if item.get("status") == "failed"]
    assets_downloaded = bool(download_assets) and not failed_assets
    asset_dir = ""
    if downloaded_assets:
        first_asset = pathlib.Path(str(downloaded_assets[0]["path"]))
        asset_dir = first_asset.parent.as_posix()

    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "local-html",
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/local-html", "status/draft"],
        "source_hash": source_hash,
        "assets_downloaded": assets_downloaded,
        "asset_count": len(downloaded_assets),
        "asset_failed": len(failed_assets),
    }
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 源文件：`{rel(path)}`",
        "- 文件类型：本地 HTML",
        f"- 作者：{metadata['author'] or '未知'}",
        f"- 发布时间：{metadata['published'] or '未知'}",
        f"- 描述：{metadata['description'] or '无'}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`lxml` + `convert_sources_to_md.py`",
        f"- 图片资产：{'已本地化' if assets_downloaded else ('部分失败' if failed_assets else '未本地化')}",
        f"- 资产目录：`{asset_dir or '无'}`",
        "",
        "## 待整理区",
        "",
        "> 这是本地 HTML 转换草稿。Qwen 整理建议重点核对标题层级、图片说明、表格和外部引用。",
        "",
        "## 原文抽取",
        "",
        extracted,
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_url": "",
        "source_type": "local-html",
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
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
    enable_ocr: bool = False,
    output_filename: str = "",
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

    ocr_used = False
    ocr_error = ""
    extracted_chars = sum(len(re.sub(r"\s+", "", text)) for _, text in page_texts)
    sparse_pages = sum(1 for _, text in page_texts if len(re.sub(r"\s+", "", text)) < 30)
    if enable_ocr and page_texts and (extracted_chars < 160 or sparse_pages >= max(1, len(page_texts) // 2)):
        try:
            ocr_page_texts = ocr_document(path, cfg, max_pages=pages_to_extract)
            if ocr_page_texts:
                replaced_pages: list[tuple[int, str]] = []
                for index, (page_number, text) in enumerate(page_texts):
                    ocr_text = ocr_page_texts[index] if index < len(ocr_page_texts) else ""
                    if len(re.sub(r"\s+", "", text)) < 30 and len(re.sub(r"\s+", "", ocr_text)) >= 30:
                        replaced_pages.append((page_number, clean_text(ocr_text)))
                        ocr_used = True
                    else:
                        replaced_pages.append((page_number, text))
                page_texts = replaced_pages
        except Exception as exc:
            ocr_error = str(exc)

    for page_number, text in page_texts:
        body.append(f"## Page {page_number}\n\n{text or '> 本页未抽取到可读文本。'}")

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

    out_path = output_path_for(output_dir, f"PDF-{slugify(title, path.stem)}.md", output_filename)
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
        f"- OCR 回退：{'已启用并生效' if ocr_used else ('已尝试但未生效' if enable_ocr and ocr_error else ('已启用但无需回退' if enable_ocr else '未启用'))}",
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
        "ocr_used": ocr_used,
        "ocr_error": ocr_error,
        "error": "",
    }
    return out_path, item


def extract_pdf_page_images(path: pathlib.Path, max_pages: int | None = None) -> list[pathlib.Path]:
    if PdfReader is None:
        raise RuntimeError("pypdf is not available for scanned PDF image extraction")
    reader = PdfReader(str(path))
    image_paths: list[pathlib.Path] = []
    temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="local-note-pdf-images-"))
    limit = min(len(reader.pages), max_pages or len(reader.pages))
    for page_index in range(limit):
        page = reader.pages[page_index]
        images = list(getattr(page, "images", []) or [])
        if not images:
            continue
        image = max(images, key=lambda item: len(getattr(item, "data", b"") or b""))
        name = getattr(image, "name", f"page-{page_index + 1}.jpg")
        suffix = pathlib.Path(name).suffix.lower() or ".jpg"
        out_path = temp_dir / f"page-{page_index + 1:03d}{suffix}"
        out_path.write_bytes(image.data)
        image_paths.append(out_path)
    return image_paths


def emit_progress(
    phase: str,
    current: int,
    total: int,
    *,
    backend: str = "",
    label: str = "",
    resumed: bool = False,
) -> None:
    payload = {
        "phase": phase,
        "current": current,
        "total": total,
        "backend": backend,
        "label": label,
        "resumed": resumed,
    }
    print("PROGRESS_JSON:" + json.dumps(payload, ensure_ascii=False), flush=True)


def ocr_checkpoint_path(path: pathlib.Path, backend: str) -> pathlib.Path:
    configured = os.environ.get("OCR_CHECKPOINT_DIR", "").strip()
    root = pathlib.Path(configured).expanduser() if configured else pathlib.Path.home() / "Library/Application Support/Local Note Studio/state/ocr-checkpoints"
    signature = f"{path.resolve()}:{path.stat().st_size}:{path.stat().st_mtime_ns}:{backend}"
    return root / f"{hashlib.sha256(signature.encode('utf-8')).hexdigest()}.json"


def load_ocr_checkpoint(path: pathlib.Path, backend: str, total: int) -> list[str | None]:
    pages: list[str | None] = [None] * total
    if not parse_bool(os.environ.get("OCR_RESUME", "true")):
        return pages
    checkpoint = ocr_checkpoint_path(path, backend)
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        saved = payload.get("pages", [])
        if payload.get("total") == total and isinstance(saved, list):
            for index, text in enumerate(saved[:total]):
                if isinstance(text, str):
                    pages[index] = text
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return pages


def save_ocr_checkpoint(path: pathlib.Path, backend: str, pages: list[str | None]) -> None:
    checkpoint = ocr_checkpoint_path(path, backend)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    temp = checkpoint.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"source": str(path.resolve()), "backend": backend, "total": len(pages), "pages": pages}, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(checkpoint)


def recognize_ocr_pages(
    source: pathlib.Path,
    backend: str,
    page_images: list[pathlib.Path],
    recognize: Any,
) -> list[str]:
    total = len(page_images)
    pages = load_ocr_checkpoint(source, backend, total)
    completed = sum(text is not None for text in pages)
    if completed:
        emit_progress("OCR", completed, total, backend=backend, label="读取已完成页", resumed=True)
    for index, image_path in enumerate(page_images):
        if pages[index] is not None:
            continue
        emit_progress("OCR", index, total, backend=backend, label=f"识别第 {index + 1} 页")
        pages[index] = clean_text(str(recognize(image_path) or ""))
        save_ocr_checkpoint(source, backend, pages)
        emit_progress("OCR", index + 1, total, backend=backend, label=f"第 {index + 1} 页完成")
    return [text or "" for text in pages]


def ocr_document(path: pathlib.Path, cfg: dict[str, str], max_pages: int | None = None) -> list[str]:
    errors: list[str] = []
    try:
        return ocr_with_qwen(cfg, path, max_pages=max_pages)
    except Exception as exc:
        detail = compact_error_detail(exc)
        errors.append(f"qwen vision backend failed: {detail}")
        emit_progress("OCR 回退", 0, 1, backend="Qwen Vision", label=detail)
    if shutil.which("tesseract"):
        try:
            return ocr_with_tesseract(path, max_pages=max_pages)
        except Exception as exc:
            detail = compact_error_detail(exc)
            errors.append(f"tesseract backend failed: {detail}")
            emit_progress("OCR 回退", 0, 1, backend="Tesseract/Poppler", label=detail)
    try:
        return ocr_with_swift(path, max_pages=max_pages)
    except Exception as exc:
        detail = compact_error_detail(exc)
        errors.append(f"macOS Vision backend failed: {detail}")
        emit_progress("OCR 失败", 0, 1, backend="macOS Vision", label=detail)
    raise RuntimeError(
        "OCR backend unavailable. "
        "Enable a multimodal Qwen/OpenAI-compatible model, or install `tesseract` (and `pdftoppm` for scanned PDFs), or fix local Xcode/Swift toolchain for the Vision fallback. "
        + " | ".join(errors)
    )


def ocr_with_qwen(cfg: dict[str, str], path: pathlib.Path, max_pages: int | None = None) -> list[str]:
    api_base = cfg.get("DEFAULT_LLM_API_BASE", "").strip()
    model = cfg.get("DEFAULT_LLM_MODEL", "").strip()
    if not api_base or not model:
        raise RuntimeError("LLM API base or model is not configured")
    if path.suffix.lower() in IMAGE_SOURCE_EXTS:
        emit_progress("OCR", 0, 1, backend="Qwen Vision", label=path.name)
        text = vision_ocr_image(cfg, path)
        emit_progress("OCR", 1, 1, backend="Qwen Vision", label=path.name)
        return [text]
    if path.suffix.lower() != ".pdf":
        raise RuntimeError(f"unsupported Qwen OCR source: {path}")
    image_paths = extract_pdf_page_images(path, max_pages=max_pages)
    if not image_paths:
        raise RuntimeError("no page images found in PDF; cannot run vision OCR")
    try:
        return recognize_ocr_pages(path, "Qwen Vision", image_paths, lambda image_path: vision_ocr_image(cfg, image_path))
    finally:
        temp_dir = image_paths[0].parent if image_paths else None
        if temp_dir and temp_dir.name.startswith("local-note-pdf-images-"):
            shutil.rmtree(temp_dir, ignore_errors=True)


def ocr_with_tesseract(path: pathlib.Path, max_pages: int | None = None) -> list[str]:
    langs = os.environ.get("OCR_LANGS", "chi_sim+eng")
    if path.suffix.lower() in IMAGE_SOURCE_EXTS:
        emit_progress("OCR", 0, 1, backend="Tesseract", label=path.name)
        result = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", langs],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(compact_error_detail(result.stderr or result.stdout or "tesseract failed"))
        emit_progress("OCR", 1, 1, backend="Tesseract", label=path.name)
        return [clean_text(result.stdout)]
    if path.suffix.lower() != ".pdf":
        raise RuntimeError(f"unsupported tesseract OCR source: {path}")
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm not found for scanned PDF rasterization")

    with tempfile.TemporaryDirectory(prefix="local-note-ocr-") as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        prefix = temp_path / "page"
        command = ["pdftoppm", "-png"]
        if max_pages:
            command.extend(["-f", "1", "-l", str(max_pages)])
        command.extend([str(path), str(prefix)])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(compact_error_detail(result.stderr or result.stdout or "pdftoppm failed"))
        images = sorted(temp_path.glob("page-*.png"))

        def recognize(image_path: pathlib.Path) -> str:
            page_result = subprocess.run(
                ["tesseract", str(image_path), "stdout", "-l", langs],
                capture_output=True,
                text=True,
                check=False,
            )
            if page_result.returncode != 0:
                raise RuntimeError(compact_error_detail(page_result.stderr or page_result.stdout or "tesseract failed"))
            return page_result.stdout

        return recognize_ocr_pages(path, "Tesseract/Poppler", images, recognize)


def ocr_with_swift(path: pathlib.Path, max_pages: int | None = None) -> list[str]:
    script = pathlib.Path(__file__).with_name("macos_ocr.swift")
    if not script.exists():
        raise RuntimeError(f"OCR script not found: {script}")
    with tempfile.TemporaryDirectory(prefix="local-note-swift-ocr-") as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        binary_path = temp_path / "macos-ocr"
        module_cache = temp_path / "module-cache"
        module_cache.mkdir(parents=True, exist_ok=True)
        compile_command = [
            "xcrun",
            "swiftc",
            "-module-cache-path",
            str(module_cache),
            str(script),
            "-o",
            str(binary_path),
        ]
        compile_result = subprocess.run(compile_command, capture_output=True, text=True, check=False)
        if compile_result.returncode != 0:
            raise RuntimeError(compact_error_detail(compile_result.stderr or compile_result.stdout or "swift compile failed"))
        command = [str(binary_path), "--source", str(path)]
        if max_pages:
            command.extend(["--max-pages", str(max_pages)])
        total = max_pages or 1
        emit_progress("OCR", 0, total, backend="macOS Vision", label="启动系统 Vision 识别")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(compact_error_detail(result.stderr or result.stdout or "OCR command failed"))
        payload = json.loads(result.stdout or "{}")
        pages = payload.get("pages")
        if isinstance(pages, list):
            texts = [clean_text(str(item.get("text") or "")) for item in pages if isinstance(item, dict)]
            emit_progress("OCR", len(texts), len(texts) or total, backend="macOS Vision", label="识别完成")
            return texts
        text = clean_text(str(payload.get("text") or ""))
        emit_progress("OCR", 1 if text else 0, 1, backend="macOS Vision", label="识别完成")
        return [text] if text else []


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


def convert_docx(
    path: pathlib.Path,
    output_dir: pathlib.Path,
    model: str,
    *,
    source_type: str = "docx",
    source_path: pathlib.Path | None = None,
    source_hash: str | None = None,
    source_size: int | None = None,
    conversion_tool: str = "`zipfile` + `ElementTree`",
    output_filename: str = "",
) -> tuple[pathlib.Path, dict[str, Any]]:
    source_path = source_path or path
    source_hash = source_hash or sha256_file(source_path)
    source_size = source_size if source_size is not None else source_path.stat().st_size
    prefix = "DOC" if source_type == "doc" else "DOCX"
    out_path = output_path_for(output_dir, f"{prefix}-{slugify(source_path.stem)}.md", output_filename)
    assets_dir = output_dir / "assets" / out_path.stem
    body, props, new_images, image_count = docx_body_markdown(path, assets_dir)
    title = props.get("title") or source_path.stem
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": source_type,
        "source_path": rel(source_path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": [f"source/{source_type}", "status/draft"],
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
        f"- 源文件：`{rel(source_path)}`",
        f"- 文件类型：{source_type.upper()}",
        f"- 作者：{props.get('creator') or '未知'}",
        f"- 创建时间：{props.get('created') or '未知'}",
        f"- 修改时间：{props.get('modified') or '未知'}",
        f"- 图片资产：{image_count} 个",
        f"- 新增图片：{new_images} 个",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        f"- 转换工具：{conversion_tool}",
        "",
        "## 待整理区",
        "",
        f"> 这是 {source_type.upper()} 转换草稿。正式入库前建议核对标题层级、列表、表格和图片顺序。",
        "",
        "## 原文抽取",
        "",
        body or "> 未抽取到正文。",
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(source_path),
        "source_type": source_type,
        "source_hash": source_hash,
        "source_size": source_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "title": title,
        "asset_count": image_count,
        "error": "",
    }
    return out_path, item


def convert_doc(path: pathlib.Path, output_dir: pathlib.Path, model: str, output_filename: str = "") -> tuple[pathlib.Path, dict[str, Any]]:
    textutil = shutil.which("textutil")
    if not textutil:
        raise RuntimeError("unsupported .doc conversion: macOS textutil command not found")
    source_hash = sha256_file(path)
    source_size = path.stat().st_size
    with tempfile.TemporaryDirectory(prefix="local-note-doc-") as temp_dir:
        temp_path = pathlib.Path(temp_dir)
        docx_path = temp_path / f"{path.stem}.docx"
        command = [textutil, "-convert", "docx", "-output", str(docx_path), str(path)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0 or not docx_path.exists():
            message = (result.stderr or result.stdout or "unknown textutil error").strip()
            raise RuntimeError(f".doc conversion failed via textutil: {message}")
        return convert_docx(
            docx_path,
            output_dir,
            model,
            source_type="doc",
            source_path=path,
            source_hash=source_hash,
            source_size=source_size,
            conversion_tool="`textutil` -> `zipfile` + `ElementTree`",
            output_filename=output_filename,
        )


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return "> 未抽取到表格数据。"
    width = max(len(row) for row in rows)
    normalized = [[cell.replace("|", "\\|") for cell in row] + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


def convert_csv_like(
    path: pathlib.Path,
    output_dir: pathlib.Path,
    model: str,
    delimiter: str,
    source_type: str,
    output_filename: str = "",
) -> tuple[pathlib.Path, dict[str, Any]]:
    source_hash = sha256_file(path)
    text = read_text_with_fallback(path)
    rows = [row for row in csv_reader(text, delimiter)]
    out_path = output_path_for(output_dir, f"{source_type.upper()}-{slugify(path.stem)}.md", output_filename)
    title = path.stem
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": source_type,
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": [f"source/{source_type}", "status/draft"],
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
        f"- 文件类型：{source_type.upper()}",
        f"- 行数：{len(rows)}",
        f"- 列数：{max((len(row) for row in rows), default=0)}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`csv`",
        "",
        "## 待整理区",
        "",
        "> 这是表格源文件转换草稿。Qwen 整理时建议提炼字段含义、业务口径、异常值和可复用结论。",
        "",
        "## 原文抽取",
        "",
        markdown_table(rows),
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_type": source_type,
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "title": title,
        "error": "",
    }
    return out_path, item


def csv_reader(text: str, delimiter: str) -> list[list[str]]:
    import csv

    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows: list[list[str]] = []
    for row in reader:
        cleaned = [clean_text(cell) for cell in row]
        if any(cell for cell in cleaned):
            rows.append(cleaned)
    return rows


def xlsx_shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    return [compact_text("".join(node.itertext())) for node in root.findall(".//s:si", XLSX_NS)]


def xlsx_sheet_targets(book: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    rels_root = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    rels = {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in rels_root.findall("rel:Relationship", XLSX_NS)
    }
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall("s:sheets/s:sheet", XLSX_NS):
        name = sheet.attrib.get("name", "Sheet")
        rid = sheet.attrib.get(f"{{{XLSX_NS['r']}}}id", "")
        target = rels.get(rid, "")
        if target:
            sheets.append((name, "xl/" + target.lstrip("/")))
    return sheets


def xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return compact_text("".join(node.text or "" for node in cell.findall(".//s:t", XLSX_NS)))
    value = compact_text(cell.findtext("s:v", default="", namespaces=XLSX_NS))
    if cell_type == "s":
        try:
            return shared[int(value)]
        except Exception:
            return value
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def xlsx_col_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    total = 0
    for ch in letters:
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return max(total - 1, 0)


def xlsx_rows(path: pathlib.Path) -> list[tuple[str, list[list[str]]]]:
    with zipfile.ZipFile(path) as book:
        shared = xlsx_shared_strings(book)
        sheets = xlsx_sheet_targets(book)
        output: list[tuple[str, list[list[str]]]] = []
        for sheet_name, sheet_target in sheets:
            if sheet_target not in book.namelist():
                continue
            root = ET.fromstring(book.read(sheet_target))
            rows: list[list[str]] = []
            for row in root.findall(".//s:sheetData/s:row", XLSX_NS):
                values: list[str] = []
                for cell in row.findall("s:c", XLSX_NS):
                    index = xlsx_col_index(cell.attrib.get("r", "A1"))
                    while len(values) < index:
                        values.append("")
                    values.append(xlsx_cell_value(cell, shared))
                if any(value.strip() for value in values):
                    rows.append(values)
            output.append((sheet_name, rows))
        return output


def convert_xlsx(path: pathlib.Path, output_dir: pathlib.Path, model: str, output_filename: str = "") -> tuple[pathlib.Path, dict[str, Any]]:
    source_hash = sha256_file(path)
    sheets = xlsx_rows(path)
    out_path = output_path_for(output_dir, f"XLSX-{slugify(path.stem)}.md", output_filename)
    title = path.stem
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "xlsx",
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/xlsx", "status/draft"],
        "source_hash": source_hash,
    }
    body = ["## 原文抽取", ""]
    for sheet_name, rows in sheets:
        body.extend([f"### 工作表：{sheet_name}", "", markdown_table(rows), ""])
    if len(body) == 2:
        body.append("> 未抽取到工作表内容。")
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 源文件：`{rel(path)}`",
        "- 文件类型：XLSX",
        f"- 工作表数量：{len(sheets)}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`zipfile` + `ElementTree`",
        "",
        "## 待整理区",
        "",
        "> 这是 Excel 转换草稿。Qwen 整理时建议说明各工作表之间的关系、关键字段和异常数据。",
        "",
        *body,
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_type": "xlsx",
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "title": title,
        "error": "",
    }
    return out_path, item


def pptx_slide_targets(book: zipfile.ZipFile) -> list[str]:
    presentation = ET.fromstring(book.read("ppt/presentation.xml"))
    rels_root = ET.fromstring(book.read("ppt/_rels/presentation.xml.rels"))
    rels = {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in rels_root.findall("rel:Relationship", PPTX_NS)
    }
    targets: list[str] = []
    for slide in presentation.findall("p:sldIdLst/p:sldId", PPTX_NS):
        rid = slide.attrib.get(f"{{{PPTX_NS['r']}}}id", "")
        target = rels.get(rid, "")
        if target:
            targets.append("ppt/" + target.lstrip("/"))
    return targets


def pptx_slide_texts(path: pathlib.Path) -> list[tuple[int, str]]:
    with zipfile.ZipFile(path) as book:
        output: list[tuple[int, str]] = []
        for index, target in enumerate(pptx_slide_targets(book), 1):
            if target not in book.namelist():
                continue
            root = ET.fromstring(book.read(target))
            texts = [compact_text(node.text or "") for node in root.findall(".//a:t", PPTX_NS)]
            text = "\n".join(part for part in texts if part)
            output.append((index, clean_text(text)))
        return output


def convert_pptx(path: pathlib.Path, output_dir: pathlib.Path, model: str, output_filename: str = "") -> tuple[pathlib.Path, dict[str, Any]]:
    source_hash = sha256_file(path)
    slides = pptx_slide_texts(path)
    out_path = output_path_for(output_dir, f"PPTX-{slugify(path.stem)}.md", output_filename)
    title = path.stem
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "pptx",
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/pptx", "status/draft"],
        "source_hash": source_hash,
    }
    body = ["## 原文抽取", ""]
    for slide_number, text in slides:
        body.extend([f"### Slide {slide_number}", "", text or "> 本页未抽取到文本。", ""])
    if len(body) == 2:
        body.append("> 未抽取到幻灯片文本。")
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 源文件：`{rel(path)}`",
        "- 文件类型：PPTX",
        f"- 幻灯片数量：{len(slides)}",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`zipfile` + `ElementTree`",
        "",
        "## 待整理区",
        "",
        "> 这是 PowerPoint 转换草稿。Qwen 整理时建议补出每页主旨、演示顺序和结论。",
        "",
        *body,
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_type": "pptx",
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "title": title,
        "error": "",
    }
    return out_path, item


def copy_source_image(path: pathlib.Path, out_path: pathlib.Path) -> str:
    asset_dir_rel = pathlib.Path("assets") / out_path.stem
    asset_dir = out_path.parent / asset_dir_rel
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / f"source{path.suffix.lower()}"
    if not target.exists():
        shutil.copy2(path, target)
    return (asset_dir_rel / target.name).as_posix()


def convert_image_ocr(path: pathlib.Path, output_dir: pathlib.Path, model: str, cfg: dict[str, str], output_filename: str = "") -> tuple[pathlib.Path, dict[str, Any]]:
    source_hash = sha256_file(path)
    out_path = output_path_for(output_dir, f"IMAGE-{slugify(path.stem)}.md", output_filename)
    image_md_path = copy_source_image(path, out_path)
    ocr_pages = ocr_document(path, cfg, max_pages=1)
    ocr_text = ocr_pages[0] if ocr_pages else ""
    if not ocr_text:
        raise RuntimeError("OCR 未抽取到可用文本")
    title = path.stem
    meta = {
        "title": title,
        "type": "source-conversion",
        "source_type": "image-ocr",
        "source_path": rel(path),
        "source_url": "",
        "created": today(),
        "updated": today(),
        "status": "draft",
        "model": model,
        "tags": ["source/image", "status/draft"],
        "source_hash": source_hash,
        "asset_count": 1,
    }
    markdown = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        "## 来源信息",
        "",
        f"- 源文件：`{rel(path)}`",
        "- 文件类型：图片 OCR",
        f"- SHA256：`{source_hash}`",
        f"- 转换时间：{now_iso()}",
        "- 转换工具：`Vision OCR`",
        "",
        "## 待整理区",
        "",
        "> 这是图片 OCR 转换草稿。Qwen 整理时建议核对专有名词、数字、表格边界和版式顺序。",
        "",
        "## 原图",
        "",
        f"![原图]({image_md_path})",
        "",
        "## 原文抽取",
        "",
        ocr_text,
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(markdown), encoding="utf-8")
    item = {
        "source_path": rel(path),
        "source_type": "image-ocr",
        "source_hash": source_hash,
        "source_size": path.stat().st_size,
        "output_path": rel(out_path),
        "status": "converted",
        "converted_at": now_iso(),
        "model": model,
        "title": title,
        "asset_count": 1,
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


def convert_conversation(path: pathlib.Path, output_dir: pathlib.Path, model: str, output_filename: str = "") -> tuple[pathlib.Path, dict[str, Any]]:
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

    out_path = output_path_for(output_dir, f"CHAT-{slugify(title, path.stem)}.md", output_filename)
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
    source_value = rel(source)
    output_value = rel(output)
    for item in manifest.get("items", []):
        if not isinstance(item, dict):
            continue
        if item.get("source_path") == source_value or item.get("source_hash") == source_hash or item.get("output_path") == output_value:
            item["last_action"] = "skipped"
            item["last_checked_at"] = now_iso()
            break
    return True


def discover_sources(source_dir: pathlib.Path, sample: bool, cfg: dict[str, str]) -> list[pathlib.Path]:
    pdfs = sorted(source_dir.glob("AI_paper/*.pdf"))
    conversations = sorted(source_dir.glob("AI-Chat/LM-Studio/**/*.conversation.json"))
    docx_files = sorted([*source_dir.glob("**/*.docx"), *source_dir.glob("**/*.doc")])
    tabular_files = sorted([*source_dir.glob("**/*.csv"), *source_dir.glob("**/*.tsv"), *source_dir.glob("**/*.xlsx")])
    slides = sorted(source_dir.glob("**/*.pptx"))
    local_html = sorted([*source_dir.glob("**/*.html"), *source_dir.glob("**/*.htm")])
    images = sorted([path for ext in IMAGE_SOURCE_EXTS for path in source_dir.glob(f"**/*{ext}")])
    if sample:
        pdfs = pdfs[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_PDF"])]
        conversations = conversations[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_JSON"])]
        docx_files = docx_files[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX"])]
        tabular_files = tabular_files[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX"])]
        slides = slides[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX"])]
        local_html = local_html[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX"])]
        images = images[: int(cfg["SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX"])]
    return [*pdfs, *conversations, *docx_files, *tabular_files, *slides, *local_html, *images]


def main() -> int:
    cfg = config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="convert a small configured sample set")
    parser.add_argument("--all-pages", action="store_true", help="extract all PDF pages")
    parser.add_argument("--qwen-polish-pdf", action="store_true", help="ask the configured local Qwen model to polish PDF extraction into Markdown")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing outputs")
    parser.add_argument("--source", action="append", help="specific source path to convert")
    parser.add_argument("--source-dir", help="recursively convert supported documents and images from one directory")
    parser.add_argument("--url", action="append", help="web page URL to convert, including WeChat public-account articles")
    parser.add_argument("--bilibili-up-opus", help="Bilibili space opus URL or UP UID to batch convert")
    parser.add_argument("--limit", type=int, default=0, help="maximum Bilibili UP opus posts to process; 0 means all")
    parser.add_argument("--output-filename", default="", help="custom Markdown file name for one source/URL; directory separators are not allowed")
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

    source_dir = (ROOT / (args.source_dir or cfg["SOURCE_DIR"])).resolve()
    output_dir = (ROOT / args.output_dir).resolve()
    index_dir = (ROOT / cfg["INDEX_DIR"]).resolve()
    manifest_path = index_dir / "source-manifest.json"
    manifest_enabled = not parse_bool(cfg.get("LOCAL_NOTE_STUDIO_INCOGNITO", "false"))
    manifest = load_manifest(manifest_path) if manifest_enabled else {"items": []}
    model = cfg["DEFAULT_LLM_MODEL"]
    max_pages = None if args.all_pages else int(cfg["SOURCE_CONVERSION_MAX_PDF_PAGES"])
    download_assets = parse_bool(cfg["WEB_DOWNLOAD_ASSETS"]) if args.download_assets is None else args.download_assets

    if args.limit < 0:
        parser.error("--limit 不能小于 0")
    if args.bilibili_up_opus and (args.source or args.source_dir or args.url):
        parser.error("--bilibili-up-opus 不能和 --source/--source-dir/--url 同时使用")
    urls = args.url or []
    if args.bilibili_up_opus:
        urls = fetch_bilibili_space_opus_urls(args.bilibili_up_opus, cfg, args.limit)
        print(f"发现 {len(urls)} 条 UP 主图文，开始逐条转换。")
    if args.source:
        sources = [(ROOT / item).resolve() for item in args.source]
    elif args.source_dir:
        sources = discover_sources(source_dir, False, cfg)
    elif urls:
        sources = []
    else:
        sources = discover_sources(source_dir, args.sample, cfg)
    if args.output_filename and (args.bilibili_up_opus or len(sources) + len(urls) != 1):
        parser.error("--output-filename 只能用于单个 source 或 URL")

    converted = 0
    skipped = 0
    failed = 0
    pdf_cooldown_delay = float(cfg.get("QWEN_PDF_POLISH_COOLDOWN_DELAY") or 0)
    for source_index, source in enumerate(sources, start=1):
        try:
            emit_progress("文件转换", source_index - 1, len(sources), backend="source", label=source.name)
            if not source.exists():
                raise FileNotFoundError(source)
            source_hash = sha256_file(source)
            if source.suffix.lower() == ".pdf":
                probe_reader = PdfReader(str(source)) if PdfReader is not None else None
                title = pdf_title(probe_reader, source) if probe_reader is not None else source.stem
                output_path = output_path_for(output_dir, f"PDF-{slugify(title, source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite, args.qwen_polish_pdf):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_pdf(
                    source,
                    output_dir,
                    model,
                    max_pages,
                    cfg,
                    args.qwen_polish_pdf,
                    parse_bool(cfg.get("ENABLE_OCR", "false")),
                    args.output_filename,
                )
            elif source.name.endswith(".conversation.json"):
                title = str(json.loads(source.read_text(encoding="utf-8")).get("name") or source.stem)
                output_path = output_path_for(output_dir, f"CHAT-{slugify(title, source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_conversation(source, output_dir, model, args.output_filename)
            elif source.suffix.lower() == ".docx":
                output_path = output_path_for(output_dir, f"DOCX-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_docx(source, output_dir, model, output_filename=args.output_filename)
            elif source.suffix.lower() == ".doc":
                output_path = output_path_for(output_dir, f"DOC-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_doc(source, output_dir, model, args.output_filename)
            elif source.suffix.lower() == ".csv":
                output_path = output_path_for(output_dir, f"CSV-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_csv_like(source, output_dir, model, ",", "csv", args.output_filename)
            elif source.suffix.lower() == ".tsv":
                output_path = output_path_for(output_dir, f"TSV-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_csv_like(source, output_dir, model, "\t", "tsv", args.output_filename)
            elif source.suffix.lower() == ".xlsx":
                output_path = output_path_for(output_dir, f"XLSX-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_xlsx(source, output_dir, model, args.output_filename)
            elif source.suffix.lower() == ".pptx":
                output_path = output_path_for(output_dir, f"PPTX-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_pptx(source, output_dir, model, args.output_filename)
            elif source.suffix.lower() in {".html", ".htm"}:
                output_path = output_path_for(output_dir, f"HTML-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item, _ = convert_local_html(source, output_dir, model, cfg, manifest, args.overwrite, download_assets, args.output_filename)
            elif source.suffix.lower() in IMAGE_SOURCE_EXTS:
                output_path = output_path_for(output_dir, f"IMAGE-{slugify(source.stem)}.md", args.output_filename)
                if should_skip(manifest, source, output_path, source_hash, args.overwrite):
                    skipped += 1
                    print(f"skip {rel(source)}")
                    continue
                out_path, item = convert_image_ocr(source, output_dir, model, cfg, args.output_filename)
            else:
                raise ValueError(f"unsupported source type: {source}")
            update_manifest(manifest, item)
            converted += 1
            print(f"converted {rel(source)} -> {rel(out_path)}")
            emit_progress("文件转换", source_index, len(sources), backend=str(item.get("source_type") or source.suffix), label=source.name)
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
    for url_index, url in enumerate(urls, 1):
        if args.bilibili_up_opus:
            print(f"[抓取 {url_index}/{len(urls)}] 开始：动态 {bilibili_opus_id(url)}")
        try:
            if is_bilibili_opus_url(url):
                out_path, item, did_skip = convert_bilibili_opus(url, output_dir, model, cfg, manifest, args.overwrite, download_assets, args.output_filename)
            else:
                out_path, item, did_skip = convert_webpage(url, output_dir, model, cfg, manifest, args.overwrite, download_assets, args.output_filename)
            if did_skip:
                skipped += 1
                if args.bilibili_up_opus:
                    print(f"[抓取 {url_index}/{len(urls)}] 已存在，跳过")
                else:
                    print(f"skip {url}")
                continue
            update_manifest(manifest, item)
            converted += 1
            if args.bilibili_up_opus:
                print(f"[抓取 {url_index}/{len(urls)}] 完成：{out_path.name}")
            else:
                print(f"converted {url} -> {rel(out_path)}")
        except Exception as exc:
            failed += 1
            source_type = "bilibili-opus" if is_bilibili_opus_url(url) else "webpage"
            error_item = {
                "source_path": "",
                "source_url": url,
                "source_type": source_type,
                "source_hash": "",
                "source_size": 0,
                "output_path": "",
                "status": "failed",
                "converted_at": now_iso(),
                "model": model,
                "error": str(exc),
            }
            update_manifest(manifest, error_item)
            if args.bilibili_up_opus:
                print(f"[抓取 {url_index}/{len(urls)}] 失败：{exc}", file=sys.stderr)
            else:
                print(f"failed {url}: {exc}", file=sys.stderr)
        if args.bilibili_up_opus and url_index < len(urls):
            request_delay = float(cfg.get("BILIBILI_OPUS_REQUEST_DELAY_SECONDS") or 0)
            if request_delay > 0:
                time.sleep(request_delay)
    if manifest_enabled:
        save_manifest(manifest_path, manifest)
    if args.bilibili_up_opus:
        print(f"抓取阶段完成：成功 {converted}，跳过 {skipped}，失败 {failed}。")
    else:
        manifest_label = rel(manifest_path) if manifest_enabled else "disabled (incognito)"
        print(f"done converted={converted} skipped={skipped} failed={failed} manifest={manifest_label}")
    if args.bilibili_up_opus and failed and converted:
        print(f"批量任务部分完成：成功 {converted}，失败 {failed}。", file=sys.stderr)
        return 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
