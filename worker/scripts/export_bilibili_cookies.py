#!/usr/bin/env python3
"""Export only Bilibili cookies from a local browser profile."""

from __future__ import annotations

import argparse
import http.cookiejar
import os
import pathlib
import sys
import tempfile

from yt_dlp.cookies import extract_cookies_from_browser


WORKER_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from local_note_studio_worker import bilibili_cookie_login_detail, validate_chromium_profile_path  # noqa: E402


BILIBILI_DOMAINS = ("bilibili.com", "bilibili.cn")


class ExportLogger:
    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        print(message)

    def warning(self, message: str, only_once: bool = False) -> None:
        print(f"警告: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        print(f"错误: {message}", file=sys.stderr)


def is_bilibili_cookie(cookie: http.cookiejar.Cookie) -> bool:
    domain = cookie.domain.lstrip(".").lower()
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in BILIBILI_DOMAINS)


def filtered_jar(source: http.cookiejar.CookieJar) -> http.cookiejar.MozillaCookieJar:
    result = http.cookiejar.MozillaCookieJar()
    for cookie in source:
        if is_bilibili_cookie(cookie):
            result.set_cookie(cookie)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从浏览器个人资料中只导出 B站 Cookie，并校验登录态。"
    )
    parser.add_argument(
        "--browser",
        default="chrome",
        choices=["chrome", "chromium", "edge", "brave", "vivaldi"],
        help="浏览器类型，默认 chrome。",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="个人资料目录名（如 'Profile 1'）或绝对路径；不填时由 yt-dlp 自动选择。",
    )
    parser.add_argument(
        "--output",
        default="./bili_cookies.txt",
        help="输出的 Netscape cookies.txt 路径。",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="跳过 B站登录态校验；仅在网络暂时不可用时使用。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.profile:
        print("错误: 必须指定具体的 Chrome Profile 目录。", file=sys.stderr)
        return 1
    try:
        profile = validate_chromium_profile_path(args.profile)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    output = pathlib.Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    profile_label = profile.name
    print(f"正在从 {args.browser} 导出 Cookie（Profile: {profile_label}）...")
    source = extract_cookies_from_browser(
        args.browser,
        profile=str(profile),
        logger=ExportLogger(),
    )
    jar = filtered_jar(source)
    if not jar:
        print("错误: 所选浏览器个人资料中没有找到 B站 Cookie。", file=sys.stderr)
        return 1

    fd, temp_name = tempfile.mkstemp(prefix=".bili_cookies_", suffix=".txt", dir=output.parent)
    os.close(fd)
    temp_path = pathlib.Path(temp_name)
    try:
        jar.filename = str(temp_path)
        jar.save(ignore_discard=True, ignore_expires=True)
        print(f"已筛选 {len(jar)} 条 B站 Cookie（不会写入其他站点 Cookie）。")

        if not args.no_validate:
            login_ok, detail = bilibili_cookie_login_detail(temp_path)
            if not login_ok:
                print(f"登录态校验失败: {detail}", file=sys.stderr)
                print(
                    "原 Cookie 文件未被覆盖。请在 chrome://version 查看当前有权限窗口的“个人资料路径”，"
                    "然后用 --profile 指定其末级目录名或完整路径。",
                    file=sys.stderr,
                )
                return 2
            print(f"登录态校验通过: {detail}")

        os.replace(temp_path, output)
        print(f"Cookie 已保存: {output}")
        return 0
    finally:
        temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
