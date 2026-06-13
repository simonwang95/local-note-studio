# 快捷命令参考

这份文档用于手动复制和快速改参数。日期按当前手动批次写为 `2026-05`；后续只需要改 `MONTH`。

## 基础变量

```bash
PY="${CODEX_PYTHON_BIN:-python3}"
MONTH="2026-05"
```

## 手动处理流程

`source/` 文件、微信公众号和普通网页：先运行 `convert_sources_to_md.py` 转成 Markdown 草稿，再运行 `qwen_organize_notes.py` 正式整理。

B 站收藏夹、单个 B 站视频和本地视频：优先运行 `run_bilibili_transcript.py`，它已经封装了下载/转写/校对/frontmatter/manifest 后处理。

## B 站视频

```bash
# 扫描并处理收藏夹新增视频
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" BILIBILI_DEDUPE_DIRS="notes" $PY scripts/run_bilibili_transcript.py --favorite

# 小批量处理收藏夹新增视频
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" BILIBILI_DEDUPE_DIRS="notes" $PY scripts/run_bilibili_transcript.py --favorite --limit 1

# 处理单个 B 站视频
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" BILIBILI_DEDUPE_DIRS="notes" $PY scripts/run_bilibili_transcript.py --url "https://www.bilibili.com/video/BVxxxx/"

# 跳过 video-manifest.json 写入，但仍补齐 frontmatter
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" BILIBILI_DEDUPE_DIRS="notes" $PY scripts/run_bilibili_transcript.py --favorite --no-video-manifest
```

## 本地视频

```bash
# 单个本地视频或音频
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --local-file "/path/to/video.mp4"

# 文件夹
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --local-dir "/path/to/videos"

# 递归文件夹
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --local-dir "/path/to/videos" --recursive
```

## 微信公众号和网页

```bash
$PY scripts/convert_sources_to_md.py --url "https://mp.weixin.qq.com/s/IED0AJ7p6LJoETNP7PlVAQ" --output-dir "notes/Net/WeChat/$MONTH" --overwrite

# 不下载图片资产，仅保留远程链接
$PY scripts/convert_sources_to_md.py --url "https://mp.weixin.qq.com/s/IED0AJ7p6LJoETNP7PlVAQ" --output-dir "notes/Net/WeChat/$MONTH" --no-download-assets --overwrite
```

## PDF 论文速读

```bash
# 自动速读单篇 PDF：一次性抽取文本并交给 Qwen 生成速读、思维导图和末尾全文翻译
QWEN_QUICKREAD_COOLDOWN_DELAY=60 $PY scripts/quick_read_pdf.py --source "source/AI_paper/example.pdf" --output-dir "notes/AI/_quickread/AI_paper/$MONTH" --overwrite

# 批量速读 AI_paper 下所有 PDF
QWEN_QUICKREAD_COOLDOWN_DELAY=60 $PY scripts/quick_read_pdf.py --all --source-dir "source/AI_paper" --output-dir "notes/AI/_quickread/AI_paper/$MONTH"

# 生成 LM Studio 手动直读原始 PDF 的提示词文件
$PY scripts/quick_read_pdf.py --source "source/AI_paper/example.pdf" --output-dir "notes/AI/_quickread/AI_paper/$MONTH" --prompt-only --overwrite
```

## Source 转 Markdown 草稿

PDF 有两条路线：速读 PDF 先看 `docs/pdf-routes.md`；下面命令是精读归档路线。精读时，`PDF-*` 转换草稿写入项目根目录 `_drafts/AI_paper`，正式 `PAPER-*` 笔记写入 `notes/AI/AI_paper`。

```bash
# 单篇 AI paper PDF，启用 Qwen 辅助整理抽取文本
QWEN_PDF_POLISH_COOLDOWN_DELAY=60 $PY scripts/convert_sources_to_md.py --source "source/AI_paper/example.pdf" --output-dir "_drafts/AI_paper/$MONTH" --qwen-polish-pdf --overwrite

# 单篇 AI paper PDF，全页转换
QWEN_PDF_POLISH_COOLDOWN_DELAY=60 $PY scripts/convert_sources_to_md.py --source "source/AI_paper/example.pdf" --output-dir "_drafts/AI_paper/$MONTH" --all-pages --qwen-polish-pdf --overwrite

# 单个 LM Studio conversation JSON
$PY scripts/convert_sources_to_md.py --source "source/AI-Chat/LM-Studio/example.conversation.json" --output-dir "notes/AI/AI-Chat/$MONTH" --overwrite

# 样稿模式
$PY scripts/convert_sources_to_md.py --sample --output-dir "notes/_samples/source-conversion" --overwrite
```

## Qwen 正式整理

```bash
# 整理论文草稿，使用较保守的分块；PDF 正式笔记末尾会保留全文翻译
QWEN_ORGANIZE_MAX_CHARS=12000 QWEN_ORGANIZE_OVERLAP_CHARS=800 QWEN_ORGANIZE_SYNTHESIS_MAX_CHARS=16000 QWEN_ORGANIZE_TIMEOUT_SECONDS=600 QWEN_ORGANIZE_COOLDOWN_DELAY=60 $PY scripts/qwen_organize_notes.py --source "_drafts/AI_paper/$MONTH/PDF-example.md" --output-dir "notes/AI/AI_paper/$MONTH" --overwrite

# 从 source-manifest.json 批量整理全部草稿时，输出目录要按来源分开设置，避免聊天/网页误写入论文目录
QWEN_ORGANIZE_MAX_CHARS=12000 QWEN_ORGANIZE_OVERLAP_CHARS=800 QWEN_ORGANIZE_SYNTHESIS_MAX_CHARS=16000 QWEN_ORGANIZE_TIMEOUT_SECONDS=600 QWEN_ORGANIZE_COOLDOWN_DELAY=60 $PY scripts/qwen_organize_notes.py --from-manifest --limit 1 --output-dir "notes/AI/_organized/$MONTH"
```

## 索引和格式

```bash
# 补齐或重排 YAML frontmatter
$PY scripts/normalize_note_frontmatter.py

# 先看会改哪些笔记
$PY scripts/normalize_note_frontmatter.py --dry-run

# 生成笔记和资产索引
$PY scripts/build_note_index.py
```

## 质量检查

```bash
# 检查 B 站待处理占位符
rg -n "AI待处理|请设置 SUMMARY_API_KEY" notes/Net/BiliBili/$MONTH -g "*.md"

# 检查 source 正式整理失败项
$PY - <<'PY'
import json
from pathlib import Path
m=json.loads(Path("indexes/source-manifest.json").read_text(encoding="utf-8"))
for item in m.get("items", []):
    if item.get("organized_status") == "failed":
        print(item.get("output_path"), "=>", item.get("organize_error"))
PY

# 检查 JSON manifest 是否有效
$PY -m json.tool indexes/source-manifest.json >/dev/null
$PY -m json.tool indexes/video-manifest.json >/dev/null
```
