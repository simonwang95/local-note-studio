# 项目说明

## 目标

构建一个本地 Markdown 知识库，支持 Obsidian 使用、Agent 自动整理、本地 Qwen 模型深度加工和长期增量维护。

本项目的核心对象包括：

- 手工 Markdown 笔记。
- PDF、Word、JSON、网页导出等非 Markdown 源文件。
- 本地视频、字幕、音频文件。
- B 站单视频和 B 站收藏夹。

## 设计取向

- 本地优先：源文件、笔记、索引和配置都保存在本机。
- Markdown 优先：所有材料先落为 Markdown，便于检索、编辑和迁移。
- Obsidian 兼容：使用标准 Markdown、YAML frontmatter、相对链接和清晰目录。
- Agent 友好：项目说明、流程、进度和索引都放在可读文件里。
- 增量处理：通过 hash 和 manifest 识别新增、变更、已完成、失败任务。
- 本地模型可完成：默认主整理模型为 `qwen3.6-35b-a3b-nvfp4`。

## 当前默认目录

| 路径 | 说明 |
| --- | --- |
| `notes/` | 默认笔记输出目录，也是现有示例笔记目录 |
| `source/` | 原始非 Markdown 文件目录 |
| `docs/` | 项目文档目录 |
| `scripts/` | 自动化脚本目录 |
| `indexes/` | 增量 manifest 和索引目录 |
| `outputs/` | 导出物和批处理报告目录 |
| `cache/` | 可重建缓存目录 |

正式 Obsidian Vault 路径待定，后续通过 `env.local` 配置 `NOTES_DIR` 和 `OBSIDIAN_VAULT_DIR`。

## 已有素材状态

- `notes/` 中已有一批视频课程和专题笔记样例。
- `source/AI_paper/` 中已有多篇 AI 论文 PDF。
- `source/AI-Chat/LM-Studio/` 中已有 LM Studio conversation JSON。

这些素材用于验证转换、整理和索引流程，不代表最终分类已经固定。
