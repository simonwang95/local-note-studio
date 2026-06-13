# 笔记规范

## 文件与目录

- 沿用现有主题目录：`AI`、`Finance`、`Media`、`Work` 等。
- 新增专题时优先放在已有大类下；确实不适合时再新建一级目录。
- 文件名使用清晰中文标题，可保留编号、作者、日期、BV 号等信息。
- 附件放在同级或上级 `assets/` 子目录，Markdown 中使用相对路径。

## Frontmatter

新笔记推荐包含 YAML frontmatter：

```yaml
---
title: 标题
type: source-conversion
source_type: pdf
source_path: source/AI_paper/example.pdf
source_url:
created: 2026-06-10
updated: 2026-06-10
status: draft
model: qwen3.6-35b-a3b-nvfp4
tags:
  - source/pdf
  - domain/ai
source_hash: sha256...
---
```

常用字段：

| 字段 | 说明 |
| --- | --- |
| `title` | 笔记标题 |
| `type` | 笔记类型，例如 `video-note`、`paper-note`、`chat-note`、`source-conversion` |
| `source_type` | 原始材料类型，例如 `pdf`、`docx`、`lmstudio-conversation`、`webpage`、`wechat-article`、`video`、`bilibili` |
| `source_path` | 本地源文件路径 |
| `source_url` | 网络来源链接，没有则留空 |
| `created` | 笔记创建日期 |
| `updated` | 最近更新日期 |
| `status` | `draft`、`organized`、`reviewed`、`archived` |
| `model` | 主要整理模型 |
| `tags` | Obsidian 标签 |
| `source_hash` | 源文件 hash，用于增量处理 |

## 推荐正文结构

源文件转换草稿：

1. 标题
2. 来源信息
3. 转换说明
4. 待整理区
5. 原文抽取或对话正文

正式整理笔记：

1. 一句话概括
2. 核心观点
3. 结构化正文
4. 关键概念
5. 可复习清单
6. 相关链接
7. 风险提示或适用边界

视频课程笔记：

1. 视频信息
2. 速读摘要
3. 详细讲义
4. 图文截图
5. 术语表
6. 复习清单
7. 完整转写或折叠原文

## 兼容性要求

- 优先使用标准 Markdown，不依赖 Obsidian 专有语法。
- 可以使用 `[[双链]]`，但关键链接也应尽量保留普通 Markdown 路径或标题。
- 代码块必须带语言标识。
- 表格尽量保持简单，避免过宽。
- 不在笔记中写入本地敏感配置、cookie 或 API key。
