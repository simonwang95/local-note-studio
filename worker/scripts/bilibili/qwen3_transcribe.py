#!/usr/bin/env python3
"""
Qwen3-ASR 转录辅助脚本 v1.3
自动检测设备 (CUDA/ROCm/MPS/CPU)，自动选择模型 (1.7B/0.6B)

被 bilibili_transcript.sh 第3级降级调用：
  有独显 → Qwen3-ASR-1.7B
  无独显 → Qwen3-ASR-0.6B

支持本地模型路径（--local-model），跳过 HuggingFace 下载。

模型缓存目录：通过环境变量 HF_HOME 控制（由调用方设置）

输出格式（写入 --output-file）：
  第一行：转录来源字符串（如 "Qwen3-ASR-1.7B（GPU加速）"）
  第二行起：完整转录文本
"""

import argparse
import os
import sys


def detect_device():
    """检测最佳可用设备，返回 device_map 字符串"""
    try:
        import torch
    except ImportError:
        return "cpu"

    # NVIDIA CUDA / AMD ROCm
    if torch.cuda.is_available():
        return "cuda"

    # Apple Metal (MPS)
    if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        return "mps"

    return "cpu"


def device_label(device_map):
    return {"cuda": "CUDA", "mps": "Apple MPS", "cpu": "CPU"}.get(device_map, device_map.upper())


def select_model(device_map):
    """根据设备自动选择模型版本"""
    if device_map == "cpu":
        return "Qwen/Qwen3-ASR-0.6B", "0.6B"
    return "Qwen/Qwen3-ASR-1.7B", "1.7B"


def main():
    parser = argparse.ArgumentParser(description="Qwen3-ASR 语音转录")
    parser.add_argument("--audio", required=True, help="音频文件路径")
    parser.add_argument("--output-file", required=True, help="输出文件路径（第一行=来源，后续=文本）")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"],
                        help="设备选择 (默认: auto 自动检测)")
    parser.add_argument("--model-cache-dir", default=None,
                        help="模型缓存目录（优先级高于 HF_HOME 环境变量）")
    parser.add_argument("--local-model", default=None,
                        help="本地模型路径（设置后跳过 HuggingFace 下载，直接从本地加载）")
    parser.add_argument("--force-cpu", action="store_true", default=False,
                        help="强制使用 CPU 推理（Apple Silicon MPS 内存超限时使用）")
    args = parser.parse_args()

    if not os.path.exists(args.audio):
        print(f"错误: 音频文件不存在: {args.audio}", file=sys.stderr)
        sys.exit(1)

    # === 模型缓存目录 ===
    # 优先级: --model-cache-dir 参数 > HF_HOME 环境变量 > 默认 ~/.cache/huggingface
    cache_dir = args.model_cache_dir
    if not cache_dir:
        cache_dir = os.environ.get("HF_HOME", None)

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        print(f"📦 模型缓存目录: {cache_dir}", file=sys.stderr)

    # === 设备检测 ===
    if args.force_cpu:
        device_map = "cpu"
        print(f"   ⚙️  强制 CPU 模式（绕过 MPS 内存限制）", file=sys.stderr)
    else:
        device_map = detect_device()
        if args.device != "auto":
            device_map = args.device

    # === 模型选择 ===
    local_model = args.local_model or os.environ.get("ASR_LOCAL_MODEL", "")
    if local_model:
        if not os.path.exists(local_model):
            print(f"错误: 本地模型路径不存在: {local_model}", file=sys.stderr)
            sys.exit(1)
        model_name = local_model
        model_short = os.path.basename(local_model)
        print(f"🎤 使用本地模型: {local_model}", file=sys.stderr)
    else:
        model_name, model_short = select_model(device_map)
        print(f"🎤 使用 Qwen3-ASR-{model_short}", file=sys.stderr)

    print(f"   ⚙️  设备: {device_label(device_map)}", file=sys.stderr)
    print(f"   📦 模型: {model_name}", file=sys.stderr)

    if local_model:
        print(f"   ⏳ 加载本地模型中...", file=sys.stderr)
    else:
        print(f"   ⏳ 加载模型中（首次需下载权重 {model_short} ~{2 if model_short == '0.6B' else 5}GB）...", file=sys.stderr)

    try:
        from qwen_asr import Qwen3ASRModel

        # 构建 from_pretrained 参数
        pretrained_kwargs = {
            "device_map": device_map,
            "torch_dtype": "auto",
        }
        if cache_dir and not local_model:
            pretrained_kwargs["cache_dir"] = cache_dir

        model = Qwen3ASRModel.from_pretrained(
            model_name,
            **pretrained_kwargs,
        )

        print(f"   ✅ 模型加载完成", file=sys.stderr)
        print(f"   🎤 正在转录...", file=sys.stderr)

        results = model.transcribe(args.audio)
        transcript = results[0].text if results else ""

        if not transcript or transcript.strip() == "":
            print("错误: 转录结果为空", file=sys.stderr)
            sys.exit(1)

        transcript = transcript.strip()

        # === 构造来源描述 ===
        if local_model:
            source = f"本地模型（{model_short}）"
        else:
            source = f"Qwen3-ASR-{model_short}"
        lbl = device_label(device_map)
        if lbl != "CPU":
            source += f"（{lbl}加速）"

        # === 写入输出文件 ===
        # 第一行: 来源字符串
        # 第二行起: 转录文本
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(source + "\n")
            f.write(transcript + "\n")

        print(f"   ✅ 转录完成", file=sys.stderr)
        print(f"   📄 来源: {source}", file=sys.stderr)

    except ImportError:
        print("错误: 请先在 conda 环境中安装 qwen-asr: pip install qwen-asr", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: 转录失败 - {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
