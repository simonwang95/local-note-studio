# 本地 Qwen 模型

## 默认模型

本项目默认主整理模型：

```text
qwen3.6-35b-a3b-nvfp4
```

默认通过 LM Studio 或兼容 OpenAI API 的本地服务调用：

```text
http://127.0.0.1:1234/v1
```

## 角色分工

源文件转换脚本负责：

- 读取源文件。
- 抽取文本。
- 生成 Markdown 草稿。
- 更新 manifest。

Qwen 整理流程负责：

- 总结核心观点。
- 建立结构化正文。
- 提炼术语和概念。
- 生成复习清单。
- 发现错别字、ASR 错误和术语错误。
- 对 PDF 抽取文本进行 Markdown 层级、断行、公式和表格线索整理。
- 按领域风格生成正式笔记。

## 正式整理脚本

命令约定：

```bash
PY="${CODEX_PYTHON_BIN:-python3}"
```

```bash
$PY scripts/qwen_organize_notes.py --from-manifest --limit 1
```

脚本会：

- 从 `indexes/source-manifest.json` 读取已转换草稿。
- 按 `QWEN_ORGANIZE_MAX_CHARS` 分块，并使用 `QWEN_ORGANIZE_OVERLAP_CHARS` 保留重叠上下文。
- 对长文先分块整理，再综合成正式笔记；综合阶段过长时继续分批综合，不做静默截断。
- 正式笔记默认生成 `## 思维导图`，适用于论文、微信公众号、普通网页、聊天记录等 source 草稿。
- PDF 论文的速读和精读产物默认在末尾保留 `## 全文翻译`；这会显著增加 token、耗时和文件大小。
- 批量整理时按 `QWEN_ORGANIZE_COOLDOWN_DELAY` 控制每篇之间的冷却间隔；未单独配置时继承 `COOLDOWN_DELAY`。
- 输出到 `ORGANIZED_OUTPUT_DIR`。
- 回写 manifest 的 `organized_*` 字段。

## 默认提示词方向

整理时应优先要求模型：

- 保留原意，不编造来源中没有的信息。
- 区分原文观点、模型推断和整理者补充。
- 对不确定内容标注“待核验”。
- PDF 公式或表格信息不完整时，明确标注 `[公式待核验]` 或 `[表格待核验]`，不猜测。
- 输出兼容 Obsidian 的 Markdown。
- 使用统一 frontmatter 和正文结构。
- 生成 Markdown 缩进列表格式的思维导图，并对 overlap 重叠内容去重。

## 失败处理

- 模型服务不可用时，保留 Markdown 草稿，不阻塞源文件转换。
- API 临时失败时可重试。
- 输出过长时按章节、页码或消息块分段整理。
- 整理失败写入 manifest，便于后续补跑。
