# 处理进度

## 2026-06-10

- 已确认当前 `notes/` 为示例笔记目录，正式 Obsidian 目录待定。
- 已确认路径和模型通过 `env.local` 配置。
- 已确认可以新增工程目录。
- 已确认沿用现有主题目录结构。
- 已确认新笔记增加推荐 frontmatter。
- 已确认默认主整理模型为 `qwen3.6-35b-a3b-nvfp4`。
- 已确认 B 站和视频转写从历史项目迁移到当前仓库维护。
- 已确认使用 manifest/index 做增量处理。
- 已建立第一版项目文档。
- 已新增 `scripts/convert_sources_to_md.py`，支持 PDF 和 LM Studio conversation JSON 转 Markdown 草稿。
- 已生成 4 篇源文件转换样稿到 `notes/_samples/source-conversion/`。
- 已生成 `indexes/source-manifest.json` 记录样稿转换状态。
- 已新增 `--qwen-polish-pdf`，可调用本地 `qwen3.6-35b-a3b-nvfp4` 对 PDF 抽取文本做 Markdown 和公式整理。
- 已新增 `scripts/qwen_organize_notes.py`，支持从转换草稿生成正式 Qwen 笔记并回写 manifest。
- 已新增 `scripts/build_note_index.py`，支持生成 `note-index.json` 和 `asset-index.json`。
- 已将 B 站转写核心脚本迁移到 `scripts/bilibili/`，`scripts/run_bilibili_transcript.py` 默认调用本仓库脚本。
- 已为 `scripts/run_bilibili_transcript.py` 增加 `--limit`，支持收藏夹小批量增量处理，并已支持 `--url` 指定单视频链接。
- 已参考历史项目 `env.local` 补齐 B 站后处理参数：`SUMMARY_MAX_TOKENS=80000`、`LLM_TIMEOUT=1800`、`COOLDOWN_DELAY=60`。
- 已真实跑通收藏夹限量 1 条流程，输出到 `notes/Net/BiliBili/2026-06/`，并完成 Whisper 转写、Qwen 摘要、思维导图和 AI 校对。
- 已真实跑通指定视频链接流程，`--url` 会在转写后自动执行 `summary-only` 后处理。
- 已新增网页/微信公众号文章转换入口：`scripts/convert_sources_to_md.py --url URL`，使用 `lxml` 抽取正文并写入 `source-manifest.json`。
- 已用微信公众号样例 `https://mp.weixin.qq.com/s/IED0AJ7p6LJoETNP7PlVAQ` 跑通转换，并验证正文 hash 增量跳过。
- 已新增网页图片离线归档：默认下载 Markdown 图片到 `assets/<笔记文件名>/`，可用 `--no-download-assets` 保留远程链接。
- 已将 B 站和本地视频输出接入统一 YAML frontmatter，并新增 `indexes/video-manifest.json`。
- 已新增 `.docx` 转 Markdown 草稿，支持标题、段落、列表、表格、超链接和图片资产，并用临时 Word 文件验证通过。
- 已新增 `scripts/normalize_note_frontmatter.py`，并对当前 `notes/` 下 393 篇 Markdown 笔记补齐/对齐 YAML frontmatter；再次 dry-run 已验证 0 个变更。
- 已将自动落盘目录规则调整为月份目录：B 站写入 `notes/Net/BiliBili/YYYY-MM/`，微信公众号写入 `notes/Net/WeChat/YYYY-MM/`，AI Chat 写入 `notes/AI/AI-Chat/YYYY-MM/`，AI paper 草稿写入 `_drafts/AI_paper/YYYY-MM/`，正式论文笔记写入 `notes/AI/AI_paper/YYYY-MM/`。
- 已将 `source/` 下 9 篇 AI paper PDF 和 13 个 LM Studio conversation JSON 全量转换为 Markdown；AI paper 草稿已迁出 Obsidian 笔记目录到 `_drafts/AI_paper/2026-06/`，正式论文笔记位于 `notes/AI/AI_paper/2026-06/`，AI Chat 位于 `notes/AI/AI-Chat/2026-06/`；`indexes/source-manifest.json` 已同步新路径。
- 已修正 B 站收藏夹去重范围：默认扫描整个 `NOTES_DIR=notes`，从文件名、frontmatter 和正文前部识别 BV 号，避免迁移到 `notes/Net/BiliBili/` 后重复处理历史笔记。

## 待办

- 继续用更长真实批次观察 Qwen 后处理耗时和输出稳定性。
- 增加网页 HTML 文件、PPT/表格等更多源文件转换器。
- 根据正式 Obsidian Vault 位置调整 `env.local`。
