#!/usr/bin/env python3
"""
批量转录 B站收藏夹中的所有新视频 v3.0
功能：
  - 自动扫描收藏夹所有视频（含分页）
  - 支持本地目录批量转录
  - 支持断点续传（已处理视频自动跳过）
  - 自动重试失败任务
  - 生成转录报告 CSV
  - 支持 LLM 摘要自动生成（可选）

配置：编辑项目根目录的 env.local 文件
"""

import csv
import hashlib
import os
import select
import subprocess
import sys
import time

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
SCANNER = os.path.join(SCRIPT_DIR, "bilibili_scanner.py")
TRANSCRIPT_SH = os.path.join(SCRIPT_DIR, "bilibili_transcript.sh")


def _expand_path(raw):
    """展开路径中的 $HOME / $VAR 和 ~ —— 兼容 env.local 的双引号写法"""
    return os.path.expanduser(os.path.expandvars(raw))


STATE_DIR = _expand_path(
    _env.get("STATE_DIR", _env.get("BILIBILI_STATE_DIR", os.path.join(PROJECT_DIR, "indexes", "bilibili-state")))
)
PROCESSED_FILE = os.path.join(STATE_DIR, "processed_videos.txt")
REPORT_FILE = os.path.join(STATE_DIR, "transcript_report.csv")
OUTPUT_DIR = _expand_path(
    _env.get("OUTPUT_DIR", _env.get("BILIBILI_OUTPUT_DIR", os.path.join(PROJECT_DIR, "notes", "_inbox", "bilibili")))
)

CONDA_ENV = _env.get("CONDA_ENV", "course-whisper")
MAX_RETRIES = int(_env.get("MAX_RETRIES", "2"))
BATCH_DELAY = int(_env.get("BATCH_DELAY", "3"))
COOLDOWN_DELAY = int(_env.get("COOLDOWN_DELAY", "30"))

# LLM 摘要配置
SUMMARY_API_KEY = _env.get("SUMMARY_API_KEY", _env.get("DEFAULT_LLM_API_KEY", ""))
SUMMARY_API_URL = _env.get("SUMMARY_API_URL", _env.get("DEFAULT_LLM_API_BASE", "https://api.openai.com/v1/chat/completions"))
SUMMARY_MODEL = _env.get("SUMMARY_MODEL", _env.get("DEFAULT_LLM_MODEL", "gpt-4o-mini"))
SUMMARY_MAX_TOKENS = int(_env.get("SUMMARY_MAX_TOKENS", "80000"))
SUMMARY_MAX_TOKENS_CAP = int(_env.get("SUMMARY_MAX_TOKENS_CAP", str(max(SUMMARY_MAX_TOKENS, 80000))))
SUMMARY_CHUNK_CHARS = int(_env.get("SUMMARY_CHUNK_CHARS", "20000"))
SUMMARY_CHUNK_OVERLAP_CHARS = int(_env.get("SUMMARY_CHUNK_OVERLAP_CHARS", _env.get("QWEN_ORGANIZE_OVERLAP_CHARS", "800")))
SUMMARY_CHUNK_COOLDOWN_DELAY = max(0.0, float(_env.get("SUMMARY_CHUNK_COOLDOWN_DELAY", _env.get("COOLDOWN_DELAY", "0"))))
LLM_TIMEOUT = int(_env.get("LLM_TIMEOUT", "1800"))
LLM_MAX_RETRIES = max(0, int(_env.get("LLM_MAX_RETRIES", "2")))
LLM_RETRY_DELAY = max(0.0, float(_env.get("LLM_RETRY_DELAY", "3")))
PROOFREAD_DOMAINS = _env.get("PROOFREAD_DOMAINS", "").strip()
ENABLE_DIALOGUE_DETECTION = _env.get("ENABLE_DIALOGUE_DETECTION", "false").strip().lower() == "true"

os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_python_cmd():
    """获取 Python 运行命令（conda 环境优先）"""
    if CONDA_ENV:
        # 检查 conda 是否可用
        try:
            result = subprocess.run(
                ["conda", "env", "list"],
                capture_output=True, text=True, timeout=10
            )
            if CONDA_ENV in result.stdout:
                return ["conda", "run", "--no-capture-output", "-n", CONDA_ENV, "python3", "-u"]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return [sys.executable]


def load_processed():
    processed = set()
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE) as f:
            processed = set(line.strip() for line in f if line.strip())
    return processed


def save_processed(avid):
    with open(PROCESSED_FILE, "a") as f:
        f.write(f"{avid}\n")


def get_content_hash(filepath):
    if not os.path.exists(filepath):
        return ""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            h.update(f.read(65536))
        return h.hexdigest()[:16]
    except Exception:
        return ""


def _safe_subprocess(args, **kwargs):
    """运行子进程，用 errors='replace' 处理编码问题。

    macOS 上 yt-dlp --cookies-from-browser 可能输出钥匙串相关的
    非 UTF-8 终端序列，导致 UnicodeDecodeError。此函数强制替换
    无效字节为 U+FFFD 而非抛出异常。
    """
    timeout = kwargs.pop("timeout", None)
    cwd = kwargs.pop("cwd", None)
    result = subprocess.run(
        args,
        capture_output=True,
        cwd=cwd,
        timeout=timeout,
    )
    result.stdout = result.stdout.decode("utf-8", errors="replace")
    result.stderr = result.stderr.decode("utf-8", errors="replace")
    return result


def _stream_subprocess(args, **kwargs):
    """实时转发子进程输出，同时保留 stdout 供后续解析。"""
    timeout = kwargs.pop("timeout", None)
    cwd = kwargs.pop("cwd", None)
    start_time = time.time()
    chunks = []

    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
    )

    stdout_fd = process.stdout.fileno()
    try:
        while True:
            if timeout is not None and time.time() - start_time > timeout:
                process.kill()
                raise subprocess.TimeoutExpired(args, timeout)

            ready, _, _ = select.select([stdout_fd], [], [], 0.5)
            if ready:
                raw = os.read(stdout_fd, 4096)
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace")
                chunks.append(text)
                print(text, end="", flush=True)
            elif process.poll() is not None:
                rest = process.stdout.read()
                if rest:
                    text = rest.decode("utf-8", errors="replace")
                    chunks.append(text)
                    print(text, end="", flush=True)
                break
    finally:
        if process.poll() is None and timeout is not None and time.time() - start_time > timeout:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()

    return subprocess.CompletedProcess(
        args=args,
        returncode=process.wait(),
        stdout="".join(chunks),
        stderr="",
    )


def _extract_output_paths(stdout):
    """从子脚本输出中提取独立一行打印的真实 Markdown 路径。"""
    paths = []
    seen = set()
    for line in stdout.splitlines():
        path = line.strip()
        if not path.endswith(".md"):
            continue
        if not os.path.isabs(path):
            continue
        if not os.path.isfile(path):
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def scan_videos():
    """扫描 B站收藏夹新视频"""
    python_cmd = get_python_cmd()
    result = _safe_subprocess(
        python_cmd + [SCANNER], cwd=PROJECT_DIR
    )

    # 过滤 conda 的干扰输出（conda run 可能在 stdout 或 stderr 中注入噪声）
    stdout_lines = []
    for line in result.stdout.splitlines():
        # 跳过 conda 注入行
        if "conda.cli.main_run" in line or "conda run" in line:
            continue
        stdout_lines.append(line)

    clean_stdout = "\n".join(stdout_lines)
    print(clean_stdout)

    if result.returncode != 0:
        # 提取有意义的错误信息（跳过 conda 噪声）
        stderr_lines = []
        for line in result.stderr.splitlines():
            if "conda.cli.main_run" in line:
                continue
            stderr_lines.append(line)
        meaningful_stderr = "\n".join(stderr_lines).strip()
        if meaningful_stderr:
            print(f"Scanner error: {meaningful_stderr}")

        # 检查是否是收藏夹权限问题，给出明确指引
        if "访问权限" in result.stdout or "权限不足" in result.stdout:
            print("")
            print("💡 提示：收藏夹可能为私有。解决方法：")
            print("  1) 在 B站网页端将该收藏夹设为「公开」")
            print("  2) 或在 env.local 中配置 BILI_COOKIE_FILE：")
            print("     yt-dlp --cookies-from-browser chromium --cookies ./bili_cookies.txt \\")
            print("       --skip-download --print title \"https://www.bilibili.com/video/BVxxx/\"")
            print("     然后在 env.local 中添加: BILI_COOKIE_FILE=\"./bili_cookies.txt\"")
        return []

    # 解析视频列表（使用过滤后的行）
    videos = []
    current = None
    for line in stdout_lines:
        if line.startswith("  - AVID:"):
            if current:
                videos.append(current)
            current = {"avid": line.split("AVID:", 1)[1].strip()}
        elif line.startswith("    BVID:") and current:
            current["bvid"] = line.split("BVID:", 1)[1].strip()
        elif line.startswith("    TITLE:") and current:
            current["title"] = line.split("TITLE:", 1)[1].strip()
        elif line.startswith("    DURATION:") and current:
            current["duration"] = line.split("DURATION:", 1)[1].strip()
        elif line.startswith("    UPPER:") and current:
            current["upper"] = line.split("UPPER:", 1)[1].strip()
        elif line.startswith("    PUBTIME:") and current:
            current["pubtime"] = line.split("PUBTIME:", 1)[1].strip()
    if current:
        videos.append(current)
    return videos


def transcribe_video(bvid, attempt=1, max_retries=1):
    """转录单个 B站视频"""
    url = f"https://www.bilibili.com/video/{bvid}/"
    print(f"\n{'='*70}")
    print(f"🎬 开始转录: {bvid} (尝试 {attempt}/{max_retries})")
    print(f"{'='*70}")

    result = _safe_subprocess(
        ["bash", TRANSCRIPT_SH, url],
        cwd=PROJECT_DIR, timeout=7200,
    )

    if result.stdout:
        print(result.stdout[-2000:])
    if result.stderr:
        stderr_preview = result.stderr.strip()[-500:]
        if stderr_preview:
            print(f"STDERR: {stderr_preview}")

    used_stt = "🎤" in result.stdout

    if "✅ 转录完成" in result.stdout:
        output_paths = _extract_output_paths(result.stdout)
        saved_file = output_paths[-1] if output_paths else None
        transcript_source = None
        for line in result.stdout.splitlines():
            if "转录来源" in line:
                transcript_source = line.replace("📝 转录来源：", "").strip()
                break
        return True, saved_file or "unknown", transcript_source or "unknown", used_stt
    else:
        error_msg = result.stdout[-300:] if result.stdout else "无输出"
        return False, error_msg, None, used_stt


def transcribe_local_dir(local_dir, recursive=False):
    """转录本地目录中的所有媒体文件"""
    print(f"\n{'='*70}")
    print(f"📁 本地目录转录: {local_dir}")
    if recursive:
        print("🔁 递归扫描子目录: 已启用")
    print(f"{'='*70}")

    cmd = ["bash", TRANSCRIPT_SH, "--local-dir", local_dir, "--output-dir", OUTPUT_DIR]
    if recursive:
        cmd.append("--recursive")

    result = _stream_subprocess(
        cmd,
        cwd=PROJECT_DIR, timeout=7200,
    )

    return _extract_output_paths(result.stdout), result.returncode


def _is_retryable_http_status(status_code):
    return status_code in (408, 409, 425, 429) or status_code >= 500


def _call_llm(system_prompt, user_prompt, max_tokens=None, task_name="LLM", max_retries=None):
    """调用 LLM，返回响应文本或 None。临时错误按配置重试。"""
    if not SUMMARY_API_KEY:
        return None

    api_url = SUMMARY_API_URL.rstrip("/")
    if not api_url.endswith("/chat/completions"):
        api_url += "/chat/completions"

    payload = {
        "model": SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens or SUMMARY_MAX_TOKENS,
    }
    retry_count = LLM_MAX_RETRIES if max_retries is None else max(0, max_retries)
    total_attempts = max(1, retry_count + 1)
    last_error = None

    for attempt in range(1, total_attempts + 1):
        try:
            resp = requests.post(
                api_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {SUMMARY_API_KEY}",
                },
                timeout=LLM_TIMEOUT,
            )

            if resp.status_code >= 400:
                preview = resp.text.strip()[:500]
                msg = f"HTTP {resp.status_code}: {preview or resp.reason}"
                if not _is_retryable_http_status(resp.status_code):
                    raise RuntimeError(msg)
                raise requests.HTTPError(msg, response=resp)

            resp_data = resp.json()
            # LM Studio 等本地服务可能不返回 choices
            if "choices" in resp_data:
                choice = resp_data["choices"][0]
                message = choice.get("message", {})
                content = message.get("content", "")
            elif "content" in resp_data:
                content = resp_data["content"]
                choice = {}
            else:
                raise ValueError(f"Unexpected response: {resp_data}")

            if not content or not content.strip():
                finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                current_tokens = int(payload.get("max_tokens") or SUMMARY_MAX_TOKENS)
                usage = resp_data.get("usage", {}) if isinstance(resp_data, dict) else {}
                completion_tokens = usage.get("completion_tokens")
                reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                if finish_reason == "length" and current_tokens < SUMMARY_MAX_TOKENS_CAP:
                    next_tokens = min(SUMMARY_MAX_TOKENS_CAP, max(current_tokens * 2, current_tokens + 1024))
                    payload["max_tokens"] = next_tokens
                    raise ValueError(
                        "Empty LLM response; model used output budget before final content "
                        f"(max_tokens {current_tokens}->{next_tokens}, "
                        f"completion_tokens={completion_tokens}, reasoning_tokens={reasoning_tokens})"
                    )
                raise ValueError(
                    "Empty LLM response "
                    f"(finish_reason={finish_reason}, completion_tokens={completion_tokens}, "
                    f"reasoning_tokens={reasoning_tokens})"
                )

            return content

        except RuntimeError:
            raise
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError) as e:
            last_error = e
            if attempt >= total_attempts:
                break
            wait = LLM_RETRY_DELAY * (2 ** (attempt - 1))
            print(f"   ⚠️ {task_name} 调用失败（第 {attempt}/{total_attempts} 次）: {e}")
            print(f"   ⏳ {wait:g} 秒后重试...")
            time.sleep(wait)
        except requests.RequestException as e:
            last_error = e
            if attempt >= total_attempts:
                break
            wait = LLM_RETRY_DELAY * (2 ** (attempt - 1))
            print(f"   ⚠️ {task_name} 请求异常（第 {attempt}/{total_attempts} 次）: {e}")
            print(f"   ⏳ {wait:g} 秒后重试...")
            time.sleep(wait)

    raise RuntimeError(f"{task_name} 调用失败，已重试 {retry_count} 次: {last_error}")


def _chunk_text_with_overlap(text, max_chars=SUMMARY_CHUNK_CHARS, overlap_chars=SUMMARY_CHUNK_OVERLAP_CHARS):
    """Split long transcript text on safe boundaries with overlap context."""
    text = text.strip()
    if not text or len(text) <= max_chars:
        return [text] if text else []
    overlap_chars = max(0, min(overlap_chars, max_chars // 3))
    chunks = []
    start = 0
    length = len(text)
    while start < length:
        hard_end = min(length, start + max_chars)
        end = hard_end
        if hard_end < length:
            min_boundary = start + max(max_chars // 2, max_chars - max(overlap_chars * 2, 1))
            candidates = [
                text.rfind("\n\n", min_boundary, hard_end),
                text.rfind("\n", min_boundary, hard_end),
                text.rfind("。", min_boundary, hard_end),
                text.rfind("！", min_boundary, hard_end),
                text.rfind("？", min_boundary, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        next_start = max(0, end - overlap_chars)
        if next_start <= start:
            next_start = end
        start = next_start
    return chunks


def _run_chunked_llm(task_name, title, transcript_text, system_prompt, chunk_instruction, combine_instruction, max_tokens=None):
    chunks = _chunk_text_with_overlap(transcript_text)
    if not chunks:
        return None
    if len(chunks) == 1:
        return _call_llm(
            system_prompt,
            f"视频标题：{title}\n\n转录文本：\n{chunks[0]}",
            max_tokens=max_tokens,
            task_name=task_name,
        )

    print(
        f"   🧩 {task_name}: 长文本分为 {len(chunks)} 块 "
        f"(chunk={SUMMARY_CHUNK_CHARS}, overlap={SUMMARY_CHUNK_OVERLAP_CHARS})"
    )
    partials = []
    for index, chunk in enumerate(chunks, 1):
        partial = _call_llm(
            system_prompt,
            (
                f"视频标题：{title}\n\n"
                f"分块：{index}/{len(chunks)}\n"
                "说明：相邻分块可能包含少量重叠上下文；重叠部分仅用于衔接，不要重复生成。\n\n"
                f"{chunk_instruction}\n\n"
                f"转录文本分块：\n{chunk}"
            ),
            max_tokens=max_tokens,
            task_name=f"{task_name} 分块 {index}/{len(chunks)}",
        )
        if partial:
            partials.append(f"## 分块 {index}\n\n{partial.strip()}")
        if SUMMARY_CHUNK_COOLDOWN_DELAY > 0 and index < len(chunks):
            print(f"   ⏳ {SUMMARY_CHUNK_COOLDOWN_DELAY:g} 秒后处理下一个分块...")
            time.sleep(SUMMARY_CHUNK_COOLDOWN_DELAY)

    if not partials:
        return None

    joined = "\n\n".join(partials)
    return _call_llm(
        "你是本地知识库整理助手。请综合多个分块结果，去除相邻分块重叠造成的重复内容，保持原始顺序和事实边界。",
        (
            f"视频标题：{title}\n\n"
            f"{combine_instruction}\n\n"
            "分块结果：\n"
            f"{joined}"
        ),
        max_tokens=max_tokens,
        task_name=f"{task_name} 综合",
    )


def _detect_dialogue(text, sample_chars=3000):
    """快速判断转录文本是否为对话/访谈/多人讨论。

    取文本前 sample_chars 字符，用 LLM 判断是否包含多人对话特征：
    问答交替、观点交锋、称呼切换、语气变化等。
    """
    sample = text[:sample_chars]
    try:
        result = _call_llm(
            "你是一个文本分析助手。请判断以下转录文本是否属于对话/访谈/多人讨论类型。"
            "不要输出思考过程，只回复一个字：「是」或「否」。如果无法判断，回复「否」。\n"
            "判断依据：是否出现明显的多人轮流发言特征，如问答交替、观点交锋、"
            "不同语气或立场切换、明显的说话人切换等。",
            f"转录文本片段：\n{sample}",
            task_name="对话检测",
            max_retries=0,
        )
        if result and "是" in result:
            return True
    except Exception:
        pass
    return False


def _build_domain_prompt(domains_str):
    """根据 PROOFREAD_DOMAINS 配置生成领域专有名词校对提示。

    默认覆盖金融和计算机领域。用户可在 env.local 中通过 PROOFREAD_DOMAINS
    追加额外领域（逗号分隔，如 "medical,legal,engineering"）。
    """
    domain_map = {
        "finance": (
            "6a) 金融领域：修正金融术语的语音识别错误，如「股权→债券」「期货→期权」"
            "「量化→量价」「对冲→对充」「杠杆→钢杆」「IPO→I P O」等；"
            "保持「PE/VC/ROE/ROI/NPV/EBITDA」等缩写格式正确\n"
        ),
        "computer": (
            "6b) 计算机领域：修正技术术语的识别错误，如「API→A P I」「SDK→S D K」"
            "「Kubernetes→K 8 s」「Docker→道客」「Git→给特」「SQL→C Q L」"
            "「JSON→J 桑」「RESTful→REST ful」「微服务→微浮物」「容器化→荣启华」等\n"
        ),
        "medical": (
            "6c) 医学领域：修正医学术语的识别错误，如药名、疾病名、解剖学术语等；"
            "保持「CT/MRI/DNA/RNA」等缩写格式正确\n"
        ),
        "legal": (
            "6d) 法律领域：修正法律术语的识别错误，如「合同法→和同法」「仲裁→中才」"
            "「知识产权→知识产全」「法人→发人」等\n"
        ),
        "engineering": (
            "6e) 工程领域：修正工程术语的识别错误，如「架构→加购」「模块→磨快」"
            "「耦合→偶合」「并发→病发」「冗余→绒余」等\n"
        ),
    }

    # 默认领域
    domains = ["finance", "computer"]
    if domains_str:
        extra = [d.strip().lower() for d in domains_str.split(",") if d.strip()]
        domains.extend(extra)

    seen = set()
    letters = "abcdefgh"
    idx = 0
    parts = []
    for d in domains:
        if d in seen:
            continue
        seen.add(d)
        if d in domain_map:
            # Replace placeholder numbering with actual sequential numbering
            rule_num = f"6{letters[idx]})"
            desc = domain_map[d]
            # The stored desc has hardcoded numbering, rebuild it
            desc_clean = desc.split(")", 1)[1] if ")" in desc else desc
            parts.append(f"{rule_num}{desc_clean}")
            idx += 1

    if not parts:
        return ""

    return "".join(parts) + "\n"


def generate_summary(filepath, progress_label=None):
    """使用 LLM 为转录文件生成摘要、思维导图、校对版本

    三阶段处理：
      1. 结构化摘要（替换第一部分占位符）
      2. 思维导图（替换思维导图占位符）
      3. 原文校对——修复 ASR 错别字，优化可读性（替换校对占位符）
    每个阶段独立，一个失败不影响其他。
    """
    label = progress_label or os.path.basename(filepath)

    if not SUMMARY_API_KEY:
        return False
    if not os.path.exists(filepath):
        return False

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 如果所有占位符都已被替换，跳过
    placeholders = [
        "【AI待处理：请设置 SUMMARY_API_KEY 后重新运行以生成结构化摘要】",
        "【AI待处理：请设置 SUMMARY_API_KEY 后重新运行以生成思维导图】",
        "【AI待处理：请设置 SUMMARY_API_KEY 后重新运行以生成校对版本】",
    ]
    # 兼容旧版占位符
    old_summary_ph = "【AI待处理：请阅读全文后，替换此行，写结构化摘要】"

    has_any = any(ph in content for ph in placeholders) or old_summary_ph in content
    if not has_any:
        return False

    title = ""
    for line in content.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            break
        if "视频标题：" in line:
            title = line.split("视频标题：", 1)[1].strip()
            break

    # 提取原文（在文件末尾，"## AI校对" 之后）
    # 新版模板：AI校对在前，原文在末尾（默认用 <details> 折叠，或 ## 完整原文 展开）
    text_start = content.find("## AI校对")
    if text_start == -1:
        return False

    # 跳过 AI校对 段落到内容结束
    text_start = content.find("\n---", text_start)
    if text_start == -1:
        text_start = content.find("【AI待处理", text_start)
        if text_start == -1:
            return False
    text_start = content.find("\n", text_start + 4)
    if text_start == -1:
        return False

    # 从 AI校对 段落后取到文件末尾（包含原文）
    transcript_text = content[text_start:].strip()

    # 如果原文在 <details> 里，提取纯文本；如果在 ## 完整原文 标题下，跳过标题
    if transcript_text.startswith("<details>"):
        # 跳过 <details> 和 <summary> 标签
        inner = transcript_text
        for tag in ["<details>", "</details>", "<summary>", "</summary>"]:
            inner = inner.replace(tag, "")
        # 跳过 summary 内容行（📄 完整原文）
        nl = inner.find("\n")
        if nl != -1:
            inner = inner[nl:].strip()
        transcript_text = inner
    elif transcript_text.startswith("## 完整原文"):
        nl = transcript_text.find("\n")
        if nl != -1:
            transcript_text = transcript_text[nl:].strip()

    changed = False

    # ===== 第 1 阶段：结构化摘要 =====
    summary_ph = placeholders[0] if placeholders[0] in content else old_summary_ph
    if summary_ph in content:
        print(f"   📝 {label}: 生成摘要...")
        try:
            summary = _run_chunked_llm(
                f"摘要生成 {label}",
                title,
                transcript_text,
                "你是一个视频摘要助手。请对以下转录文本生成结构化摘要，"
                "包含：1) 核心观点 2) 主要论点 3) 关键结论。用中文回复，简洁明了。",
                "请为当前分块生成结构化摘要，保留本分块的关键事实、论证链条和结论。",
                "请综合所有分块摘要，生成一份不重复、覆盖全文的结构化摘要，包含：1) 核心观点 2) 主要论点 3) 关键结论。",
            )
            if summary:
                # 用 replace 精确匹配（old_summary_ph 和 new ph 不同）
                if old_summary_ph in content:
                    content = content.replace(old_summary_ph, summary.strip())
                else:
                    content = content.replace(placeholders[0], summary.strip())
                changed = True
                print(f"   ✅ {label}: 摘要已写入")
        except Exception as e:
            print(f"   ⚠️ {label}: 摘要生成失败: {e}")

    # ===== 第 2 阶段：思维导图 =====
    if placeholders[1] in content:
        print(f"   🧠 {label}: 生成思维导图...")
        try:
            mindmap = _run_chunked_llm(
                f"思维导图生成 {label}",
                title,
                transcript_text,
                "你是一个结构化整理助手。请根据转录文本生成一份思维导图，"
                "使用缩进的 Markdown 列表格式（2空格缩进）。\n"
                "格式示例：\n"
                "- 主题\n  - 子主题\n    - 要点\n"
                "要求：层次清晰、要点精炼、覆盖全文核心内容。",
                "请为当前分块生成 Markdown 缩进列表格式的思维导图。",
                "请综合所有分块思维导图，生成一份去重后的全片 Markdown 思维导图，保持层次清晰。",
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            if mindmap:
                content = content.replace(placeholders[1], mindmap.strip())
                changed = True
                print(f"   ✅ {label}: 思维导图已写入")
        except Exception as e:
            print(f"   ⚠️ {label}: 思维导图生成失败: {e}")

    # ===== 第 3 阶段：原文校对 =====
    if placeholders[2] in content:
        print(f"   🔍 {label}: AI校对转录文本...")
        try:
            # 可选：先判断是否为对话
            is_dialogue = False
            if ENABLE_DIALOGUE_DETECTION:
                is_dialogue = _detect_dialogue(transcript_text)
            if is_dialogue:
                print(f"   💬 {label}: 检测为对话内容，校对时将标注说话角色")

            # 构建领域专有名词提示
            domain_terms = _build_domain_prompt(PROOFREAD_DOMAINS)

            if is_dialogue:
                proofread_prompt = (
                    "你是一个文字校对员。以下是语音转文字的对话转录文本。\n"
                    "规则：\n"
                    "1) 修正明显的同音错别字和语音识别错误\n"
                    "2) 修复断句问题（合并不合理的断句、拆分超长句）\n"
                    "3) 去除口语填充词（如过多的「嗯」「啊」「就是说」）\n"
                    "4) 修正标点符号，使文本更易读\n"
                    "5) 严禁增删实质性内容，严禁改变原意和说话风格\n"
                    "6) 根据语义判断说话人切换的位置，为每个发言段落标注角色。\n"
                    "   格式使用「角色名：」或「说话人：」前缀（如「主持人：」「嘉宾：」）。\n"
                    "   如果无法确定具体角色名，使用「说话人A：」「说话人B：」区分。\n"
                    "   连续同一角色的发言合并为一段，角色切换时另起新段。\n"
                    + domain_terms +
                    "7) 输出完整的校对后文本（含角色标注）"
                )
            else:
                proofread_prompt = (
                    "你是一个文字校对员。请校对并修正以下语音转文字的转录文本。\n"
                    "规则：\n"
                    "1) 修正明显的同音错别字和语音识别错误\n"
                    "2) 修复断句问题（合并不合理的断句、拆分超长句）\n"
                    "3) 去除口语填充词（如过多的「嗯」「啊」「就是说」）\n"
                    "4) 修正标点符号，使文本更易读\n"
                    "5) 严禁增删实质性内容，严禁改变原意和说话风格\n"
                    + domain_terms +
                    "6) 输出完整的校对后文本"
                )

            proofread = _run_chunked_llm(
                f"AI校对 {label}",
                title,
                transcript_text,
                proofread_prompt,
                "请只校对当前分块，修正明显 ASR 错误、断句和标点；不要新增原文没有的信息。",
                "请把所有分块校对结果合并为一份完整校对文本，去除 overlap 重复，保持原始顺序和语义。",
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            if proofread:
                content = content.replace(placeholders[2], proofread.strip())
                changed = True
                print(f"   ✅ {label}: AI校对已写入")
        except Exception as e:
            print(f"   ⚠️ {label}: AI校对失败: {e}")

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    return changed


def print_summary_stats(report_rows):
    """打印转录来源分布统计"""
    sources = {}
    for row in report_rows:
        if row["status"] == "success":
            s = row["source"]
            sources[s] = sources.get(s, 0) + 1
    if sources:
        print(f"\n   📝 转录来源分布:")
        for src, count in sorted(sources.items(), key=lambda x: -x[1]):
            print(f"      - {src}: {count} 个")


def _list_markdown_files(path):
    if os.path.isfile(path):
        return [path] if path.endswith(".md") else []

    files = []
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {"epub", "epub-build"}]
        for name in names:
            if name.endswith(".md"):
                files.append(os.path.join(root, name))
    return sorted(files)


def run_summary_only(target_path=None):
    """只为已有 Markdown 文件补齐 LLM 摘要/导图/校对。"""
    if not SUMMARY_API_KEY:
        print("❌ 未设置 SUMMARY_API_KEY，无法执行 LLM 后处理")
        return 1

    target = _expand_path(target_path) if target_path else os.path.join(OUTPUT_DIR, "local")
    if not os.path.exists(target):
        print(f"❌ 路径不存在: {target}")
        return 1

    files = _list_markdown_files(target)
    if not files:
        print(f"❌ 没有找到 Markdown 文件: {target}")
        return 1

    print("=" * 70)
    print("📝 仅补齐 AI 后处理")
    print("=" * 70)
    print(f"📂 目标: {target}")
    print(f"📄 Markdown 文件: {len(files)} 个")

    changed_count = 0
    skipped_count = 0
    failed_count = 0
    start_time = time.time()

    for i, filepath in enumerate(files, 1):
        progress_label = f"[{i}/{len(files)}] {os.path.basename(filepath)}"
        print(f"\n📄 {progress_label}")
        changed = False
        try:
            changed = generate_summary(filepath, progress_label=progress_label)
        except Exception as e:
            failed_count += 1
            print(f"   ⚠️ {progress_label}: AI 后处理异常: {e}")
            continue

        if changed:
            changed_count += 1
            if COOLDOWN_DELAY > 0 and i < len(files):
                print(f"   🥶 {progress_label}: LLM 散热等待 {COOLDOWN_DELAY} 秒...")
                time.sleep(COOLDOWN_DELAY)
        else:
            skipped_count += 1
            print(f"   ⏭️  {progress_label}: 无待处理占位符，跳过")

    total_time = time.time() - start_time
    print(f"\n{'=' * 70}")
    print("📊 AI 后处理完成")
    print(f"   写入: {changed_count} 个")
    print(f"   跳过: {skipped_count} 个")
    print(f"   失败: {failed_count} 个")
    print(f"   耗时: {int(total_time // 60)}分{int(total_time % 60)}秒")
    print(f"{'=' * 70}")
    return 1 if failed_count else 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="B站收藏夹批量转录")
    parser.add_argument("--local-dir", default=None, help="转录本地目录中的媒体文件")
    parser.add_argument("--recursive", action="store_true", help="本地目录模式下递归扫描子目录")
    parser.add_argument(
        "--summary-only",
        nargs="?",
        const="",
        default=None,
        help="只为已有 Markdown 补齐 LLM 后处理，可选指定文件或目录（默认 OUTPUT_DIR/local）",
    )
    args = parser.parse_args()

    # ===== 模式：仅补齐 LLM 后处理 =====
    if args.summary_only is not None:
        return run_summary_only(args.summary_only or None)

    # ===== 模式：本地目录转录 =====
    if args.local_dir:
        local_dir = os.path.expanduser(args.local_dir)
        if not os.path.isdir(local_dir):
            print(f"❌ 目录不存在: {local_dir}")
            return 1

        print("=" * 70)
        print("📼 本地目录批量转录 v3.0")
        print("=" * 70)

        start_time = time.time()
        output_files, returncode = transcribe_local_dir(local_dir, recursive=args.recursive)

        # 生成摘要
        if SUMMARY_API_KEY and output_files:
            print(f"\n📝 生成 AI 摘要...")
            for i, f in enumerate(output_files, 1):
                progress_label = f"[{i}/{len(output_files)}] {os.path.basename(f)}"
                print(f"\n📄 {progress_label}")
                changed = False
                try:
                    changed = generate_summary(f, progress_label=progress_label)
                except Exception as e:
                    print(f"   ⚠️ {progress_label}: 摘要生成异常: {e}")
                # LLM 散热
                if changed and COOLDOWN_DELAY > 0 and i < len(output_files):
                    print(f"   🥶 {progress_label}: LLM 散热等待 {COOLDOWN_DELAY} 秒...")
                    time.sleep(COOLDOWN_DELAY)

        total_time = time.time() - start_time
        print(f"\n⏱️  总耗时: {int(total_time // 60)}分{int(total_time % 60)}秒")
        return returncode

    # ===== 模式：B站收藏夹转录 =====
    print("=" * 70)
    print("📼 B站收藏夹批量转录 v3.0")
    print("=" * 70)

    videos = scan_videos()
    if not videos:
        print("没有新视频需要转录")
        return 0

    # 扫描器已通过磁盘文件做了权威去重，videos 中就是真正需要转录的
    pending = videos
    total = len(videos)
    remaining = len(pending)

    print(f"\n📊 总计 {total} 个视频")
    print(f"✅ 已处理 {total - remaining} 个")
    print(f"⏳ 待处理 {remaining} 个")

    if remaining == 0:
        print("🎉 全部视频已转录完成！")
        return 0

    enable_summary = bool(SUMMARY_API_KEY)

    if enable_summary:
        print(f"📝 AI摘要生成: 已启用 (模型: {SUMMARY_MODEL})")
    else:
        print(f"📝 AI摘要生成: 未启用（在 env.local 中设置 SUMMARY_API_KEY 可开启）")

    start_time = time.time()
    success_count = 0
    fail_count = 0
    report_rows = []

    for i, v in enumerate(pending, 1):
        bvid = v["bvid"]
        current_remaining = remaining - i + 1

        elapsed = time.time() - start_time if i > 1 else 0
        if elapsed > 0 and success_count > 0:
            avg_time = elapsed / success_count
            eta = avg_time * current_remaining
            print(f"\n⏱️  已用: {int(elapsed // 60)}分{int(elapsed % 60)}秒"
                  f" | 预计剩余: {int(eta // 60)}分{int(eta % 60)}秒")

        print(f"\n📌 [{total - remaining + i}/{total}] {v['title']}")
        print(f"   ⏱️  {v['duration']} | 👤 {v['upper']}")

        # 带重试的转录
        ok = False
        output_file = None
        transcript_source = None
        used_stt = False
        max_attempts = MAX_RETRIES + 1
        for attempt in range(1, max_attempts + 1):
            ok, output_path, transcript_source, used_stt = transcribe_video(
                bvid, attempt, max_attempts
            )
            if ok:
                output_file = output_path
                break
            if used_stt:
                print("   ⏭️ Qwen3-ASR 失败，跳过重试（模型加载耗时）")
                break
            if attempt <= MAX_RETRIES:
                wait = BATCH_DELAY * attempt
                print(f"   ⏳ 等待 {wait} 秒后重试...")
                time.sleep(wait)

        if ok and output_file and output_file != "unknown":
            content_hash = get_content_hash(output_file)

            report_rows.append({
                "bvid": bvid,
                "title": v["title"],
                "author": v["upper"],
                "duration": v["duration"],
                "source": transcript_source or "unknown",
                "output_file": output_file,
                "content_hash": content_hash,
                "status": "success",
                "attempts": attempt,
            })

            success_count += 1
            save_processed(v["avid"])
            print(f"   ✅ [{success_count}/{remaining}] 成功! 来源: {transcript_source}")

            # AI摘要生成
            if enable_summary and output_file and output_file != "unknown":
                progress_label = f"[{i}/{len(pending)}] {v['title']}"
                changed = False
                try:
                    changed = generate_summary(output_file, progress_label=progress_label)
                except Exception as e:
                    print(f"   ⚠️ {progress_label}: 摘要生成异常: {e}")
                # LLM 散热
                if changed and COOLDOWN_DELAY > 0 and i < len(pending):
                    print(f"   🥶 {progress_label}: LLM 散热等待 {COOLDOWN_DELAY} 秒...")
                    time.sleep(COOLDOWN_DELAY)

        else:
            report_rows.append({
                "bvid": bvid,
                "title": v["title"],
                "author": v["upper"],
                "duration": v["duration"],
                "source": "失败",
                "output_file": "",
                "content_hash": "",
                "status": f"failed_after_{attempt}_attempts",
                "attempts": attempt,
            })

            fail_count += 1
            print(f"   ❌ [{fail_count}] 失败 (尝试{attempt}次后放弃)")

        # 视频间防风控延迟
        if i < len(pending):
            if BATCH_DELAY > 0:
                print(f"   ⏳ 等待 {BATCH_DELAY} 秒后处理下一视频...")
                time.sleep(BATCH_DELAY)

    # 生成报告
    total_time = time.time() - start_time
    print(f"\n{'=' * 70}")
    print(f"📊 批量转录完成")
    print(f"{'=' * 70}")
    print(f"   总计: {remaining} 个")
    print(f"   成功: {success_count} 个 ✅")
    print(f"   失败: {fail_count} 个 {'❌' if fail_count else '✅'}")
    print(f"   耗时: {int(total_time // 60)}分{int(total_time % 60)}秒")

    if report_rows:
        with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "bvid", "title", "author", "duration",
                "source", "output_file", "content_hash",
                "status", "attempts",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(report_rows)
        print(f"   📄 报告已保存: {REPORT_FILE}")
        print_summary_stats(report_rows)

    # 列出失败项
    if fail_count:
        print(f"\n   ❌ 失败列表:")
        for row in report_rows:
            if row["status"] != "success":
                print(f"      - {row['bvid']} {row['title']}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
