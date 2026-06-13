# 增量处理与索引

## 目标

通过 manifest/index 让知识库整理流程可以反复运行，并且只处理新增或变更内容。

## 源文件 manifest

默认文件：

```text
indexes/source-manifest.json
```

建议字段：

| 字段 | 说明 |
| --- | --- |
| `source_path` | 源文件路径 |
| `source_type` | 文件类型 |
| `source_hash` | sha256 hash |
| `source_size` | 文件大小 |
| `output_path` | 输出 Markdown 路径 |
| `status` | `converted`、`organized`、`failed`、`skipped` |
| `converted_at` | 转换时间 |
| `model` | 整理模型 |
| `error` | 失败信息 |
| `organized_output_path` | Qwen 正式整理输出路径 |
| `organized_status` | `organized` 或 `failed` |
| `organized_at` | 正式整理时间 |
| `organize_model` | 正式整理使用的模型 |
| `organize_error` | 正式整理失败信息 |

## 跳过规则

- 源文件存在于 manifest。
- hash 未变化。
- 输出 Markdown 存在。
- 未指定 `--overwrite`。

满足以上条件时默认跳过。

## 变更规则

当源文件 hash 变化时：

- 新输出可以覆盖草稿样稿。
- 正式整理笔记默认不覆盖，建议生成新版本或标记待审。
- manifest 更新 hash 和状态。

## 索引扩展

后续可以增加：

- `indexes/note-index.json`：记录每篇笔记的标题、标签、来源、更新时间。
- `indexes/asset-index.json`：记录图片、截图、附件引用和缺失状态。

当前已提供 `scripts/build_note_index.py` 生成 `note-index.json` 和 `asset-index.json`。

## 视频 manifest

默认文件：

```text
indexes/video-manifest.json
```

当前由 `scripts/run_bilibili_transcript.py` 在转写和 AI 后处理完成后写入。主要字段：

| 字段 | 说明 |
| --- | --- |
| `source_url` | B 站视频链接 |
| `source_path` | 本地视频路径，B 站视频为空 |
| `source_type` | `bilibili`、`local-video` 或 `video` |
| `source_hash` | 输出 Markdown 正文 hash |
| `output_path` | 生成的 Markdown 路径 |
| `title` | 视频标题 |
| `bvid` / `avid` | B 站视频 ID |
| `author` | UP 主或本地来源 |
| `published` | 发布时间 |
| `duration` | 视频时长 |
| `transcript_source` | 字幕或 ASR 来源 |
| `transcribed_at` | 转写时间 |
| `status` | `converted` 或 `failed` |
