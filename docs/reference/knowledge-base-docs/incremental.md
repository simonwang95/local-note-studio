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

## Profile 只读检索索引

`scripts/build_note_index.py` 仍兼容原有 `--notes-dir/--index-dir` 用法；Agent 检索使用 Profile 级 Schema 2.0：

```bash
scripts/local-notes-agent rebuild-index --profile qingfeng
# 等价的底层入口：
python3 worker/scripts/build_note_index.py --profile qingfeng
```

默认文件：

```text
indexes/note-indexes/<profile_id>/note-index.json
indexes/note-indexes/<profile_id>/asset-index.json
```

索引保留旧字段并增加稳定 `note_id`、Profile/真实 Markdown 路径、来源 URL/ID/hash、作者和发布时间、整理模型/状态、章节 provenance、缺失元数据与质量信息。BVID、动态 ID、模型和发布时间按确定性回退；缺失值为 `null`，不会调用模型补齐。mtime 只用于 `modified_at` 和过期指纹，不冒充可信发布时间。

每次重建扫描的只是 Profile `output_dir` 内且仍通过 `allowed_output_roots` 校验的 Markdown。符号链接逃逸和 `.obsidian` 内容不进入索引。稳定来源身份重复时会幂等去重并优先保留完整正式笔记；PPTX/PDF/网页等没有 BVID 或动态 ID 的资料使用 `source_path/source_hash` 身份。

写入采用临时文件、`fsync` 和 `os.replace`。查询只校验 Schema 与路径/大小/mtime 集合指纹，不写任何文件，也不会自动修复：

- 索引缺失：`NOTE_INDEX_UNAVAILABLE`；
- JSON/Schema 损坏：`NOTE_INDEX_CORRUPT`；
- Markdown 集合变化：`NOTE_INDEX_STALE`。

三种情况均通过显式 `rebuild-index` 恢复。采集/整理成功后会刷新所属 Profile；刷新失败仅产生 warning，不回滚或破坏已完成笔记。

索引状态与原始资料状态不同：只有已生成的 Markdown 可检索；已发现但未整理、整理失败、以及尚未纳入 Profile 的原始资料都不在检索结果中。

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
