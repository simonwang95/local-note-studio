#!/usr/bin/env python3
"""Command worker for Local Note Studio MVP tasks."""

from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "worker"
SCRIPTS_DIR = WORKER_DIR / "scripts"


REQUIRED_PYTHON_PACKAGES = {
    "pypdf": "pip install pypdf",
    "lxml": "pip install lxml",
    "requests": "pip install requests",
}

REQUIRED_COMMANDS = {
    "yt-dlp": "pip install yt-dlp",
    "ffmpeg": "Install ffmpeg with Homebrew (`brew install ffmpeg`) or conda.",
    "ffprobe": "Install ffmpeg with Homebrew (`brew install ffmpeg`) or conda.",
}

OPTIONAL_PYTHON_PACKAGES = {
    "mlx_whisper": "pip install mlx-whisper",
}

OPTIONAL_INFO_COMMANDS = {
    "opencc": "Install opencc if you want traditional-to-simplified conversion.",
    "pandoc": "Install pandoc if you want recursive Markdown-to-EPUB export.",
}

OCR_FALLBACK_COMMANDS = {
    "tesseract": "Install tesseract if you want OCR for images and scanned PDFs.",
    "pdftoppm": "Install poppler if you want OCR fallback for scanned PDFs.",
}

ASR_MODEL_HINT = "Set ASR_LOCAL_MODEL in worker/env.local when videos have no usable subtitles."


@dataclass
class TaskRequest:
    task: str
    source: str = ""
    output_dir: str = ""
    output_filename: str = ""
    conda_env: str = ""
    python_bin: str = "python3"
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    cookies: str = ""
    browser_profile: str = ""
    subtitle_strategy: str = "yt-dlp"
    favorite_limit: int = 1
    collection_type: str = "favorite"
    collection_id: str = ""
    collection_mid: str = ""
    retry_failed: bool = False
    extract_keyframes: bool = False
    dialogue_detection: bool = False
    keep_original_subtitles: bool = True
    recursive_search: bool = False
    overwrite_outputs: bool = False
    stock_terms: bool = False
    enable_ocr: bool = False
    dry_run: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TaskRequest":
        return cls(
            task=str(data.get("task") or ""),
            source=str(data.get("source") or ""),
            output_dir=str(data.get("output_dir") or ""),
            output_filename=str(data.get("output_filename") or ""),
            conda_env=str(data.get("conda_env") or ""),
            python_bin=str(data.get("python_bin") or "python3"),
            api_base=str(data.get("api_base") or ""),
            api_key=str(data.get("api_key") or ""),
            model=str(data.get("model") or ""),
            cookies=str(data.get("cookies") or ""),
            browser_profile=str(data.get("browser_profile") or ""),
            subtitle_strategy=str(data.get("subtitle_strategy") or "yt-dlp"),
            favorite_limit=parse_int(data.get("favorite_limit"), 1),
            collection_type=str(data.get("collection_type") or "favorite"),
            collection_id=str(data.get("collection_id") or ""),
            collection_mid=str(data.get("collection_mid") or ""),
            retry_failed=parse_bool(data.get("retry_failed")),
            extract_keyframes=parse_bool(data.get("extract_keyframes")),
            dialogue_detection=parse_bool(data.get("dialogue_detection")),
            keep_original_subtitles=parse_bool(data.get("keep_original_subtitles", True)),
            recursive_search=parse_bool(data.get("recursive_search")),
            overwrite_outputs=parse_bool(data.get("overwrite_outputs")),
            stock_terms=parse_bool(data.get("stock_terms")),
            enable_ocr=parse_bool(data.get("enable_ocr")),
            dry_run=bool(data.get("dry_run")),
        )


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


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_env(req: TaskRequest) -> dict[str, str]:
    env = os.environ.copy()
    env.update(load_env_file(WORKER_DIR / "env.local"))
    env["PYTHONUNBUFFERED"] = "1"
    if req.conda_env:
        env["CONDA_ENV"] = req.conda_env
    if req.api_base:
        env["DEFAULT_LLM_API_BASE"] = req.api_base
        env["SUMMARY_API_URL"] = req.api_base
    if req.api_key:
        env["DEFAULT_LLM_API_KEY"] = req.api_key
        env["SUMMARY_API_KEY"] = req.api_key
    if req.model:
        env["DEFAULT_LLM_MODEL"] = req.model
        env["SUMMARY_MODEL"] = req.model
    if req.cookies:
        env["BILIBILI_COOKIES_FILE"] = req.cookies
        env["BILI_COOKIE_FILE"] = req.cookies
    if req.output_dir:
        env["BILIBILI_OUTPUT_DIR"] = req.output_dir
    if req.collection_id:
        env["BILIBILI_FAV_MEDIA_ID"] = req.collection_id
    env["EXTRACT_KEYFRAMES"] = "true" if req.extract_keyframes else "false"
    env["ENABLE_DIALOGUE_DETECTION"] = "true" if req.dialogue_detection else "false"
    env["KEEP_ORIGINAL_SUBTITLES"] = "true" if req.keep_original_subtitles else "false"
    env["OVERWRITE_OUTPUT"] = "true" if req.overwrite_outputs else "false"
    env["A_SHARE_TERMS_ENABLED"] = "true" if req.stock_terms else "false"
    env["ENABLE_OCR"] = "true" if req.enable_ocr else "false"
    subtitle_strategy = (req.subtitle_strategy or "yt-dlp").strip().lower()
    if subtitle_strategy == "web":
        env["BILIBILI_PREFER_WEB_SUBTITLE"] = "true"
        env["FORCE_ASR"] = "false"
    elif subtitle_strategy == "asr":
        env["BILIBILI_PREFER_WEB_SUBTITLE"] = "false"
        env["FORCE_ASR"] = "true"
    else:
        env["BILIBILI_PREFER_WEB_SUBTITLE"] = "false"
        env["FORCE_ASR"] = "false"
    return env


def inspect_cookie_file(path: pathlib.Path) -> str:
    bilibili_lines = 0
    total_lines = 0
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        total_lines += 1
        if "bilibili.com" in line:
            bilibili_lines += 1
    stat = path.stat()
    modified = dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    return f"{path} (B站域 cookie {bilibili_lines}/{total_lines}，更新于 {modified})"


def resolve_local_path(raw: str, base: pathlib.Path = ROOT) -> pathlib.Path:
    path = pathlib.Path(os.path.expanduser(os.path.expandvars(raw)))
    if path.is_absolute():
        return path
    candidates = [base / path, WORKER_DIR / path]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def mirror_bilibili_auth_cookies(jar: http.cookiejar.CookieJar) -> None:
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


def bilibili_cookie_login_detail(path: pathlib.Path) -> tuple[bool, str]:
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    mirror_bilibili_auth_cookies(jar)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.bilibili.com/",
        },
    )
    try:
        with opener.open(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return False, f"登录态检查失败: {first_line(str(exc))}"
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if payload.get("code") == 0 and data.get("isLogin"):
        mid = data.get("mid") or "unknown"
        return True, f"已登录 mid={mid}"
    return False, f"未登录或已失效 (code={payload.get('code')})"


def check_bilibili_cookie(req: TaskRequest) -> str:
    raw = req.cookies.strip()
    if not raw:
        return "[WARN] Bilibili cookie file - 未配置\n"
    path = resolve_local_path(raw)
    if not path.exists():
        return f"[WARN] Bilibili cookie file - 文件不存在: {path}\n"
    login_ok, login_detail = bilibili_cookie_login_detail(path)
    status = "OK" if login_ok else "WARN"
    return f"[{status}] Bilibili cookie file - {inspect_cookie_file(path)}；{login_detail}\n"


def bilibili_error_category(code: object = None, message: str = "", http_status: int | None = None) -> str:
    """Return a stable, actionable category for Bilibili failures."""
    text = message.strip()
    if http_status == 412 or str(code) == "-412" or "412" in text or "请求被拦截" in text:
        return "接口风控/HTTP 412：请稍后再试；若持续出现，请从已登录 Chrome Profile 刷新 Cookie。"
    if str(code) in {"-101", "-111"} or "未登录" in text or "账号未登录" in text:
        return "未登录：Cookie 已失效或未包含登录凭据，请刷新 Cookie 后重新验证登录态。"
    if str(code) in {"-403", "11010", "11011"} or any(token in text for token in ("权限不足", "无权限", "充电", "仅粉丝")):
        return "账号无对应内容权限：请确认当前账号已加入所需充电档位或具备私密内容访问权限。"
    if str(code) in {"-404", "62002"} or "不存在" in text:
        return "目标内容不存在或已删除：请核对链接/ID。"
    return f"B站接口失败：code={code} message={text or '未知错误'}"


def bilibili_json(req: TaskRequest, url: str, referer: str = "https://www.bilibili.com/") -> dict[str, Any]:
    local_env = load_env_file(WORKER_DIR / "env.local")
    raw_cookie = req.cookies.strip() or local_env.get("BILIBILI_COOKIES_FILE", "") or local_env.get("BILI_COOKIE_FILE", "")
    handlers: list[urllib.request.BaseHandler] = []
    if raw_cookie:
        cookie_path = resolve_local_path(raw_cookie)
        if not cookie_path.exists():
            raise FileNotFoundError(f"B站 Cookie 文件不存在: {cookie_path}")
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(str(cookie_path), ignore_discard=True, ignore_expires=True)
        mirror_bilibili_auth_cookies(jar)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
        },
    )
    try:
        with opener.open(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(bilibili_error_category(http_status=exc.code, message=str(exc))) from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"B站接口连接或响应解析失败：{first_line(str(exc))}") from exc
    if payload.get("code") != 0:
        raise RuntimeError(bilibili_error_category(payload.get("code"), str(payload.get("message") or payload.get("msg") or "")))
    return payload


def bilibili_login_data(req: TaskRequest) -> dict[str, Any]:
    payload = bilibili_json(req, "https://api.bilibili.com/x/web-interface/nav")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not data.get("isLogin"):
        raise RuntimeError(bilibili_error_category(-101, "未登录"))
    return data


def validate_bilibili_target_payload(kind: str, payload: dict[str, Any]) -> None:
    """Validate sanitized API payload shapes used by authorized-content regressions."""
    if payload.get("code") != 0:
        raise RuntimeError(bilibili_error_category(payload.get("code"), str(payload.get("message") or "")))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if kind == "video":
        if not data.get("bvid") or not data.get("title"):
            raise RuntimeError("视频元数据抓取失败：接口缺少 bvid 或 title。")
        return
    item = data.get("item") if isinstance(data.get("item"), dict) else {}
    if not item:
        raise RuntimeError("动态正文为空：内容可能已删除、接口风控或账号没有目标权限。")
    modules = item.get("modules") if isinstance(item.get("modules"), dict) else {}
    dynamic = modules.get("module_dynamic") if isinstance(modules.get("module_dynamic"), dict) else {}
    major = dynamic.get("major") if isinstance(dynamic.get("major"), dict) else {}
    if major.get("type") == "MAJOR_TYPE_BLOCKED":
        raise RuntimeError("账号无对应充电权限：接口返回权限占位，请确认当前账号已加入该内容要求的充电档位。")


def list_bilibili_collections(req: TaskRequest) -> str:
    login = bilibili_login_data(req)
    mid = str(login.get("mid") or "")
    favorites_payload = bilibili_json(
        req,
        "https://api.bilibili.com/x/v3/fav/folder/created/list-all?" + urllib.parse.urlencode({"up_mid": mid}),
        f"https://space.bilibili.com/{mid}/favlist",
    )
    fav_data = favorites_payload.get("data") if isinstance(favorites_payload.get("data"), dict) else {}
    favorites = []
    for item in fav_data.get("list") or []:
        if not isinstance(item, dict):
            continue
        favorites.append({
            "type": "favorite",
            "id": str(item.get("id") or item.get("media_id") or ""),
            "mid": mid,
            "title": str(item.get("title") or "未命名收藏夹"),
            "count": parse_int(item.get("media_count"), 0),
        })

    series: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        series_payload = bilibili_json(
            req,
            "https://api.bilibili.com/x/polymer/space/seasons_series_list?" + urllib.parse.urlencode({"mid": mid, "page_num": 1, "page_size": 100}),
            f"https://space.bilibili.com/{mid}/lists",
        )
        data = series_payload.get("data") if isinstance(series_payload.get("data"), dict) else {}
        items_lists = data.get("items_lists") if isinstance(data.get("items_lists"), dict) else data
        for item in items_lists.get("series_list") or []:
            meta = item.get("meta") if isinstance(item, dict) and isinstance(item.get("meta"), dict) else item
            if not isinstance(meta, dict):
                continue
            series.append({
                "type": "series",
                "id": str(meta.get("series_id") or meta.get("id") or ""),
                "mid": str(meta.get("mid") or mid),
                "title": str(meta.get("name") or meta.get("title") or "未命名系列"),
                "count": parse_int(meta.get("total") or meta.get("count"), 0),
            })
    except Exception as exc:
        warnings.append(f"系列列表读取失败（收藏夹仍可使用）：{exc}")

    result = {"mid": mid, "name": str(login.get("uname") or ""), "items": [*favorites, *series], "warnings": warnings}
    return "COLLECTIONS_JSON:" + json.dumps(result, ensure_ascii=False) + "\n"


def check_bilibili_target_access(req: TaskRequest) -> str:
    login = bilibili_login_data(req)
    lines = [f"[OK] 登录态：{login.get('uname') or 'B站用户'} (mid={login.get('mid')})"]
    if req.collection_id:
        if req.collection_type == "series":
            url = "https://api.bilibili.com/x/series/archives?" + urllib.parse.urlencode({
                "mid": req.collection_mid or login.get("mid"), "series_id": req.collection_id, "pn": 1, "ps": 1,
            })
        else:
            url = "https://api.bilibili.com/x/v3/fav/resource/list?" + urllib.parse.urlencode({"media_id": req.collection_id, "pn": 1, "ps": 1})
        bilibili_json(req, url)
        lines.append(f"[OK] 目标权限：可读取{('系列' if req.collection_type == 'series' else '收藏夹')} {req.collection_id}")
    elif req.source:
        opus = re.search(r"/opus/(\d+)", req.source)
        bvid = re.search(r"(BV[0-9A-Za-z]+)", req.source)
        if opus:
            url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?" + urllib.parse.urlencode({"id": opus.group(1), "features": "itemOpusStyle"})
            payload = bilibili_json(req, url, req.source)
            validate_bilibili_target_payload("opus", payload)
        elif bvid:
            view_payload = bilibili_json(req, "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode({"bvid": bvid.group(1)}), req.source)
            validate_bilibili_target_payload("video", view_payload)
            command = tool_cmd(req, "yt-dlp", "--simulate", "--no-warnings")
            cookie_value = req.cookies.strip() or build_env(req).get("BILIBILI_COOKIES_FILE", "")
            if cookie_value:
                command.extend(["--cookies", str(resolve_local_path(cookie_value))])
            command.append(req.source)
            ok, detail = probe(command, build_env(req), timeout=90)
            if not ok:
                lowered = detail.lower()
                if "412" in lowered:
                    raise RuntimeError(bilibili_error_category(http_status=412, message=detail))
                if any(token in detail for token in ("充电", "权限", "会员")) or "login" in lowered:
                    raise RuntimeError("账号无对应视频权限或登录态未被 yt-dlp 接受：" + first_line(detail))
                raise RuntimeError("视频元数据/播放权限抓取失败：" + first_line(detail))
        else:
            raise ValueError("目标权限验证需要 B站视频/动态链接，或已选择收藏夹/系列。")
        lines.append("[OK] 目标权限：目标内容元数据可读取")
    else:
        lines.append("[WARN] 未指定目标，仅验证了登录态。")
    return "\n".join(lines) + "\n"


def python_cmd(req: TaskRequest, script: pathlib.Path) -> list[str]:
    if req.conda_env:
        return ["conda", "run", "--no-capture-output", "-n", req.conda_env, "python3", "-u", str(script)]
    return [req.python_bin or "python3", "-u", str(script)]


def python_eval_cmd(req: TaskRequest, code: str) -> list[str]:
    if req.conda_env:
        return ["conda", "run", "--no-capture-output", "-n", req.conda_env, "python3", "-c", code]
    return [req.python_bin or "python3", "-c", code]


def tool_cmd(req: TaskRequest, tool: str, *args: str) -> list[str]:
    if req.conda_env:
        return ["conda", "run", "--no-capture-output", "-n", req.conda_env, tool, *args]
    return [tool, *args]


def probe(command: list[str], env: dict[str, str], timeout: int = 20) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            cwd=str(WORKER_DIR),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    output = (result.stdout or "").strip()
    return result.returncode == 0, output


def first_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if lines[0].startswith("Traceback") and len(lines) > 1:
        return lines[-1]
    return lines[0]


def status_line(ok: bool, label: str, detail: str = "", hint: str = "", required: bool = True) -> str:
    status = "[OK]" if ok else ("[MISSING]" if required else "[WARN]")
    parts = [f"{status} {label}"]
    if detail:
        parts.append(f"- {detail}")
    if not ok and hint:
        parts.append(f"Hint: {hint}")
    return " ".join(parts)


def info_line(ok: bool, label: str, detail: str = "", hint: str = "") -> str:
    status = "[OK]" if ok else "[INFO]"
    parts = [f"{status} {label}"]
    if detail:
        parts.append(f"- {detail}")
    if not ok and hint:
        parts.append(f"Hint: {hint}")
    return " ".join(parts)


def check_environment(req: TaskRequest, env: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append("Local Note Studio environment check")
    lines.append("")
    lines.append("Selected runtime")
    if req.conda_env:
        lines.append(f"- conda environment: {req.conda_env}")
    else:
        lines.append(f"- Python command: {req.python_bin or 'python3'}")
    lines.append(f"- LLM API base: {req.api_base or env.get('DEFAULT_LLM_API_BASE', '(not set)')}")
    lines.append(f"- model: {req.model or env.get('DEFAULT_LLM_MODEL', '(not set)')}")
    lines.append("")

    required_ok = True
    warning_count = 0

    ok, output = probe(
        python_eval_cmd(
            req,
            "import sys; print(sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 2)",
        ),
        env,
    )
    required_ok = required_ok and ok
    lines.append(
        status_line(
            ok,
            "Python runtime",
            first_line(output),
            "Install Python 3.10/3.11 or choose a conda environment that contains Python.",
        )
    )

    for package, hint in REQUIRED_PYTHON_PACKAGES.items():
        ok, output = probe(python_eval_cmd(req, f"import {package}; print('import ok')"), env)
        required_ok = required_ok and ok
        lines.append(status_line(ok, f"Python package `{package}`", first_line(output), hint))

    for command, hint in REQUIRED_COMMANDS.items():
        version_args = ("-version",) if command in {"ffmpeg", "ffprobe"} else ("--version",)
        ok, output = probe(tool_cmd(req, command, *version_args), env)
        required_ok = required_ok and ok
        lines.append(status_line(ok, f"Command `{command}`", first_line(output), hint))

    for package, hint in OPTIONAL_PYTHON_PACKAGES.items():
        ok, output = probe(
            python_eval_cmd(
                req,
                f"import importlib.util, sys; "
                f"found = importlib.util.find_spec('{package}') is not None; "
                f"print('installed' if found else 'not installed'); "
                f"raise SystemExit(0 if found else 1)",
            ),
            env,
        )
        if not ok:
            warning_count += 1
        lines.append(status_line(ok, f"Optional Python package `{package}`", first_line(output), hint, required=False))

    for command, hint in OPTIONAL_INFO_COMMANDS.items():
        ok, output = probe(tool_cmd(req, command, "--version"), env)
        lines.append(info_line(ok, f"Optional command `{command}`", first_line(output), hint))

    for command, hint in OCR_FALLBACK_COMMANDS.items():
        ok, output = probe(tool_cmd(req, command, "--version"), env)
        lines.append(
            info_line(
                ok,
                f"Optional OCR fallback command `{command}`",
                first_line(output) if ok else first_line(output),
                hint,
            )
        )

    textutil = "/usr/bin/textutil" if pathlib.Path("/usr/bin/textutil").exists() else "textutil"
    ok, output = probe([textutil, "-help"], env)
    if not ok:
        warning_count += 1
    lines.append(
        status_line(
            ok,
            "Optional command `textutil`",
            "available for legacy .doc conversion" if ok else first_line(output),
            "macOS textutil is required when converting legacy .doc files.",
            required=False,
        )
    )

    qlmanage = "/usr/bin/qlmanage" if pathlib.Path("/usr/bin/qlmanage").exists() else "qlmanage"
    ok, output = probe([qlmanage, "-h"], env)
    if not ok:
        warning_count += 1
    lines.append(
        status_line(
            ok,
            "Optional command `qlmanage`",
            "available for macOS previews and OCR page rendering" if ok else first_line(output),
            "qlmanage helps render previews for scanned PDFs and OCR workflows on macOS.",
            required=False,
        )
    )

    ok, output = probe(["xcrun", "swift", "--version"], env)
    if not ok:
        warning_count += 1
    lines.append(
        status_line(
            ok,
            "Optional command `swift`",
            first_line(output) if ok else "",
            "Swift is used for the macOS Vision OCR fallback.",
            required=False,
        )
    )

    lines.append("")
    lines.append("Configuration checks")
    api_base = req.api_base or env.get("DEFAULT_LLM_API_BASE", "")
    api_key = req.api_key or env.get("DEFAULT_LLM_API_KEY", "")
    model = req.model or env.get("DEFAULT_LLM_MODEL", "")
    for label, value, hint in [
        ("LLM API base", api_base, "Set an OpenAI-compatible API base such as http://127.0.0.1:1234/v1."),
        ("LLM API key", api_key, "Set an API key. LM Studio can use a placeholder such as lm-studio."),
        ("LLM model", model, "Set the model name served by your OpenAI-compatible endpoint."),
    ]:
        ok = bool(value)
        required_ok = required_ok and ok
        lines.append(status_line(ok, label, value if ok and label != "LLM API key" else ("set" if ok else ""), hint))

    cookie_value = req.cookies or env.get("BILIBILI_COOKIES_FILE") or env.get("BILI_COOKIE_FILE") or ""
    if cookie_value:
        cookie_path = resolve_local_path(cookie_value)
        ok = cookie_path.exists()
        if not ok:
            warning_count += 1
            detail = str(cookie_path)
        else:
            login_ok, login_detail = bilibili_cookie_login_detail(cookie_path)
            ok = login_ok
            if not ok:
                warning_count += 1
            detail = f"{inspect_cookie_file(cookie_path)}；{login_detail}"
        lines.append(
            status_line(
                ok,
                "Bilibili cookie file",
                detail,
                "Export a fresh Netscape cookies.txt file from the browser account that has access.",
                required=False,
            )
        )
    else:
        warning_count += 1
        lines.append(
            status_line(
                False,
                "Bilibili cookie file",
                "",
                "Optional for public videos, usually required for private favorites or restricted subtitles.",
                required=False,
            )
        )

    if req.output_dir:
        output_path = pathlib.Path(req.output_dir).expanduser()
        exists_or_parent = output_path.exists() or output_path.parent.exists()
        if not exists_or_parent:
            warning_count += 1
        lines.append(
            status_line(
                exists_or_parent,
                "Default output path",
                str(output_path),
                "Choose an existing notes root or a path whose parent directory exists.",
                required=False,
            )
        )

    asr_engine = (env.get("ASR_ENGINE") or "whisper").strip().lower()
    asr_model = (env.get("ASR_LOCAL_MODEL") or "").strip()
    if asr_engine == "whisper":
        if asr_model:
            asr_model_path = pathlib.Path(asr_model).expanduser()
            if not asr_model_path.is_absolute():
                asr_model_path = WORKER_DIR / asr_model_path
            ok = asr_model_path.exists()
            if not ok:
                warning_count += 1
            lines.append(
                status_line(
                    ok,
                    "ASR local model",
                    str(asr_model_path),
                    "Set ASR_LOCAL_MODEL to an existing local Whisper model directory.",
                    required=False,
                )
            )
        else:
            warning_count += 1
            lines.append(status_line(False, "ASR local model", "", ASR_MODEL_HINT, required=False))
    elif asr_engine == "qwen3":
        ok, output = probe(python_eval_cmd(req, "import qwen_asr; print('import ok')"), env)
        if not ok:
            warning_count += 1
        lines.append(status_line(ok, "Qwen3-ASR package", first_line(output), "pip install qwen-asr", required=False))
    else:
        warning_count += 1
        lines.append(status_line(False, "ASR engine", asr_engine, "Use ASR_ENGINE=whisper or ASR_ENGINE=qwen3.", required=False))

    lines.append("")
    if required_ok:
        if warning_count:
            lines.append(f"Result: required dependencies are ready, with {warning_count} warning(s).")
        else:
            lines.append("Result: environment looks ready.")
    else:
        lines.append("Result: required dependencies are missing. Fix the [MISSING] items before running tasks.")
    lines.append("")
    lines.append("Common setup hints")
    if req.conda_env:
        lines.append(f"- conda install -n {req.conda_env} -c conda-forge ffmpeg")
        lines.append(f"- conda install -n {req.conda_env} -c conda-forge pandoc")
        lines.append(f"- conda run -n {req.conda_env} python3 -m pip install pypdf lxml requests yt-dlp mlx-whisper")
    else:
        python = req.python_bin or "python3"
        lines.append(f"- {python} -m pip install pypdf lxml requests yt-dlp mlx-whisper")
        lines.append("- brew install ffmpeg")
        lines.append("- brew install pandoc")
    return "\n".join(lines) + "\n"


def command_for(req: TaskRequest) -> list[str]:
    if not req.task:
        raise ValueError("task is required")
    if req.task == "env-check":
        raise ValueError("env-check is handled internally")
    if req.task == "refresh-bilibili-cookies":
        if not req.browser_profile.strip():
            raise ValueError("请先配置 Chrome 个人资料路径")
        cookie_path = resolve_local_path(req.cookies or "bili_cookies.txt")
        return [
            *python_cmd(req, SCRIPTS_DIR / "export_bilibili_cookies.py"),
            "--browser",
            "chrome",
            "--profile",
            req.browser_profile,
            "--output",
            str(cookie_path),
        ]
    if not req.source and req.task not in {"bilibili-favorite"}:
        raise ValueError("source is required")
    if not req.output_dir:
        raise ValueError("output_dir is required")

    if req.task == "bilibili-url":
        command = [
            *python_cmd(req, SCRIPTS_DIR / "run_bilibili_transcript.py"),
            "--url",
            req.source,
        ]
        if req.overwrite_outputs:
            command.append("--overwrite")
        if req.output_filename:
            command.extend(["--output-filename", req.output_filename])
        return command
    if req.task == "bilibili-favorite":
        command = [
            *python_cmd(req, SCRIPTS_DIR / "run_bilibili_transcript.py"),
            "--favorite",
            "--collection-type",
            req.collection_type,
            "--collection-id",
            req.collection_id,
        ]
        command.extend(["--limit", str(max(0, req.favorite_limit))])
        if req.collection_mid:
            command.extend(["--collection-mid", req.collection_mid])
        if req.retry_failed:
            command.append("--retry-failed")
        if req.overwrite_outputs:
            command.append("--overwrite")
        return command
    if req.task == "local-video":
        source_path = pathlib.Path(req.source)
        args = ["--local-dir" if source_path.is_dir() else "--local-file", req.source]
        if source_path.is_dir() and req.output_filename:
            raise ValueError("output_filename can only be used with a single local video file")
        if source_path.is_dir() and req.recursive_search:
            args.append("--recursive")
        if req.overwrite_outputs:
            args.append("--overwrite")
        if req.output_filename and not source_path.is_dir():
            args.extend(["--output-filename", req.output_filename])
        return [*python_cmd(req, SCRIPTS_DIR / "run_bilibili_transcript.py"), *args]
    if req.task in {"web-url", "bilibili-opus", "bilibili-up-opus"}:
        command = [
            *python_cmd(req, SCRIPTS_DIR / "convert_sources_to_md.py"),
            "--bilibili-up-opus" if req.task == "bilibili-up-opus" else "--url",
            req.source,
            "--output-dir",
            req.output_dir,
        ]
        if req.task == "bilibili-up-opus" and req.favorite_limit > 0:
            command.extend(["--limit", str(req.favorite_limit)])
        if req.overwrite_outputs:
            command.append("--overwrite")
        if req.output_filename and req.task != "bilibili-up-opus":
            command.extend(["--output-filename", req.output_filename])
        return command
    if req.task in {"source-file", "ai-chat"}:
        command = [
            *python_cmd(req, SCRIPTS_DIR / "convert_sources_to_md.py"),
            "--source",
            req.source,
            "--output-dir",
            req.output_dir,
        ]
        if req.overwrite_outputs:
            command.append("--overwrite")
        if req.output_filename:
            command.extend(["--output-filename", req.output_filename])
        return command
    if req.task == "paper-quickread":
        command = [
            *python_cmd(req, SCRIPTS_DIR / "quick_read_pdf.py"),
            "--source",
            req.source,
            "--output-dir",
            req.output_dir,
        ]
        if req.overwrite_outputs:
            command.append("--overwrite")
        if req.output_filename:
            command.extend(["--output-filename", req.output_filename])
        return command
    if req.task == "epub-export":
        command = [
            *python_cmd(req, SCRIPTS_DIR / "export_epub.py"),
            "--source-dir",
            req.source,
            "--output-dir",
            req.output_dir,
        ]
        if req.overwrite_outputs:
            command.append("--overwrite")
        if req.output_filename:
            command.extend(["--output-filename", req.output_filename])
        return command
    raise ValueError(f"unsupported task: {req.task}")


def run_command(command: list[str], env: dict[str, str], dry_run: bool) -> str:
    if dry_run:
        return render_command(command) + "\n"
    run_process(command, env)
    return ""


def run_convert_and_organize_task(req: TaskRequest, env: dict[str, str]) -> str:
    if req.dry_run:
        staged_req = replace(req, output_dir="<temporary-staging-dir>")
        convert_command = command_for(staged_req)
        organize_preview = [
            *python_cmd(req, SCRIPTS_DIR / "qwen_organize_notes.py"),
            "--source",
            "<converted-markdown-path>",
            "--output-dir",
            req.output_dir,
            "--omit-draft-path",
        ]
        if req.overwrite_outputs:
            organize_preview.append("--overwrite")
        if req.output_filename and req.task != "bilibili-up-opus":
            organize_preview.extend(["--output-filename", req.output_filename])
        return "\n".join(
            [
                render_command(convert_command),
                "",
                "then organize staged Markdown with Qwen into the final output directory:",
                render_command(organize_preview),
            ]
        ) + "\n"

    if req.task in {"bilibili-opus", "bilibili-up-opus"}:
        backfill_legacy_bilibili_originals(pathlib.Path(req.output_dir))
        archive_legacy_bilibili_drafts(pathlib.Path(req.output_dir))

    with tempfile.TemporaryDirectory(prefix="local-note-studio-drafts-") as staging_dir:
        staged_req = replace(req, output_dir=staging_dir)
        convert_command = command_for(staged_req)
        print("已创建临时草稿区；整理完成后会自动清理。")
        output = run_process(convert_command, env)
        if req.task == "bilibili-up-opus":
            converted_paths = [
                str(path)
                for path in sorted(
                    pathlib.Path(staging_dir).glob("*.md"),
                    key=lambda item: item.stat().st_mtime_ns,
                )
            ]
        else:
            converted_paths = extract_converted_paths(output)
        if not converted_paths:
            print("未从转换输出中识别到 Markdown 路径，跳过 Qwen 整理。", file=sys.stderr)
            return ""

        organize_command = [*python_cmd(req, SCRIPTS_DIR / "qwen_organize_notes.py")]
        for converted_path in converted_paths:
            organize_command.extend(["--source", converted_path])
        organize_command.extend(["--output-dir", req.output_dir, "--omit-draft-path"])
        if req.overwrite_outputs:
            organize_command.append("--overwrite")
        if req.output_filename and req.task != "bilibili-up-opus":
            organize_command.extend(["--output-filename", req.output_filename])
        print("")
        print(f"开始 Qwen 整理：共 {len(converted_paths)} 篇，正式输出到 {req.output_dir}")
        run_process(organize_command, env)
        promote_staged_assets(pathlib.Path(staging_dir), pathlib.Path(req.output_dir))

    if req.task in {"bilibili-opus", "bilibili-up-opus"}:
        archive_legacy_bilibili_drafts(pathlib.Path(req.output_dir))
    return ""


def promote_staged_assets(staging_dir: pathlib.Path, output_dir: pathlib.Path) -> None:
    source = staging_dir / "assets"
    if not source.exists():
        return
    target = output_dir / "assets"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"图片资产已写入: {target}")


def markdown_frontmatter_value(markdown: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.*?)\s*$", markdown)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def extracted_original_section(markdown: str) -> str:
    match = re.search(r"(?ms)^##\s+原文抽取\s*$\n(.+)\Z", markdown)
    if not match:
        return ""
    original = match.group(1).strip()
    if not original:
        return ""
    return "\n\n".join(
        [
            "## 原文抽取",
            "> 以下为转换脚本抽取的完整原文，Qwen 整理内容插入在上方，便于回看与校对。",
            original,
        ]
    )


def backfill_legacy_bilibili_originals(output_dir: pathlib.Path) -> None:
    if not output_dir.exists():
        return
    drafts: dict[str, str] = {}
    organized: list[tuple[pathlib.Path, str, str]] = []
    for path in output_dir.glob("*.md"):
        try:
            markdown = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if markdown_frontmatter_value(markdown, "source_type") != "bilibili-opus":
            continue
        source_url = markdown_frontmatter_value(markdown, "source_url")
        status = markdown_frontmatter_value(markdown, "status")
        if status == "draft":
            original = extracted_original_section(markdown)
            if source_url and original:
                drafts[source_url] = original
        elif status == "organized":
            organized.append((path, source_url, markdown))

    for path, source_url, markdown in organized:
        migrated = re.sub(r"(?m)^draft_path:\s*.*\n", "", markdown, count=1)
        original = drafts.get(source_url, "")
        if not re.search(r"(?m)^##\s+原文抽取\s*$", migrated) and original:
            migrated = f"{migrated.rstrip()}\n\n{original}\n"
            print(f"已为历史正式笔记补全原文: {path}")
        if migrated != markdown:
            path.write_text(migrated, encoding="utf-8")


def archive_legacy_bilibili_drafts(output_dir: pathlib.Path) -> None:
    if not output_dir.exists():
        return
    organized_urls: set[str] = set()
    drafts: list[tuple[pathlib.Path, str]] = []
    for path in output_dir.glob("*.md"):
        try:
            markdown = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if markdown_frontmatter_value(markdown, "source_type") != "bilibili-opus":
            continue
        source_url = markdown_frontmatter_value(markdown, "source_url")
        status = markdown_frontmatter_value(markdown, "status")
        if status == "organized" and re.search(r"(?m)^##\s+原文抽取\s*$", markdown):
            organized_urls.add(source_url)
        elif status == "draft":
            drafts.append((path, source_url))

    archive_dir = output_dir / ".local-note-studio-legacy-drafts"
    for path, source_url in drafts:
        if not source_url or source_url not in organized_urls:
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / path.name
        if target.exists():
            target = archive_dir / f"{path.stem}-{int(path.stat().st_mtime)}{path.suffix}"
        shutil.move(str(path), str(target))
        print(f"旧草稿已移入隐藏备份目录: {target}")


def output_snapshot(output_dir: str) -> dict[pathlib.Path, tuple[int, int]]:
    root = pathlib.Path(output_dir).expanduser()
    if not root.exists():
        return {}
    snapshot: dict[pathlib.Path, tuple[int, int]] = {}
    for pattern in ("*.md", "*.epub"):
        for path in root.rglob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[path.resolve()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def changed_outputs(output_dir: str, before: dict[pathlib.Path, tuple[int, int]]) -> list[pathlib.Path]:
    after = output_snapshot(output_dir)
    return sorted(path for path, signature in after.items() if before.get(path) != signature)


def markdown_image_targets(markdown: str) -> list[str]:
    targets = [match.group(1).strip() for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", markdown)]
    targets.extend(match.group(1).strip() for match in re.finditer(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", markdown, re.I))
    return targets


def validate_markdown_output(path: pathlib.Path, req: TaskRequest) -> list[str]:
    markdown = path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    if re.search(r"(?m)^draft_path:\s*", markdown) or "local-note-studio-drafts-" in markdown:
        errors.append("包含系统临时草稿路径或 draft_path")
    if not re.search(r"(?m)^(source_path|source_url):\s*\S", markdown) and "## 来源追溯" not in markdown:
        errors.append("缺少来源追溯信息")
    for target in markdown_image_targets(markdown):
        clean = target.split(maxsplit=1)[0].strip("<>").split("#", 1)[0].split("?", 1)[0]
        if not clean or re.match(r"^(?:https?:|data:|//)", clean, re.I):
            continue
        decoded = urllib.parse.unquote(clean)
        resolved = pathlib.Path(decoded) if pathlib.Path(decoded).is_absolute() else path.parent / decoded
        if not resolved.exists():
            errors.append(f"图片相对路径无法解析: {target}")

    raw_subtitle = re.search(r"(?m)^(?:##\s+原始字幕|<summary>📄\s*原始字幕</summary>)", markdown) is not None
    if req.task in {"bilibili-url", "bilibili-favorite", "local-video"}:
        if req.keep_original_subtitles and not raw_subtitle:
            errors.append("界面要求保留原始字幕，但输出中缺少原始字幕")
        if not req.keep_original_subtitles and raw_subtitle:
            errors.append("界面要求移除原始字幕，但输出中仍含原始字幕")

    if req.task in {"web-url", "source-file", "ai-chat", "bilibili-opus", "bilibili-up-opus"}:
        if not re.search(r"(?m)^status:\s*organized\s*$", markdown):
            errors.append("缺少 Qwen 整理完成标记 status: organized")
        original = re.search(r"(?ms)^##\s+原文抽取\s*$\n(.+)", markdown)
        if not original or not original.group(1).strip():
            errors.append("缺少完整原文（## 原文抽取）")
    if req.task == "paper-quickread":
        translated = re.search(r"(?ms)^##\s+全文翻译\s*$\n(.+)", markdown)
        if not translated or not translated.group(1).strip():
            errors.append("缺少全文翻译（## 全文翻译）")
    return errors


def validate_task_outputs(req: TaskRequest, before: dict[pathlib.Path, tuple[int, int]]) -> None:
    if req.dry_run or not req.output_dir:
        return
    outputs = changed_outputs(req.output_dir, before)
    if not outputs:
        print("[完整性 WARN] 本次没有新增或更新输出（可能全部命中跳过策略）。")
        return
    failures: list[str] = []
    for path in outputs:
        if path.suffix.lower() == ".epub":
            if path.stat().st_size == 0:
                failures.append(f"{path.name}: EPUB 文件为空")
            continue
        for error in validate_markdown_output(path, req):
            failures.append(f"{path.name}: {error}")
    if failures:
        detail = "\n".join(f"- {item}" for item in failures)
        raise RuntimeError(f"输出完整性检查失败：\n{detail}")
    print(f"[完整性 OK] 已检查 {len(outputs)} 个本次输出：来源、正文/翻译、字幕选项和图片路径均符合要求。")


def render_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_process(command: list[str], env: dict[str, str]) -> str:
    process = subprocess.Popen(
        command,
        cwd=str(WORKER_DIR),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    lines: list[str] = []
    if process.stdout:
        for line in process.stdout:
            lines.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
    returncode = process.wait()
    output = "".join(lines)
    if returncode != 0:
        raise RuntimeError(f"command failed ({returncode}):\n{render_command(command)}\n\n{output}")
    return output


def extract_converted_paths(output: str) -> list[str]:
    paths: list[str] = []
    seen = set()
    for line in output.splitlines():
        match = re.search(r"\bconverted\s+.+?\s+->\s+(.+\.md)$", line.strip())
        if not match:
            continue
        path = match.group(1).strip()
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", help="Task request JSON from the desktop app.")
    parser.add_argument("--task", help="Task type.")
    parser.add_argument("--source", default="", help="URL or file path.")
    parser.add_argument("--output-dir", default="", help="Markdown output directory.")
    parser.add_argument("--output-filename", default="", help="Custom Markdown/EPUB file name for single-output tasks.")
    parser.add_argument("--conda-env", default="", help="Existing conda environment to use.")
    parser.add_argument("--python-bin", default="python3", help="Python command when conda is not used.")
    parser.add_argument("--api-base", default="", help="OpenAI-compatible API base.")
    parser.add_argument("--api-key", default="", help="OpenAI-compatible API key.")
    parser.add_argument("--model", default="", help="LLM model name.")
    parser.add_argument("--cookies", default="", help="Bilibili Netscape cookies.txt path.")
    parser.add_argument("--browser-profile", default="", help="Chrome profile directory name or absolute path.")
    parser.add_argument(
        "--subtitle-strategy",
        default="yt-dlp",
        choices=["yt-dlp", "web", "asr"],
        help="Preferred Bilibili transcript source.",
    )
    parser.add_argument("--favorite-limit", type=int, default=1, help="Maximum videos to process in favorite mode. Use 0 for full run.")
    parser.add_argument("--collection-type", default="favorite", choices=["favorite", "series"], help="Selected Bilibili collection type.")
    parser.add_argument("--collection-id", default="", help="Selected Bilibili favorite/series ID.")
    parser.add_argument("--collection-mid", default="", help="Owner mid for a selected Bilibili series.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry the failed entries saved by the previous batch.")
    parser.add_argument("--extract-keyframes", action="store_true", help="Extract key frames for Bilibili or local video notes.")
    parser.add_argument("--dialogue-detection", action="store_true", help="Detect dialogue and label speakers in video transcripts.")
    parser.add_argument("--no-keep-original-subtitles", action="store_true", help="Do not keep the raw subtitle section in video notes.")
    parser.add_argument("--recursive-search", action="store_true", help="Recursively scan local video directories.")
    parser.add_argument("--overwrite-outputs", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--stock-terms", action="store_true", help="Enable A-share stock terminology validation.")
    parser.add_argument("--enable-ocr", action="store_true", help="Enable OCR for images and scanned PDFs in source conversion.")
    parser.add_argument("--dry-run", action="store_true", help="Print command without running.")
    return parser.parse_args(argv)


def request_from_args(args: argparse.Namespace) -> TaskRequest:
    if args.request_json:
        return TaskRequest.from_mapping(json.loads(args.request_json))
    return TaskRequest(
        task=args.task or "",
        source=args.source,
        output_dir=args.output_dir,
        output_filename=args.output_filename,
        conda_env=args.conda_env,
        python_bin=args.python_bin,
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        cookies=args.cookies,
        browser_profile=args.browser_profile,
        subtitle_strategy=args.subtitle_strategy,
        favorite_limit=args.favorite_limit,
        collection_type=args.collection_type,
        collection_id=args.collection_id,
        collection_mid=args.collection_mid,
        retry_failed=args.retry_failed,
        extract_keyframes=args.extract_keyframes,
        dialogue_detection=args.dialogue_detection,
        keep_original_subtitles=not args.no_keep_original_subtitles,
        recursive_search=args.recursive_search,
        overwrite_outputs=args.overwrite_outputs,
        stock_terms=args.stock_terms,
        enable_ocr=args.enable_ocr,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    req = request_from_args(args)
    env = build_env(req)
    if req.task == "env-check":
        sys.stdout.write(check_environment(req, env))
        return 0
    if req.task == "bilibili-cookie-status":
        sys.stdout.write(check_bilibili_cookie(req))
        return 0
    if req.task == "bilibili-collections":
        sys.stdout.write(list_bilibili_collections(req))
        return 0
    if req.task == "bilibili-access-check":
        sys.stdout.write(check_bilibili_target_access(req))
        return 0
    before = output_snapshot(req.output_dir)
    if req.task in {"web-url", "bilibili-opus", "bilibili-up-opus", "source-file", "ai-chat"}:
        sys.stdout.write(run_convert_and_organize_task(req, env))
        validate_task_outputs(req, before)
        return 0
    command = command_for(req)
    sys.stdout.write(run_command(command, env, req.dry_run))
    validate_task_outputs(req, before)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
