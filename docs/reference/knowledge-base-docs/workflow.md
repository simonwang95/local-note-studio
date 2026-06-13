# 工作流总览

## 总流程

```text
源文件/视频/B站收藏夹
  -> 抽取或转写
  -> Markdown 草稿
  -> qwen3.6-35b-a3b-nvfp4 结构化整理
  -> 人工或 Agent 校验
  -> 更新 manifest/index
  -> Obsidian 中复习和链接
```

## 命令约定

常用命令可直接看 `docs/quick-commands.md`；下面保留工作流内的核心示例。

长文本整理默认采用安全分块：PDF Qwen polish 按页分块并带前序页 overlap；正式整理按字符分块并带 overlap；B 站摘要、思维导图、AI 校对也按转录文本分块并综合去重，避免长视频或长文只处理前半段。

PDF 保留两条路线：快速理解时用 `docs/pdf-routes.md` 里的“速读 PDF”；需要长期保存时用当前“精读归档”流程。两条路线的 PDF 笔记末尾都默认保留 `## 全文翻译`。

文档中的 Python 命令统一使用 `PY` 变量：

```bash
PY="${CODEX_PYTHON_BIN:-python3}"
MONTH="$(date +%Y-%m)"
```

## 源文件流程

1. 扫描 `SOURCE_DIR`。
2. 识别来源类型：PDF、conversation JSON、网页 URL、微信公众号文章、Word、网页导出等。
3. 用开源库转换为 Markdown 草稿。
4. 写入 `SAMPLE_OUTPUT_DIR` 或正式 `NOTES_DIR`。
5. 更新 `indexes/source-manifest.json`。
6. 需要深度整理时，再调用本地 Qwen 输出正式笔记。

常用命令：

```bash
$PY scripts/convert_sources_to_md.py --sample
$PY scripts/convert_sources_to_md.py --url "https://mp.weixin.qq.com/s/IED0AJ7p6LJoETNP7PlVAQ" --output-dir "notes/Net/WeChat/$MONTH"
$PY scripts/convert_sources_to_md.py --source "source/AI_paper/example.pdf" --output-dir "_drafts/AI_paper/$MONTH" --qwen-polish-pdf
$PY scripts/convert_sources_to_md.py --source "source/AI-Chat/LM-Studio/example.conversation.json" --output-dir "notes/AI/AI-Chat/$MONTH"
$PY scripts/qwen_organize_notes.py --from-manifest --limit 1
```

目录规则：

- `source/AI_paper/` 草稿写入项目根目录 `_drafts/AI_paper/$MONTH/`；正式论文笔记写入 `notes/AI/AI_paper/$MONTH/`。
- `source/AI-Chat/` 写入 `notes/AI/AI-Chat/$MONTH/`。
- 微信公众号和网页样例写入 `notes/Net/WeChat/$MONTH/`。

## 长期目录策略

当前自动落盘使用 `YYYY-MM` 月份目录，便于增量处理和回溯批次。积累 1-2 年后，如果单个主题下月份目录过多，可以迁移成 `YYYY/YYYY-MM` 或按主题二级目录重排。为降低迁移对 Obsidian 关系网络的影响，提前遵守这些规则：

- 草稿、缓存和转写中间产物放在项目根目录 `_drafts/` 或 `cache/`，不放进 Obsidian 正式笔记目录。
- 正式笔记文件名保持稳定，避免频繁改标题；同名风险高的笔记用前缀区分，例如 `PAPER-`、`CHAT-`、`WECHAT-`。
- frontmatter 保留 `source_path`、`source_hash`、`draft_path`、`created`、`updated` 等稳定元数据，脚本和索引用这些字段定位，而不是只依赖目录层级。
- 正文链接优先使用 Obsidian wikilink，例如 `[[PAPER-DeepSeek_R1]]`；需要显示中文时用别名 `[[PAPER-DeepSeek_R1|DeepSeek R1]]`。
- 如果未来要移动正式笔记，优先在 Obsidian 内移动并开启自动更新内部链接；脚本批量移动时要同步 manifest 和 frontmatter 路径。

## 视频流程

1. 优先读取已有 `.srt` 或 B 站字幕。
2. 字幕缺失或质量差时，用本地 ASR 转写。
3. 生成转写 Markdown。
4. 调用 Qwen 生成摘要、思维导图、校对文本或详细讲义。
5. 需要图文笔记时，抽取封面和关键帧。
6. 补齐 YAML frontmatter，并更新 `indexes/video-manifest.json`。

常用命令：

```bash
# 转写单个本地视频或音频文件
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --local-file "/path/to/video.mp4"

# 转写本地文件夹中的视频或音频
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --local-dir "/path/to/videos"

# 递归转写本地文件夹及子文件夹
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --local-dir "/path/to/videos" --recursive

# 转写单个 B 站视频链接
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --url "https://www.bilibili.com/video/BVxxxx/"

# 只为已有 Markdown 补齐 Qwen 后处理
$PY scripts/run_bilibili_transcript.py --summary-only
```

B 站和本地视频输出根目录为 `notes/Net/BiliBili`，脚本会自动写入当月子目录 `notes/Net/BiliBili/$MONTH/`。
收藏夹去重默认扫描 `BILIBILI_DEDUPE_DIRS=notes`，会识别已经分散在其他主题目录中的 B 站笔记。

运行环境：

- 包装入口读取项目根目录 `env.local`，没有配置时使用 `env.example` 同款默认值。
- 当前转写运行的 conda 环境是 `CONDA_ENV=course-whisper`。
- 包装脚本会自动用 `conda run -n course-whisper ...` 调用迁移后的 B 站/本地视频脚本。
- 当前 ASR 配置为 `ASR_ENGINE=whisper`，本地模型路径由 `ASR_LOCAL_MODEL` 指定。
- Qwen 后处理使用 `SUMMARY_MODEL`/`DEFAULT_LLM_MODEL`，当前为 `qwen3.6-35b-a3b-nvfp4`。

## B 站收藏夹流程

1. 用收藏夹 ID 扫描视频列表。
2. 对比 manifest，跳过已处理且未变化的视频。
3. 按字幕优先、ASR 兜底的策略生成 Markdown。
4. 调用 Qwen 完成结构化整理。
5. 输出到主题目录，并更新索引。

本项目提供包装入口：

```bash
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --favorite
BILIBILI_OUTPUT_DIR="notes/Net/BiliBili" $PY scripts/run_bilibili_transcript.py --favorite --limit 1
```

迁移后的脚本位于 `scripts/bilibili/`，默认读取当前项目 `env.local`。

## 索引与资产检查

补齐或重排现有笔记的 YAML frontmatter：

```bash
$PY scripts/normalize_note_frontmatter.py
```

先检查会变更哪些文件：

```bash
$PY scripts/normalize_note_frontmatter.py --dry-run
```

生成笔记索引和图片资产报告：

```bash
$PY scripts/build_note_index.py
```

输出：

- `indexes/note-index.json`
- `indexes/asset-index.json`

## 增量原则

- 只处理新增或 hash 变化的源文件。
- 已整理笔记不直接覆盖，除非显式指定 `--overwrite`。
- 失败任务写入 manifest 的错误字段，便于重跑。
- 脚本输出尽量确定，方便 diff 和回溯。
