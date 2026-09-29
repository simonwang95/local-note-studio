#!/usr/bin/env python3
"""
Whisper 语音转录辅助脚本 v1.0
基于 Apple MLX 的 Whisper large-v3-turbo，专为 Apple Silicon 优化。

输出格式（写入 --output-file）：
  第一行：转录来源字符串（如 "Whisper-large-v3-turbo（MLX加速）"）
  第二行起：完整转录文本
"""

import argparse
import hashlib
import math
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcript_quality import compact_text, repetition_errors, save_transcript_diagnostic


def suspect_intervals(result, duration, has_audio):
    """Locate decode loops and unexplained timestamp gaps over audible audio."""
    intervals = []
    previous_end = 0.0
    recent = []
    for segment in result.get("segments", []):
        start = max(0.0, float(segment.get("start", 0)))
        end = min(duration, float(segment.get("end", start)))
        text = segment.get("text", "").strip()
        if start - previous_end >= 20 and has_audio(previous_end, start):
            intervals.append((previous_end, start))
        if text and (not compact_text(text) or repetition_errors(text)) and has_audio(start, max(start + 1, end)):
            intervals.append((start, max(start + 1, end)))
        recent = [(a, t) for a, t in recent if start - a <= 30]
        recent.append((start, text))
        if repetition_errors(" ".join(t for _, t in recent)) and has_audio(recent[0][0], end):
            intervals.append((recent[0][0], end))
        previous_end = max(previous_end, end)
    if duration - previous_end >= 20 and has_audio(previous_end, duration):
        intervals.append((previous_end, duration))
    return sorted(set(intervals))


def repair_windows(intervals, segments, duration):
    """Use minute-sized retry windows, expanded to avoid cutting old segments."""
    windows = []
    for start, end in intervals:
        left = max(0.0, (int(start) // 60) * 60.0)
        right = min(duration, math.ceil(max(start + 0.01, end) / 60) * 60.0)
        # Include complete crossing segments before replacing a span.
        for s in segments:
            if s.get("end", 0) > left and s.get("start", 0) < right:
                left = min(left, s["start"])
                right = max(right, s["end"])
        if windows and left <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(right, windows[-1][1]))
        else:
            windows.append((left, min(duration, right)))
    return windows


def transcribe_with_repair(audio, transcribe, kwargs, has_audio):
    """One full pass, then one bounded pass over affected spans. Retain evidence."""
    duration = len(audio) / 16000
    initial = transcribe(audio, **kwargs)
    current = dict(initial)
    current["segments"] = [dict(s) for s in initial.get("segments", [])]
    attempts = []
    intervals = suspect_intervals(initial, duration, has_audio)
    windows = repair_windows(intervals, current["segments"], duration)
    # Widespread corruption must fail for review, not launch an unbounded batch.
    if len(windows) > 8 or any(end - start > 180 for start, end in windows):
        return initial, current, attempts, ["ASR异常范围过大，需检查音频或更换转写参数"]
    for start, end in windows:
        print(f"   🔁 ASR异常片段重转: {start:.1f}–{end:.1f}秒", file=sys.stderr, flush=True)
        options = dict(kwargs, condition_on_previous_text=False)
        if initial.get("language"):
            options["language"] = initial["language"]
        try:
            retry = transcribe(audio[int(start * 16000):int(end * 16000)], **options)
        except Exception as exc:
            attempts.append({"start": start, "end": end, "error": str(exc)})
            continue
        attempts.append({"start": start, "end": end, "result": retry})
        local_audio = lambda a, b: has_audio(start + a, min(end, start + b))
        if not compact_text(retry.get("text", "")) or suspect_intervals(retry, end - start, local_audio):
            continue
        shifted = [dict(s, start=s["start"] + start, end=s["end"] + start) for s in retry.get("segments", [])]
        before = [s for s in current["segments"] if s["end"] <= start]
        after = [s for s in current["segments"] if s["start"] >= end]
        current["segments"] = before + shifted + after
        current["text"] = " ".join(s.get("text", "").strip() for s in current["segments"]).strip()
    unresolved = suspect_intervals(current, duration, has_audio)
    errors = [f"ASR仍有异常片段 {a:.1f}–{b:.1f}秒" for a, b in unresolved]
    errors.extend(repetition_errors(current.get("text", "")))
    if not compact_text(current.get("text", "")):
        errors.append("ASR未返回可用文字")
    return initial, current, attempts, errors


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
    except ImportError as err:
        print("错误: mlx-whisper 导入失败。", file=sys.stderr)
        print(f"   Python: {sys.executable}", file=sys.stderr)
        print(f"   详情: {err}", file=sys.stderr)
        print("   请在托管环境点击“安装/修复”，或在当前 Python 环境安装/修复 mlx-whisper。", file=sys.stderr)
        sys.exit(1)

    try:
        print(f"   ✅ 模型加载完成", file=sys.stderr)
        print(f"   🎤 正在转录...", file=sys.stderr)

        transcribe_kwargs = {
            "path_or_hf_repo": args.model_path,
            "condition_on_previous_text": False,
        }
        if language:
            transcribe_kwargs["language"] = language
        if prompt:
            transcribe_kwargs["initial_prompt"] = prompt

        progress_interval = args.progress_interval
        if progress_interval is None:
            progress_interval = float(os.environ.get("ASR_PROGRESS_INTERVAL", "30"))
        stop_event, start_time = _start_progress_heartbeat(args.audio, progress_interval)
        try:
            import numpy as np
            audio = np.asarray(mlx_whisper.audio.load_audio(args.audio))

            def has_audio(start, end):
                samples = audio[int(start * 16000):int(end * 16000)]
                # A conservative energy screen avoids retrying genuine silence.
                return bool(len(samples) and np.sqrt(np.mean(samples ** 2)) > 0.008)

            initial, result, repairs, errors = transcribe_with_repair(
                audio, mlx_whisper.transcribe, transcribe_kwargs, has_audio,
            )
            audio_hash = hashlib.sha256(audio.tobytes()).hexdigest()
            cache = save_transcript_diagnostic("asr", audio_hash + str(time.time_ns()), {
                "schema_version": 1, "audio_sha256": audio_hash,
                "source_ref": os.environ.get("ASR_SOURCE_REF", args.audio),
                "duration_seconds": len(audio) / 16000,
                "parameters": transcribe_kwargs, "initial": initial,
                "repairs": repairs, "result": result, "errors": errors,
            })
            if cache:
                print(f"   🗂️ ASR原文与时间戳缓存: {cache}", file=sys.stderr, flush=True)
            if errors:
                raise RuntimeError("ASR质量检查失败：" + "；".join(errors))
        finally:
            if stop_event:
                stop_event.set()

        # Preserve ASR phrase boundaries for safe downstream proofreading splits.
        transcript = "\n".join(s.get("text", "").strip() for s in result.get("segments", [])
                               if s.get("text", "").strip()) or result.get("text", "").strip()

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
