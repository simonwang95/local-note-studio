# PDF 处理路线

本项目保留两条 PDF 路线，按目标选择。

## 速读 PDF

目标：快速理解、筛选论文、获得中文速读材料。

适用场景：

- 想先判断论文是否值得精读。
- 需要中文翻译式速读、摘要和思维导图。
- 本地模型已开启足够上下文，例如 128k。
- 自动脚本可用文本抽取结果一次性速读；LM Studio 内可直接读取 PDF 时，也可用提示词模式手动直读原始 PDF。

推荐输出：

- `## 中文速读`
- `## 一句话概括`
- `## 速读摘要`
- `## 思维导图`
- `## 值得精读的理由`
- `## 待核验`
- `## 全文翻译`

自动速读命令：

```bash
PY="${CODEX_PYTHON_BIN:-python3}"
MONTH="2026-05"

QWEN_QUICKREAD_COOLDOWN_DELAY=60 $PY scripts/quick_read_pdf.py --source "source/AI_paper/example.pdf" --output-dir "notes/AI/_quickread/AI_paper/$MONTH" --overwrite
```

批量速读：

```bash
QWEN_QUICKREAD_COOLDOWN_DELAY=60 $PY scripts/quick_read_pdf.py --all --source-dir "source/AI_paper" --output-dir "notes/AI/_quickread/AI_paper/$MONTH"
```

LM Studio 直读 PDF 提示词文件：

```bash
$PY scripts/quick_read_pdf.py --source "source/AI_paper/example.pdf" --output-dir "notes/AI/_quickread/AI_paper/$MONTH" --prompt-only --overwrite
```

提示词参考：

```text
请直接阅读这篇 PDF，输出中文速读笔记。

要求：
1. 先输出速读材料，保留论文核心问题、方法、实验、结论和局限。
2. 生成 Markdown 格式，包含：中文速读、一句话概括、速读摘要、思维导图、值得精读的理由、待核验、全文翻译。
3. 思维导图使用 Mermaid mindmap 代码块。
4. 全文翻译必须放在文件末尾，按原文顺序保留可读中文翻译。
5. 公式、表格或实验数据不确定时标注「待核验」，不要猜。
6. 不输出你的思考过程。
```

自动速读输出到 `notes/AI/_quickread/AI_paper/$MONTH/`，并写入 `indexes/quickread-manifest.json`。这是临时筛选材料，不替代正式知识笔记。

## 精读归档

目标：生成可追溯、可核验、适合长期保存的正式 Obsidian 笔记。

当前脚本路线：

```bash
PY="${CODEX_PYTHON_BIN:-python3}"
MONTH="2026-05"

QWEN_PDF_POLISH_COOLDOWN_DELAY=60 $PY scripts/convert_sources_to_md.py --source "source/AI_paper/example.pdf" --output-dir "_drafts/AI_paper/$MONTH" --all-pages --qwen-polish-pdf --overwrite

QWEN_ORGANIZE_MAX_CHARS=12000 QWEN_ORGANIZE_OVERLAP_CHARS=800 QWEN_ORGANIZE_SYNTHESIS_MAX_CHARS=16000 QWEN_ORGANIZE_TIMEOUT_SECONDS=600 QWEN_ORGANIZE_COOLDOWN_DELAY=60 $PY scripts/qwen_organize_notes.py --source "_drafts/AI_paper/$MONTH/PDF-example.md" --output-dir "notes/AI/AI_paper/$MONTH" --overwrite
```

精读归档特性：

- PDF Qwen polish 按页分块，使用 `QWEN_PDF_POLISH_OVERLAP_PAGES` 保留跨页上下文。
- 正式整理按字符安全分块，使用 `QWEN_ORGANIZE_OVERLAP_CHARS` 保留重叠上下文。
- 综合阶段过长时继续分批综合，不做静默截断。
- 正式笔记默认包含 `## 思维导图`。
- PDF 速读和精读笔记默认在文件末尾保留 `## 全文翻译`；这会显著增加耗时和输出长度。
- 遇到 408/429/5xx 等临时 LLM 错误时，按 `QWEN_ORGANIZE_MAX_RETRIES` 和 `QWEN_ORGANIZE_RETRY_DELAY` 重试。

## 选择建议

- 先用“速读 PDF”判断价值。
- 值得长期保存的论文，再走“精读归档”。
- 速读结果可以作为临时材料；正式知识库以精读归档输出为准。
