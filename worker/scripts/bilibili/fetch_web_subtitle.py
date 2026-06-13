#!/usr/bin/env python3
"""Fetch subtitles exposed by the Bilibili web player."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def bvid_from_url(url: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]+)", url)
    if not match:
        raise ValueError(f"cannot find BVID in URL: {url}")
    return match.group(1)


def page_from_url(url: str) -> int:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return max(1, int(query.get("p", ["1"])[0]))
    except ValueError:
        return 1


def build_opener(cookie_file: str = "") -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    if cookie_file:
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


def get_json(opener: urllib.request.OpenerDirector, url: str, referer: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
        },
        method="GET",
    )
    with opener.open(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def get_wbi_key(opener: urllib.request.OpenerDirector, referer: str) -> str:
    nav_data = get_json(opener, "https://api.bilibili.com/x/web-interface/nav", referer)
    wbi_img = ((nav_data.get("data") or {}).get("wbi_img") or {})
    lookup = ""
    for key in ("img_url", "sub_url"):
        raw_url = str(wbi_img.get(key) or "")
        if raw_url:
            lookup += raw_url.rsplit("/", 1)[-1].split(".", 1)[0]
    if not lookup:
        raise RuntimeError(f"cannot get WBI key: {nav_data}")
    return "".join(lookup[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def sign_wbi(params: dict[str, Any], mixin_key: str) -> dict[str, str]:
    signed = {**params, "wts": round(time.time())}
    cleaned = {
        key: "".join(ch for ch in str(value) if ch not in "!'()*")
        for key, value in sorted(signed.items())
    }
    query = urllib.parse.urlencode(cleaned)
    cleaned["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
    return cleaned


def select_page(view_data: dict[str, Any], page: int) -> tuple[int, int]:
    data = view_data.get("data") or {}
    aid = int(data.get("aid") or 0)
    pages = data.get("pages") or []
    if not aid or not pages:
        raise RuntimeError(f"unexpected view response: {view_data}")
    index = min(max(page, 1), len(pages)) - 1
    cid = int(pages[index].get("cid") or 0)
    if not cid:
        raise RuntimeError(f"cannot find cid in view response: {view_data}")
    return aid, cid


def subtitle_sort_key(item: dict[str, Any], preferred: list[str]) -> tuple[int, int, int]:
    lan = str(item.get("lan") or "")
    try:
        lang_rank = preferred.index(lan)
    except ValueError:
        lang_rank = len(preferred)
    is_ai = 1 if lan.startswith("ai-") or item.get("type") == 1 else 0
    return lang_rank, is_ai, int(item.get("id") or 0)


def pick_subtitle(subtitles: list[dict[str, Any]], preferred: list[str]) -> dict[str, Any]:
    candidates = [item for item in subtitles if item.get("subtitle_url")]
    if not candidates:
        raise RuntimeError("web player subtitle list is empty")
    return sorted(candidates, key=lambda item: subtitle_sort_key(item, preferred))[0]


def player_subtitles(player_data: dict[str, Any]) -> list[dict[str, Any]]:
    return (((player_data.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])


def fetch_player_data(
    opener: urllib.request.OpenerDirector,
    *,
    bvid: str,
    aid: int,
    cid: int,
    referer: str,
) -> tuple[dict[str, Any], str]:
    mixin_key = get_wbi_key(opener, referer)
    attempts = [
        (
            "x/player/wbi/v2:bvid",
            "https://api.bilibili.com/x/player/wbi/v2?"
            + urllib.parse.urlencode(sign_wbi({"bvid": bvid, "cid": cid}, mixin_key)),
        ),
        (
            "x/player/wbi/v2:aid",
            "https://api.bilibili.com/x/player/wbi/v2?"
            + urllib.parse.urlencode(sign_wbi({"aid": aid, "cid": cid}, mixin_key)),
        ),
        (
            "x/player/v2:bvid",
            "https://api.bilibili.com/x/player/v2?"
            + urllib.parse.urlencode({"bvid": bvid, "cid": cid}),
        ),
        (
            "x/player/v2:aid",
            "https://api.bilibili.com/x/player/v2?"
            + urllib.parse.urlencode({"aid": aid, "cid": cid}),
        ),
    ]
    errors: list[str] = []
    for endpoint, url in attempts:
        try:
            data = get_json(opener, url, referer)
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
            continue
        if int(data.get("code", -1)) == 0 and player_subtitles(data):
            return data, endpoint
        errors.append(f"{endpoint}: empty subtitles or code={data.get('code')}")
    raise RuntimeError("web player subtitle list is empty; tried " + "; ".join(errors))


def subtitle_url(raw_url: str) -> str:
    if raw_url.startswith("//"):
        return "https:" + raw_url
    if raw_url.startswith("http://"):
        return "https://" + raw_url[len("http://") :]
    return raw_url


def subtitle_text(subtitle_data: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in subtitle_data.get("body") or []:
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(content)
    return "\n".join(lines).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Bilibili video URL")
    parser.add_argument("--cookies", default="", help="Netscape cookies.txt file")
    parser.add_argument("--output", required=True, help="plain-text subtitle output path")
    parser.add_argument("--meta", required=True, help="subtitle metadata JSON output path")
    parser.add_argument(
        "--preferred-langs",
        default="zh-CN,zh-Hans,zh-Hant,zh-TW,ai-zh,en,ai-en,ja,ai-ja,ko,ai-kr",
        help="comma-separated language priority",
    )
    args = parser.parse_args()

    bvid = bvid_from_url(args.url)
    page = page_from_url(args.url)
    referer = f"https://www.bilibili.com/video/{bvid}/"
    opener = build_opener(args.cookies)
    preferred = [item.strip() for item in args.preferred_langs.split(",") if item.strip()]

    view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={urllib.parse.quote(bvid)}"
    view_data = get_json(opener, view_url, referer)
    if int(view_data.get("code", -1)) != 0:
        raise RuntimeError(f"view API failed: {view_data}")
    aid, cid = select_page(view_data, page)

    player_data, endpoint = fetch_player_data(opener, bvid=bvid, aid=aid, cid=cid, referer=referer)
    subtitles = player_subtitles(player_data)
    selected = pick_subtitle(subtitles, preferred)
    raw_subtitle = get_json(opener, subtitle_url(str(selected["subtitle_url"])), referer)
    text = subtitle_text(raw_subtitle)
    if not text:
        raise RuntimeError("selected web subtitle has no text body")

    output_path = Path(args.output)
    meta_path = Path(args.meta)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "bvid": bvid,
                "aid": aid,
                "cid": cid,
                "page": page,
                "lan": selected.get("lan", ""),
                "lan_doc": selected.get("lan_doc", ""),
                "id": selected.get("id", ""),
                "id_str": selected.get("id_str", ""),
                "type": selected.get("type", ""),
                "endpoint": endpoint,
                "subtitle_url": selected.get("subtitle_url", ""),
                "line_count": len(text.splitlines()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    label = selected.get("lan_doc") or selected.get("lan") or "unknown"
    print(f"web subtitle: {label}, {len(text.splitlines())} lines")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError) as exc:
        print(f"fetch web subtitle failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
