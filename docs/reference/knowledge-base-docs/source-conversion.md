# 源文件转 Markdown

## 目标

把 `source/` 下的非 Markdown 文件转换为 Obsidian 可读的 Markdown 草稿，作为后续 Qwen 深度整理的输入。

第一阶段支持：

- PDF：使用 Python 开源库 `pypdf` 抽取文本。
- PDF + Qwen：可选调用本地 `qwen3.6-35b-a3b-nvfp4` 对抽取文本做 Markdown 和公式整理。
- LM Studio conversation JSON：解析对话标题、角色、模型和正文。
- `.docx`：使用 `zipfile` 和 WordprocessingML XML 抽取标题、正文、列表、表格、超链接和图片资产。
- 网页 URL：使用开源库 `lxml` 抽取正文并转换为 Markdown。
- 微信公众号文章：优先抽取 `#js_content`，保留正文结构、链接，并默认下载图片资产做离线归档。

后续可扩展：

- 图片/PPT/表格：按内容类型单独设计。

## 命令约定

```bash
PY="${CODEX_PYTHON_BIN:-python3}"
MONTH="$(date +%Y-%m)"
```

## 目录规则

- `source/AI_paper/` 转换草稿到项目根目录 `_drafts/AI_paper/$MONTH/`，正式论文笔记再写入 `notes/AI/AI_paper/$MONTH/`。
- `source/AI-Chat/` 转换到 `notes/AI/AI-Chat/$MONTH/`。
- 微信公众号和其他网页示例转换到 `notes/Net/WeChat/$MONTH/`。
- 其他来源后续按主题新建 `notes/<Domain>/<Source>/$MONTH/`，保持月份目录为 `YYYY-MM`。

## 样稿命令

```bash
$PY scripts/convert_sources_to_md.py --sample --output-dir "notes/_samples/source-conversion"
```

如果希望让本地 Qwen 参与 PDF 整理：

```bash
$PY scripts/convert_sources_to_md.py --source "source/AI_paper/Attention Is All You Need.pdf" --output-dir "_drafts/AI_paper/$MONTH" --qwen-polish-pdf --overwrite
```

如需处理整篇 PDF：

```bash
$PY scripts/convert_sources_to_md.py --source "source/AI_paper/Attention Is All You Need.pdf" --output-dir "_drafts/AI_paper/$MONTH" --all-pages --qwen-polish-pdf --overwrite
```

转换网页或微信公众号文章：

```bash
$PY scripts/convert_sources_to_md.py --url "https://mp.weixin.qq.com/s/IED0AJ7p6LJoETNP7PlVAQ" --output-dir "notes/Net/WeChat/$MONTH"
```

网页转换默认会下载图片资产到输出目录下的 `assets/<笔记文件名>/` 并改写为相对链接。重复运行会按 `source_url` 和正文 hash 跳过；网页更新、需要重抓或补下载资产时增加 `--overwrite`。

如果只想保留远程图片链接：

```bash
$PY scripts/convert_sources_to_md.py --url "https://mp.weixin.qq.com/s/IED0AJ7p6LJoETNP7PlVAQ" --output-dir "notes/Net/WeChat/$MONTH" --no-download-assets
```

默认输出：

```text
notes/_samples/source-conversion/
```

默认索引：

```text
indexes/source-manifest.json
```

## Qwen 正式整理

源文件转换草稿生成后，可调用本地 Qwen 输出正式笔记：

```bash
$PY scripts/qwen_organize_notes.py --from-manifest --limit 1
```

指定某篇草稿：

```bash
$PY scripts/qwen_organize_notes.py --source "notes/_samples/source-conversion/PDF-Attention-Is-All-You-Need.md"
```

默认输出：

```text
notes/_organized/
```

整理成功后会回写 `indexes/source-manifest.json`：

- `organized_output_path`
- `organized_status`
- `organized_at`
- `organize_model`
- `organize_error`

## 输出定位

转换草稿只负责“可读、可追溯、可再加工”，不要求一步变成最终知识笔记。

PDF 草稿会包含：

- YAML frontmatter。
- 来源路径、hash、页数。
- 转换说明。
- 可选的 Qwen 整理草稿。
- 待整理区。
- 按页抽取的原文文本。

conversation JSON 草稿会包含：

- YAML frontmatter。
- 对话标题、创建时间、最后模型。
- 对话消息列表。
- 待整理区。

DOCX 草稿会包含：

- YAML frontmatter。
- 源文件路径、作者、创建/修改时间、图片资产数量。
- 标题、段落、列表、表格和图片相对链接。
- 待整理区。

网页草稿会包含：

- YAML frontmatter。
- 原始 URL、最终 URL、作者/账号、发布时间和页面描述。
- 正文抽取结果，包含 Markdown 链接和本地图片相对链接。
- 图片资产记录，包含下载数量、失败数量和资产目录。
- 待整理区。

## 注意事项

- PDF 文字抽取质量取决于 PDF 本身。扫描版 PDF 需要 OCR，当前第一阶段不处理 OCR。
- Qwen 可以修复 Markdown 层级、断行、部分公式和表格描述，但不能可靠恢复抽取阶段已经丢失的公式细节。
- 论文 PDF 的公式、表格、双栏排版需要以原 PDF 为准；Qwen 输出中标注 `[公式待核验]`、`[表格待核验]` 的地方应人工回看。
- conversation JSON 中可能包含私密内容，正式导入前需要确认是否适合进入知识库。
- 微信公众号和部分网页可能有访问控制、反爬或动态渲染；当前优先支持可直接抓取的文章页。若网页需要登录态或浏览器渲染，后续可接入 Chrome 抓取。
- 当前默认只下载 Markdown 图片链接指向的远程资产；脚本不会执行网页脚本，也不会下载 CSS、字体、视频等页面资源。
