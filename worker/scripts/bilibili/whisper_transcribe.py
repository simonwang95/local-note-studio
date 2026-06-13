#!/usr/bin/env python3
"""
Whisper 语音转录辅助脚本 v1.0
基于 Apple MLX 的 Whisper large-v3-turbo，专为 Apple Silicon 优化。

输出格式（写入 --output-file）：
  第一行：转录来源字符串（如 "Whisper-large-v3-turbo（MLX加速）"）
  第二行起：完整转录文本
"""

import argparse
import os
import sys
import threading
import time


def _format_seconds(seconds):
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    return f"{seconds // 60}分{seconds % 60}秒"


def _probe_duration(audio_path):
    try:
        import subprocess

        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        return None
    return None


def _start_progress_heartbeat(audio_path, interval):
    if interval <= 0:
        return None, None

    stop_event = threading.Event()
    duration = _probe_duration(audio_path)
    filename = os.path.basename(audio_path)
    start_time = time.time()

    def heartbeat():
        while not stop_event.wait(interval):
            elapsed = time.time() - start_time
            if duration and duration > 0:
                ratio = elapsed / duration
                print(
                    f"   ⏳ Whisper 转录中: {filename} | 已用 {_format_seconds(elapsed)} | "
                    f"音频 {_format_seconds(duration)} | {ratio:.2f}x 实时",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    f"   ⏳ Whisper 转录中: {filename} | 已用 {_format_seconds(elapsed)}",
                    file=sys.stderr,
                    flush=True,
                )

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    return stop_event, start_time


def main():
    parser = argparse.ArgumentParser(description="Whisper (MLX) 语音转录")
    parser.add_argument("--audio", required=True, help="音频文件路径")
    parser.add_argument("--output-file", required=True, help="输出文件路径（第一行=来源，后续=文本）")
    parser.add_argument("--model-path", required=True, help="本地 Whisper 模型路径")
    parser.add_argument("--language", default=None, help="转录语言（如 zh, en, ja），默认自动检测")
    parser.add_argument("--prompt", default=None, help="Whisper 初始提示词，用于提供语言、术语或风格提示")
    parser.add_argument("--progress-interval", type=float, default=None,
                        help="转录进度提示间隔（秒），0=关闭；默认读取 ASR_PROGRESS_INTERVAL 或 30")
    args = parser.parse_args()

    if not os.path.exists(args.audio):
        print(f"错误: 音频文件不存在: {args.audio}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.model_path):
        print(f"错误: 模型路径不存在: {args.model_path}", file=sys.stderr)
        sys.exit(1)

    # === 语言配置 ===
    language = args.language or os.environ.get("ASR_LANGUAGE", None)
    if language:
        print(f"   🌐 指定语言: {language}", file=sys.stderr)
    prompt = args.prompt or os.environ.get("ASR_PROMPT", None)
    if prompt:
        print(f"   💡 使用转录提示: {prompt[:80]}", file=sys.stderr)

    model_name = os.path.basename(args.model_path.rstrip("/"))
    print(f"🎤 使用本地 Whisper 模型: {model_name}", file=sys.stderr)
    print(f"   📦 路径: {args.model_path}", file=sys.stderr)
    print(f"   ⏳ 加载模型中...", file=sys.stderr)

    try:
        import mlx_whisper
    except ImportError:
        print("错误: 请安装 mlx-whisper: pip install mlx-whisper", file=sys.stderr)
        sys.exit(1)

    try:
        print(f"   ✅ 模型加载完成", file=sys.stderr)
        print(f"   🎤 正在转录...", file=sys.stderr)

        transcribe_kwargs = {"path_or_hf_repo": args.model_path}
        if language:
            transcribe_kwargs["language"] = language
        if prompt:
            transcribe_kwargs["initial_prompt"] = prompt

        progress_interval = args.progress_interval
        if progress_interval is None:
            progress_interval = float(os.environ.get("ASR_PROGRESS_INTERVAL", "30"))
        stop_event, start_time = _start_progress_heartbeat(args.audio, progress_interval)
        try:
            result = mlx_whisper.transcribe(
                args.audio,
                **transcribe_kwargs,
            )
        finally:
            if stop_event:
                stop_event.set()

        transcript = result.get("text", "").strip()

        if not transcript:
            print("错误: 转录结果为空", file=sys.stderr)
            sys.exit(1)

        # === 构造来源描述 ===
        source = f"Whisper-{model_name}（MLX加速）"

        # === 写入输出文件 ===
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(source + "\n")
            f.write(transcript + "\n")

        elapsed = time.time() - start_time if start_time else None
        if elapsed:
            print(f"   ✅ 转录完成，用时 {_format_seconds(elapsed)}", file=sys.stderr)
        else:
            print(f"   ✅ 转录完成", file=sys.stderr)
        print(f"   📄 来源: {source}", file=sys.stderr)

    except Exception as e:
        print(f"错误: 转录失败 - {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
