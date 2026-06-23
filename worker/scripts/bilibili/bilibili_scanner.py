#!/usr/bin/env python3
"""
B站收藏夹快速扫描脚本 v1.2 - 只扫描，不转录
输出新视频列表供 AI Agent 处理（生成摘要、通知等）
自动分页，确保收藏夹中所有视频都被扫描。

配置：编辑项目根目录的 env.local 文件

支持公开和私有收藏夹：
  - 公开收藏夹：无需额外配置，FAV_MEDIA_ID 即可
  - 私有收藏夹：在 env.local 中设置 BILI_COOKIE_FILE 指向 Netscape 格式 Cookie 文件
    推荐使用项目专用脚本从指定浏览器 Profile 导出并校验：
      python3 worker/scripts/export_bilibili_cookies.py --browser chrome --profile "Profile 1"
"""

import argparse
import os
import re
import sys

import requests

# ===== 加载 env.local 配置 =====
def _load_env_local():
    """从项目根目录的 env.local 加载配置"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(os.path.dirname(script_dir))
    env_file = os.path.join(project_dir, "env.local")

    config = {}
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    config[key] = value
    return config

_env = _load_env_local()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))

FAV_MEDIA_ID = os.environ.get("BILIBILI_FAV_MEDIA_ID") or os.environ.get("FAV_MEDIA_ID") or _env.get("FAV_MEDIA_ID", _env.get("BILIBILI_FAV_MEDIA_ID", ""))

def _expand_path(raw):
    """展开路径中的 $HOME / $VAR 和 ~"""
    return os.path.expanduser(os.path.expandvars(raw))

STATE_DIR = _expand_path(
    os.environ.get("BILIBILI_STATE_DIR") or os.environ.get("STATE_DIR") or _env.get("STATE_DIR", _env.get("BILIBILI_STATE_DIR", os.path.join(PROJECT_DIR, "indexes", "bilibili-state")))
)
OUTPUT_DIR = _expand_path(
    os.environ.get("BILIBILI_OUTPUT_DIR") or os.environ.get("OUTPUT_DIR") or _env.get("OUTPUT_DIR", _env.get("BILIBILI_OUTPUT_DIR", os.path.join(PROJECT_DIR, "notes", "_inbox", "bilibili")))
)
NOTES_DIR = _expand_path(os.environ.get("NOTES_DIR") or _env.get("NOTES_DIR", os.path.join(PROJECT_DIR, "notes")))
BILIBILI_DEDUPE_DIRS = os.environ.get("BILIBILI_DEDUPE_DIRS") or _env.get("BILIBILI_DEDUPE_DIRS", NOTES_DIR)
COOKIE_FILE = _expand_path(
    os.environ.get("BILIBILI_COOKIES_FILE") or os.environ.get("BILI_COOKIE_FILE") or _env.get("BILI_COOKIE_FILE", _env.get("BILIBILI_COOKIES_FILE", ""))
)
PROCESSED_FILE = os.path.join(STATE_DIR, "processed_videos.txt")
FAVORITE_API = "https://api.bilibili.com/x/v3/fav/resource/list"
SERIES_API = "https://api.bilibili.com/x/series/archives"


def _load_cookies():
    """从 Netscape 格式 Cookie 文件加载 Cookie，转为 requests 可用的 dict"""
    cookies = {}
    if not COOKIE_FILE or not os.path.exists(COOKIE_FILE):
        return cookies
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain, _, path, secure, expires, name, value = parts[:7]
                    cookies[name] = value
    except Exception:
        pass
    return cookies


def _api_error(code, message, status=None):
    text = str(message or "")
    if status == 412 or str(code) == "-412" or "412" in text:
        return "接口风控/HTTP 412：请稍后再试；若持续出现，请从已登录 Chrome Profile 刷新 Cookie。"
    if str(code) in {"-101", "-111"} or "未登录" in text:
        return "未登录：Cookie 已失效，请刷新 Cookie 后重新验证登录态。"
    if str(code) in {"-403", "11010", "11011"} or "权限" in text:
        return "账号无对应内容权限：请确认收藏夹可见或账号具备目标内容权限。"
    return f"B站API返回错误 (code={code}) - {text or '未知错误'}"


def fetch_all_medias(collection_type="favorite", collection_id="", collection_mid=""):
    """分页获取收藏夹或 UP 主系列中的所有视频。"""
    all_medias = []
    pn = 1
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }
    cookies = _load_cookies()

    if cookies:
        print(f"STATUS:COOKIE_FILE_LOADED:{len(cookies)}cookies", file=sys.stderr)
    else:
        print("STATUS:NO_COOKIE", file=sys.stderr)

    while True:
        if collection_type == "series":
            url = f"{SERIES_API}?mid={collection_mid}&series_id={collection_id}&ps=30&pn={pn}"
        else:
            url = f"{FAVORITE_API}?media_id={collection_id}&ps=20&pn={pn}"
        try:
            resp = requests.get(
                url, headers=headers, cookies=cookies if cookies else None, timeout=30
            )
            if resp.status_code >= 400:
                print(f"ERROR: {_api_error(None, resp.reason, resp.status_code)}")
                sys.exit(1)
            data = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"ERROR: 网络请求失败 - {e}")
            sys.exit(1)
        except ValueError as e:
            print(f"ERROR: API响应解析失败 - {e}")
            sys.exit(1)

        if data.get("code") != 0:
            msg = data.get("message", "未知错误")
            print(f"ERROR: {_api_error(data.get('code'), msg)}")
            sys.exit(1)

        payload_data = data.get("data") or {}
        medias = payload_data.get("archives", []) if collection_type == "series" else payload_data.get("medias", [])
        all_medias.extend(medias)

        if collection_type == "series":
            page = payload_data.get("page") or {}
            if pn * int(page.get("size") or 30) >= int(page.get("total") or len(all_medias)):
                break
        elif not payload_data.get("has_more"):
            break
        pn += 1

    return all_medias


def _dedupe_roots():
    roots = []
    for raw in BILIBILI_DEDUPE_DIRS.split(os.pathsep):
        raw = raw.strip()
        if not raw:
            continue
        path = _expand_path(raw)
        if not os.path.isabs(path):
            path = os.path.join(PROJECT_DIR, path)
        roots.append(path)

    output_dir = OUTPUT_DIR if os.path.isabs(OUTPUT_DIR) else os.path.join(PROJECT_DIR, OUTPUT_DIR)
    roots.append(output_dir)

    seen = set()
    unique = []
    for path in roots:
        norm = os.path.abspath(path)
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(norm)
    return unique


def _find_existing_ids():
    """扫描去重目录，找出已有 .md 文件对应的视频 ID。

    输出文件名格式: {title}_{author}_{date}_{video_id}.md
    video_id 可能是 avid（纯数字）或 bvid（BV 开头），双向收集。
    也会扫描正文里的 source_url/frontmatter 链接，兼容已整理笔记。
    返回 (avid_set, bvid_set) 两集合，调用方取并集匹配。
    """
    avids = set()
    bvids = set()
    for scan_root in _dedupe_roots():
        if not os.path.isdir(scan_root):
            continue
        for root, _dirs, files in os.walk(scan_root):
            for f in files:
                if not f.endswith(".md"):
                    continue
                base = f[:-3]  # 去掉 .md
                last_seg = base.rsplit("_", 1)[-1]
                if last_seg.isdigit():
                    avids.add(last_seg)
                elif last_seg.upper().startswith("BV"):
                    bvids.add(last_seg)

                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        head = handle.read(4096)
                except UnicodeDecodeError:
                    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                        head = handle.read(4096)
                except OSError:
                    continue
                for bvid in re.findall(r"\b(BV[0-9A-Za-z]{8,})\b", head):
                    bvids.add(bvid)
    return avids, bvids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-type", choices=["favorite", "series"], default="favorite")
    parser.add_argument("--collection-id", default=FAV_MEDIA_ID)
    parser.add_argument("--collection-mid", default="")
    args = parser.parse_args()
    if not args.collection_id:
        print("ERROR: 未选择收藏夹/系列，请先在界面读取并选择。")
        return 1
    if args.collection_type == "series" and not args.collection_mid:
        print("ERROR: 系列缺少 UP 主 mid，请重新读取列表后选择。")
        return 1

    os.makedirs(STATE_DIR, exist_ok=True)

    # 分页获取收藏夹所有视频
    medias = fetch_all_medias(args.collection_type, args.collection_id, args.collection_mid)
    print(f"COLLECTION_TOTAL:{len(medias)}")

    # 扫描去重目录，磁盘 .md 文件是去重的权威来源
    disk_avids, disk_bvids = _find_existing_ids()

    # 加载文本记录（仅用于日志参考，不作为去重依据）
    text_processed = set()
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE) as f:
            text_processed = set(line.strip() for line in f if line.strip())

    # 权威去重：仅以磁盘 .md 文件为准
    # 文件名末尾是 bvid（由 yt-dlp 的 video_id 决定），disk_avids 几乎总是空
    # 真正的去重靠 bvid 匹配，avid 仅作补充（future-proof）
    total_disk = len(disk_avids) + len(disk_bvids)
    print(f"PROCESSED:{total_disk} (disk:{total_disk}, text:{len(text_processed)})")

    # 找出新视频：磁盘上无对应 .md 文件
    new_videos = []
    for m in medias:
        avid = str(m.get("aid") or m.get("id") or "")
        bvid = m.get("bvid", "") or m.get("bv_id", "")
        if avid in disk_avids or bvid in disk_bvids:
            continue
        new_videos.append({
            "avid": avid,
            "bvid": bvid,
            "title": m["title"],
            "duration": m.get("duration") or 0,
            "upper": ((m.get("upper") or {}).get("name") or m.get("author") or ""),
            "pubtime": m.get("pubtime", 0),
        })

    if not new_videos:
        print("ALL_CAUGHT_UP")
        return 0

    print(f"NEW_VIDEOS:{len(new_videos)}")
    for v in new_videos:
        mins = v["duration"] // 60
        secs = v["duration"] % 60
        print(f"  - AVID:{v['avid']}")
        print(f"    BVID:{v['bvid']}")
        print(f"    TITLE:{v['title']}")
        print(f"    DURATION:{mins}分{secs}秒")
        print(f"    UPPER:{v['upper']}")
        print(f"    PUBTIME:{v['pubtime']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
