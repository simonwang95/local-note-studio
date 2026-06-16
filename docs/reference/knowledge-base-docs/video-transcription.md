# 视频与 B 站转写

## 默认策略

视频和 B 站转写已从历史项目迁移到当前仓库：

```text
scripts/bilibili/
```

该项目已有能力：

- B 站单视频转写。
- B 站收藏夹批量扫描。
- 本地视频目录转写。
- 字幕优先，ASR 兜底。
- Qwen3-ASR 或 Whisper MLX。
- OpenAI-compatible LLM 后处理。
- 断点续传。

## 本项目适配目标

1. 输出路径改为由 `env.local` 配置。
2. 模型默认改为 `qwen3.6-35b-a3b-nvfp4`。
3. 生成的 Markdown 增加统一 frontmatter。
4. manifest 记录视频 URL、BV 号、avid、源字幕、ASR 引擎、输出笔记。
5. 对收藏夹按增量方式处理，避免重复下载和重复整理。

## 包装脚本

命令约定：

```bash
PY="${CODEX_PYTHON_BIN:-python3}"
MONTH="$(date +%Y-%m)"
```

B 站收藏夹默认输出根目录为 `notes/Net/BiliBili`，脚本会自动写入当月子目录 `notes/Net/BiliBili/$MONTH/`。
收藏夹去重默认扫描 `BILIBILI_DEDUPE_DIRS=notes`，会从文件名、frontmatter 和正文前部提取 BV 号，避免重复处理已经分散在其他主题目录里的 B 站笔记。
视频后处理默认写入 `indexes/video-manifest.json`；临时不想更新 manifest 时，在命令末尾加 `--no-video-manifest`。

本项目提供轻量包装入口：

```bash
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --favorite
```

跳过 `video-manifest.json` 写入，但仍补齐笔记 frontmatter：

```bash
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --favorite --no-video-manifest
```

限量处理收藏夹新增视频：

```bash
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --favorite --limit 1
```

单视频：

```bash
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --url "https://www.bilibili.com/video/BVxxxx"
```

单视频入口会先生成转写 Markdown，再对生成文件执行 `summary-only`，补齐摘要、思维导图和 AI 校对。
后处理完成后，包装脚本会补齐统一 YAML frontmatter，并写入 `indexes/video-manifest.json`。

本地目录：

```bash
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --local-dir "/path/to/videos" --recursive
```

注意：

- 迁移后默认读取当前项目 `env.local`。
- `BILIBILI_DEDUPE_DIRS` 默认是 `notes`，不要只用输出目录做去重，否则迁移目录后会重复处理历史笔记。
- B 站单视频默认先用 `yt-dlp` 获取当前视频可确认的 CC 字幕和 AI 字幕，再回落到 ASR。
- 网页播放器字幕接口可用 `BILIBILI_PREFER_WEB_SUBTITLE=true` 临时开启；该接口偶尔会返回与当前 BV 不匹配的字幕，日常不建议优先使用。
- 桌面应用的“字幕/转录优先级”下拉框会覆盖上述环境变量：`yt-dlp` 字幕优先、网页播放器字幕优先、ASR 语音转写优先。
- `VIDEO_MANIFEST_ENABLED=true` 时默认写入 `indexes/video-manifest.json`；命令行 `--no-video-manifest` 优先级更高。
- Qwen reasoning 模型建议沿用旧项目参数：`SUMMARY_MAX_TOKENS=80000`、`LLM_TIMEOUT=1800`。
- 长视频的摘要、思维导图、AI 校对会按 `SUMMARY_CHUNK_CHARS` 分块，并使用 `SUMMARY_CHUNK_OVERLAP_CHARS` 保留重叠上下文；分块之间按 `SUMMARY_CHUNK_COOLDOWN_DELAY` 冷却，避免只处理前 20000 字。
- `--dry-run` 只打印命令，不执行。
- 无 `--limit` 的收藏夹模式会运行本仓库完整批处理；建议日常增量先使用 `--limit 1` 或小批量验证。

## 输出元数据

B 站和本地视频 Markdown 会统一包含 YAML frontmatter：

```yaml
type: video-note
source_type: bilibili
source_url: https://www.bilibili.com/video/BV...
bvid: BV...
author: UP主
published: 2026-06-04
duration: 10分36秒
transcript_source: Whisper-whisper-large-v3-turbo（MLX加速）
status: draft
tags:
  - video
  - source/bilibili
```

对应 manifest 位于：

```text
indexes/video-manifest.json
```

## 推荐降级链路

```text
B 站 CC 字幕
  -> B 站 AI 字幕
  -> 本地同名 SRT
  -> 本地 ASR
  -> 转写失败记录到 manifest
```

## 输出类型

- 一句话概括：用一句话快速判断内容价值。
- 速读摘要：提取核心事实、论证链条和结论。
- 思维导图：保留原有 Markdown 缩进列表形式。
- 结构化正文：主推笔记正文，按内容自然组织，不预设为股票课程。
- 图文笔记：可选抽取关键帧，插入 `## 关键帧图文笔记`。
- 校对正文：放在结构化正文和复习内容之后，保留一份可读性更好的转录文本。
- 原始字幕：放在最后；默认保留，可通过 `KEEP_ORIGINAL_SUBTITLES=false` 或桌面应用“保留原始字幕”开关关闭。

## 风险边界

涉及投资、政策、医学、法律等内容时，输出只作为学习笔记和观点整理，不构成直接建议。
