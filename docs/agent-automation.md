# Agent 自动化

Local Note Studio 0.1.22 提供受限 Agent CLI、本地 stdio MCP Server 和已有 Markdown 笔记的只读检索。OpenHanako 等 Agent 只负责选择命名 Profile、触发任务和查询状态；采集、转写、Qwen 整理、Manifest、恢复点和完整性检查仍由 `worker/local_note_studio_worker.py` 及现有脚本完成。

## 安全边界

- Agent 不能传入自由命令、Shell 参数、Python/Conda 路径、模型、Cookie 或 API Key。
- Profile 固定 UP UID、允许的内容类型、输出目录、输入根目录、URL 域名、运行时、批量上限、超时和冷却参数。
- 所有路径先展开并解析符号链接，再检查是否仍位于允许根目录；`..` 和符号链接逃逸都会被拒绝。
- URL 只允许 HTTP(S)、Profile 白名单域名和无内嵌凭据的地址；`token`、`api_key`、`authorization` 等敏感查询参数会被拒绝。
- Agent/MCP 默认通过 stdin 把请求交给 Worker。桌面 Tauri 桥也使用 stdin，完整请求和 API Key 不再出现在进程参数中。
- Cookie 内容、API Key、Token 和密码会从日志、结果及 SQLite 历史中脱敏。Profile 本身禁止保存秘密；Worker 仍从受保护的 `worker/env.local` 或 Application Support auth 目录读取。
- 写任务具有外部副作用，但按 Manifest 增量规则幂等，且默认不覆盖完整笔记。

## 配置 Profile

仓库只提交 [`automation-profiles.example.json`](../worker/automation-profiles.example.json)。真实配置默认位于：

```text
~/Library/Application Support/Local Note Studio/config/automation-profiles.json
```

开发或测试可用 `LOCAL_NOTE_STUDIO_PROFILES_FILE` 指向另一个受保护文件。仓库内的 `worker/automation-profiles.json` 已加入 `.gitignore`，但日常使用仍推荐 Application Support 路径。

复制示例后必须替换所有 `/Users/xxx/...` 占位符，并把确认无误的 Profile 设为 `"enabled": true`。Profile 会整体校验；任一字段错误都会在执行前拒绝整个配置，不会部分运行。

主要字段：

| 字段 | 约束 |
| --- | --- |
| `id` | 小写字母开头，只含小写字母、数字、`_`、`-` |
| `up_mid` | 纯数字 UID |
| `content_types` | `opus`、`video` 或两者 |
| `output_dir` | 绝对路径，必须位于 `allowed_output_roots` |
| `output_destinations` | 可选命名目标到 `output_dir` 内相对子目录的固定映射；键必须是安全 ID，值不得为绝对路径、`.`、`..` 或符号链接逃逸 |
| `allowed_input_roots` | `ingest-file` 可读取的绝对根目录 |
| `allowed_domains` | `ingest-url` 可访问的域名及其子域名 |
| `limit` | 单次处理上限；`0` 表示所有未完成内容 |
| `max_limit` | Agent 显式 `--limit` 的硬上限 |
| `overwrite_outputs` | 默认应保持 `false` |
| `keep_original_subtitles` | 默认 `false`；关闭时完整整理后的最终笔记不保留“原始字幕”章节，但仍保留整理正文、校对正文和来源元数据；若模型失败或仍有占位符，临时保留转写供安全重试 |
| `opus_image_analysis` | `off`、`ocr` 或 `vision`；旧 Profile 缺省为 `off` |
| `timeout_seconds` | 显式设置时同时覆盖单次图片、网页、PDF、Quick Read 和正式 Qwen 整理超时；不改变图片输出 token 上限 |
| `lock_timeout_seconds` | `0` 为锁冲突立即失败；正数为最长等待时间 |
| `execution_timeout_seconds` | 整个任务的硬超时，`0` 表示不设置 |

覆盖优先级固定为：Agent 显式的安全参数（来源、受上限约束的 `limit`、`dry_run`，以及 Profile 已声明的命名 `destination`）高于 Profile；Profile 高于普通 Worker 环境默认值；秘密始终只从 `env.local` 或 Application Support auth 读取。`destination` 只能引用 `output_destinations` 中的 ID，不能接收文件系统路径；Agent 无法显式覆盖运行时、模型、输出根、覆盖策略或秘密。

例如：

```json
"output_dir": "/Users/xxx/Files/Data/Notes/Finance/青枫浦上Q",
"output_destinations": {
  "daily_review": "日复盘"
}
```

此时 `destination=daily_review` 只会写入上述根目录下的 `日复盘`。未知 ID、绝对路径、父目录跳转及符号链接逃逸都会在 Worker 启动前被拒绝。未传 `destination` 时仍使用 Profile 根目录；索引仍按同一个父 Profile 递归刷新，避免子 Profile 重复收录。

`local_notes_ingest_file` 只接收白名单输入根内的一个普通文件，传入目录会被拒绝，避免自然语言误触发整目录批处理。目录批量导入只保留给 GUI 或明确的维护流程。

`opus_image_analysis` 只影响 B站图文附件，不复用也不改变现有 `enable_ocr` 的含义：

- `off`：仅下载并保留 Markdown 图片引用，不调用图片模型，成本最低；
- `ocr`：按原顺序提取直接可见文字，模糊字符标记待核验，不做扩展推断；
- `vision`：结合正文识别可核验文字、表格、图形、K线、排行榜或截图，并标注相关性、有限整理和不确定项。财经 Profile 推荐使用此模式。

分析只读取已经下载到本地的附件，不重复请求图片。单次 OCR/vision 请求显式发送 `max_tokens`，默认 `4096`、硬上限 `8192`；单次图片超时默认 `300` 秒。它们不继承 `SUMMARY_MAX_TOKENS=80000`、Quick Read 或长文/PDF 预算。Profile 的 `timeout_seconds` 显式设置时会覆盖图片与现有 Qwen 超时，但 token 上限保持独立。

成功缓存使用 Schema 2.0，并校验图片 SHA-256、模式、模型、规则版本以及正文/可信时间上下文哈希；新规则会自动使用新的缓存文件名，旧缓存保留但不命中。`finish_reason=length`、空 `content` 或非法 JSON 会在同一任务中自动重试一次，重试前仍执行统一模型冷却；单张图片最多两次调用，每次独立保持 `max_tokens=4096` 和 `timeout=300`，第二次失败后不会继续调用。HTTP 超时和临时服务错误仍转为可追溯的可重试失败，但不会在图片链路内无限重试。所有失败结果都不写成功缓存。失败时正文、已下载图片和原始引用仍保留，并写“图片分析失败/待重试”；已有完整笔记不会被失败重试覆盖。命中缓存、`off` 和未进入真实模型调用的项目不会触发冷却。单条动态最多分析 12 张图片，超过部分保留原图并写 warning。

B站正文送入正式 Qwen 整理前会剥离来源 URL、作者/MID、动态 ID、发布时间、转换路径和 SHA256，只保留原文抽取、图片分析与来源类型说明。frontmatter 和“来源追溯”由 Python 确定性生成。视觉提示词单独获得可信只读的动态发布时间、当前日期和 `Asia/Shanghai` 时区；图片中的日期只能作为直接可见文字原样记录，发布前一晚属于正常情况。只有 Python 明确产生 `time_warning` 时才能加入时间异常；模型不得依靠知识截止时间、历史印象或市场语境输出“应为2024年”“未来预设”“时间戳错误”或“年份疑似笔误”。正式整理阶段具有相同提示规则，并不得为来源中没有的机构、人名或术语添加英文名、缩写或别名；例如来源只有“沃什”时不能自行补为 `Waller`，无法确认时保留原文并标记待核验。程序化防幻觉校验只处理 Qwen 整理区，不删除原文明确讨论的时间矛盾。

A股确定性证券参考表只用于校验，不授权 Qwen 为原文没有代码的股票补代码。写文件前会检查 Qwen 新增的 6 位代码：原文没有该代码时从整理区移除；原文已有代码时按证券参考表确定性纠正 `.SH`/`.SZ` 后缀。原文抽取保持不变，文件末尾的“A股术语校验”表仍由 Python 生成。

## Agent CLI

固定入口是：

```bash
scripts/local-notes-agent profiles
scripts/local-notes-agent env-check --profile qingfeng
scripts/local-notes-agent sync-up --profile qingfeng --dry-run
scripts/local-notes-agent sync-up --profile qingfeng --limit 1
scripts/local-notes-agent ingest-url --profile qingfeng --url "https://www.bilibili.com/video/BVxxxx/"
scripts/local-notes-agent ingest-file --profile qingfeng --file "/allowed/inbox/document.pdf"
scripts/local-notes-agent ingest-file --profile qingfeng --destination daily_review --file "/allowed/videos/daily.mp4"
scripts/local-notes-agent retry-failed --profile qingfeng
scripts/local-notes-agent rebuild-index --profile qingfeng
scripts/local-notes-agent status --limit 20
scripts/local-notes-agent status --run-id "UUID"
```

CLI stdout 只输出最终 JSON；普通 Worker 日志经脱敏后写 stderr。`dry-run` 不访问网络、不调用模型、不写笔记、Manifest 或任务历史。

## UP 主增量规则

`bilibili-up-video` 分页读取 UP 空间视频，并用 BVID 去重。`bilibili-up-sync` 根据 Profile 顺序复用现有 `bilibili-up-opus` 和单视频 `bilibili-url` 链路：

1. 发现图文动态和视频；
2. 检查现有 Manifest 与完整输出；
3. 只处理未完成项，失败项保留为 `failed`；
4. 完成下载/转写、Qwen 整理和输出完整性校验后才写 `completed`；
5. 单项失败不阻断其他项，最终返回 `partial_failed`；全部选中项失败则返回非零退出和稳定错误；
6. `retry-failed` 只选择失败项；普通同步不会反复冲击已记录失败项；
7. `limit=0` 处理全部尚未完成且符合本次模式的内容，正数限制本批候选数。

UP 同步 Manifest 位于 `state/up-sync/<mid>.json`，记录 BVID、来源 URL、`source_hash`、作者 UID、发布时间、输出路径、整理状态及稳定错误码。已发现但未完整成功的内容不会被永久跳过。

## 全局锁与任务历史

所有 Worker 写入口共享：

```text
~/Library/Application Support/Local Note Studio/state/global-task.lock
~/Library/Application Support/Local Note Studio/state/global-task.json
```

锁覆盖 Worker 及其业务子进程的完整生命周期；子进程继承锁文件描述符。进程正常退出、取消、超时或异常结束后，操作系统会释放锁，不会形成永久死锁。元数据只含 `run_id`、脱敏 task、caller、PID 和开始时间。`env-check`、`status`、Profile 列表及 Manifest 状态等只读操作不获取写锁。

共享审计历史位于：

```text
~/Library/Application Support/Local Note Studio/state/automation-history.sqlite3
```

SQLite 使用 WAL 与完整同步，保存脱敏请求、状态、统计、输出、错误码、重试关系以及 Worker/Schema/规则版本。它与现有 GUI `localStorage` 历史并存；当前版本没有迁移或删除旧 GUI 历史。

`status` 同时返回当前锁、运行/历史记录以及不含来源正文和秘密的 Manifest 汇总计数，包括 Application Support 索引和各 UP 同步 Manifest。

## 结果合同

每次 Worker 调用在正常日志之后输出一行 `TASK_RESULT_JSON:`。Agent CLI 和 MCP 返回同一份 Schema 1.0：

```json
{
  "schema_version": "1.0",
  "run_id": "UUID",
  "caller": "agent",
  "task": "bilibili-up-sync",
  "status": "completed",
  "started_at": "ISO-8601",
  "finished_at": "ISO-8601",
  "source_ref": "masked-or-stable-id",
  "output_dir": "/allowed/output/path",
  "counts": {
    "discovered": 0,
    "created": 0,
    "updated": 0,
    "skipped": 0,
    "failed": 0
  },
  "outputs": [],
  "deliveries": [],
  "manifest_path": "",
  "warnings": [],
  "details": {},
  "error": null,
  "retryable": false
}
```

`status` 可为 `completed`、`no_changes`、`partial_failed`、`failed`、`cancelled` 或 `timeout`；异常强制终止且来不及返回结果的历史记录会在下次查询时恢复为 `interrupted`。`deliveries` 在可用时包含笔记路径、`source_type`、`source_url`、`source_hash`、BVID/动态 ID、`author_mid`、`published` 和 `organized_status`，供下游读取；Local Note Studio 不执行股票观点提取或数据库写入。

`env-check` 会在 `details.environment_report` 返回脱敏后的运行时检查报告，并与真实任务共用 request → environment → 内置 LM Studio 默认值的有效 LLM 配置解析；内置默认 key 只报告为 `set`。B站图文任务还可在 `details.opus_image_analysis` 返回模式、图片数、`finish_reason`、数值型 `completion_tokens`、`max_tokens`、`timeout_seconds`、`retry_count`、真实模型调用、缓存命中、失败和安全上限统计。Cookie、API Key、浏览器 Profile 和图片 base64 字段从结果/历史合同中直接省略，warning 也不含 reasoning 或完整服务端错误正文。Profile、状态或历史读取本身失败时仍返回上述合同，而不是未结构化的 traceback。

稳定错误码包括：`INVALID_REQUEST`、`PROFILE_INVALID`、`PROFILE_NOT_FOUND`、`PATH_NOT_ALLOWED`、`URL_NOT_ALLOWED`、`TASK_LOCKED`、`TASK_TIMEOUT`、`TASK_CANCELLED`、`TASK_INTERRUPTED`、`PARTIAL_FAILURE`、`BILIBILI_AUTH_INVALID`、`BILIBILI_RATE_LIMITED`、`BILIBILI_DISCOVERY_FAILED`、`BILIBILI_SUBTITLE_UNAVAILABLE`、`BILIBILI_DOWNLOAD_FAILED`、`ASR_FAILED`、`LLM_FAILED`、`OUTPUT_INTEGRITY_FAILED`、`OUTPUT_MISSING`、`SOURCE_ACCESS_DENIED`、`SOURCE_NOT_FOUND`、`BATCH_ALL_FAILED`、`STATE_STORAGE_ERROR`、`WORKER_CONTRACT_ERROR`、`UNSUPPORTED_REQUEST` 和兜底 `TASK_FAILED`。是否适合重试由 `retryable` 明确给出，无需解析中文日志。

## 已有笔记只读检索

0.1.19 在原有七个工具之外增加四个只读工具，底层只处理所有已启用 Profile 的正式 Markdown 输出：

- `local_notes_search(query, author?, date_range?, limit?)`：NFKC 规范化、英文大小写不敏感的确定性关键词检索；
- `local_notes_get(path_or_id, offset?, max_chars?, section?)`：按稳定 `note_id` 或已索引路径读取元数据、章节清单和受限正文；
- `local_notes_list_recent(author?, content_type?, days?, limit?)`：只用可信 `published` 列出近期笔记；
- `local_notes_get_viewpoints(symbol_or_topic, as_of_date, limit?)`：只返回截止历史日期的直接证据并作确定性候选分组，不在服务端总结观点或生成投资建议。

四个工具均声明 `readOnlyHint=true`、`destructiveHint=false`、`idempotentHint=true`、`openWorldHint=false`。查询进程不会访问网络、LM Studio、Shell、Stocks MCP 或 MySQL，也不会写笔记、Manifest、SQLite 自动化历史、全局锁、缓存或索引。空结果是 `status=completed` 且返回数为 0。

### 显式建索引与原子恢复

首次检索前运行：

```bash
scripts/local-notes-agent rebuild-index --profile qingfeng
```

默认输出位于：

```text
~/Library/Application Support/Local Note Studio/state/indexes/note-indexes/<profile_id>/note-index.json
~/Library/Application Support/Local Note Studio/state/indexes/note-indexes/<profile_id>/asset-index.json
```

开发或验收可用 `INDEX_DIR` 或 `LOCAL_NOTES_READ_INDEX_DIR` 指向 `/private/tmp`。索引通过同目录临时文件、`fsync` 和 `os.replace` 原子替换；失败不会改写正式笔记。Agent 的 `sync-up`、`retry-failed`、`ingest-url`、`ingest-file` 在非 dry-run 成功或部分成功后刷新对应 Profile 索引；刷新失败只附加 `NOTE_INDEX_REFRESH_FAILED` warning，可稍后显式重建。

查询发现索引缺失、JSON/Schema 损坏或 Markdown 集合的路径、大小、mtime 指纹变化时，分别返回 `NOTE_INDEX_UNAVAILABLE`、`NOTE_INDEX_CORRUPT`、`NOTE_INDEX_STALE`，不会在查询中偷偷重建。修复方式是先确认 Profile 和正式笔记目录，再执行 `rebuild-index`；索引可删除后重建，但不要为此批量改写笔记。

### note-index Schema 2.0

Profile 索引保留旧索引的 `path`、`title`、`type`、`source_type`、`source_path`、`status`、`tags`、`links`、`asset_count`、`modified_at`、`size`，并增加：

```text
note_id, profile_id, note_path, relative_path, source_url,
author, author_mid, published, date_source, content_type,
bvid, avid, dynamic_id, opus_id, model, organize_model,
organized_status, source_hash, headings, sections,
missing_metadata, metadata_quality, index_text_available
```

`note_id` 优先使用 `source_type + BVID/dynamic_id/source_url`，无网络 ID 时使用规范化 `source_path + source_hash`，最后才回退到 Profile 内相对 Markdown 路径。重复来源身份会幂等去重，并优先保留完整正式笔记。PPTX、PDF、网页和本地资料不要求 BVID 或动态 ID，不适用字段为 `null`。

确定性回退规则：

- BVID：frontmatter → `source_url` → 文件名 → `null`；
- 动态 ID：frontmatter 的 `dynamic_id/opus_id` → `source_url` → 文件名 → `null`；
- 整理模型：`organize_model` → `model` → `null`；
- 发布时间：`published` 优先；兼容 `published_at/publish_date/date` 时明确标记 `date_source=frontmatter`。mtime 只保存在 `modified_at`，不会冒充可信发布时间；
- 缺失字段进入 `missing_metadata` 和 `metadata_quality`，不调用模型、不猜测。

`source_type/content_type` 是可扩展字符串；当前兼容 `video`、`bilibili-video`、`bilibili-opus`、`pptx/presentation`、`pdf`、`webpage`、`wechat-article`、`local-file/local-video` 和 `unknown`。当前只把已经生成的 Markdown 正式笔记作为结果；原始 PPT/PPTX、视频、未整理动态不会直接出现。以后 `ingest-file` 把 PPT 生成 Markdown 后，会沿用同一个 Profile 索引，无需改查询工具。

### 检索、来源层级与时点边界

检索排序固定为标题精确命中最高，其次作者/标签/证券代码或来源 ID、小标题、正文；同分按 `published` 降序和 `note_path` 升序。`date_range` 是闭区间，只接纳 `date_source=published/frontmatter` 的显式日期，不接纳 mtime；无可信发布时间的旧笔记不会混入日期过滤结果。`limit` 默认 20、硬上限 50，查询和返回字符、单文件大小、累计扫描量也有硬限制。

每个 snippet 都携带 `provenance`：

| provenance | 含义 |
| --- | --- |
| `source_text` | “原文抽取”等直接来源文本 |
| `transcript` | 字幕、完整转写或 ASR 文本 |
| `llm_organized` | “Qwen 整理”及其结构化子章节 |
| `llm_visual_analysis` | 图片视觉/OCR 分析 |
| `deterministic_metadata` | Python 生成的来源追溯、来源信息等 |
| `unknown` | 旧笔记无法可靠判断 |

因此 `Qwen 整理` 片段不会被标成 UP 主原话。`local_notes_get` 默认返回受限正文，长笔记可用 `offset/max_chars` 分页，或用标题/上述 provenance 作为 `section` 读取，并返回 `truncated`、`next_offset` 和行号。

观点检索要求 `as_of_date=YYYY-MM-DD`。可信发布时间晚于该日期的证据严格排除；没有可信发布时间的内容只能进入 `undated_candidates`。候选分组为 `methodology_candidates`、`dated_viewpoints`、`historical_or_stale_candidates`、`undated_candidates`，并返回明确的 `classification_basis`：0–7 天为 recent、8–30 天为 aging、超过 30 天为 historical。历史分组不声称事实或观点已经失效；方法论也不会仅因时间较早被判过期。

### OpenHanako 调用示例

导入 Connector 后可让 HanaAgent 使用以下参数调用：

```json
{"query":"七轨布林","author":"青枫","date_range":{"start":"2026-01-01","end":"2026-07-22"},"limit":10}
{"author":"青枫","content_type":"bilibili-opus","days":30}
{"path_or_id":"note_0123456789abcdef01234567","section":"source_text","max_chars":8000}
{"symbol_or_topic":"七轨布林","as_of_date":"2026-06-30","limit":20}
```

`local_notes_get` 的示例 `note_id` 只是占位符，应先从搜索结果复制真实值。检索错误使用稳定错误码：`NOTE_INDEX_UNAVAILABLE`、`NOTE_INDEX_CORRUPT`、`NOTE_INDEX_STALE`、`NOTE_NOT_FOUND`、`NOTE_PATH_NOT_ALLOWED`、`NOTE_READ_FAILED`、`INVALID_QUERY`、`INVALID_DATE_RANGE`、`INPUT_LIMIT_EXCEEDED`。

资料状态需分开理解：已整理 Markdown 才能检索；已发现但未整理、整理失败、以及尚未纳入 Profile 的原始资料都不会出现在检索结果。索引不代表原始资料发现清单。

## stdio MCP 与 OpenHanako

MCP 固定入口为 `scripts/local-notes-mcp`。stdin/stdout 只传输逐行 JSON-RPC；日志只写 stderr。Server 支持初始化、工具枚举、工具调用、ping 和干净 EOF 退出。OpenHanako 关闭 Connector 时会先关闭 stdin，再升级为 SIGTERM/SIGKILL；Server 在终止信号中会清理正在运行的 Worker 进程组。

工具（共 11 个）：

- 只读：`local_notes_env_check`、`local_notes_get_status`、`local_notes_list_profiles`、`local_notes_search`、`local_notes_get`、`local_notes_list_recent`、`local_notes_get_viewpoints`；
- 写入且幂等：`local_notes_sync_up`、`local_notes_ingest_url`、`local_notes_ingest_file`、`local_notes_retry_failed`。

第一版没有暴露 `local_notes_cancel_run`：当前同步调用期间 Server 不能可靠地并行接收取消请求，因此不会宣称不可靠的取消能力。OpenHanako Connector 自身停止或超时仍会终止完整进程组，最终状态可用 `run_id` 查询。

在 OpenHanako 正式 APP 的“设置 → MCP”中导入 [`openhanako-mcp.example.json`](openhanako-mcp.example.json)，或手工选择本地 stdio 并填写：

- command：项目中 `scripts/local-notes-mcp` 的绝对路径；
- cwd：本项目绝对路径；
- timeout：建议 `21600` 秒，需覆盖视频下载、ASR 和 Qwen；
- autoStart：开启；
- env：留空，不放 API Key 或 Cookie。

该格式已按 OpenHanako 当前公开实现支持的 `mcpServers`、`transport`、`command`、`args`、`cwd`、`env`、`timeout`、`autoStart` 字段校对。这里只配置正式发布的 OpenHanako APP；不修改、构建或向 `OpenHanako.app` 写入任何文件。

## 验证与排错

```bash
npm run check
npm run release:check
scripts/local-notes-agent profiles
scripts/local-notes-agent sync-up --profile qingfeng --dry-run
```

可用逐行 JSON-RPC 检查 MCP 初始化和工具枚举。未得到用户明确授权时，不运行真实全量同步；受控验证最多使用 `limit=1` 且保持 `overwrite_outputs=false`。

Spotlight 可能同时显示三个 Local Note Studio：`/Applications` 是正式安装；`src-tauri/target/release` 是 release 构建产物；`src-tauri/target/debug` 是 debug 构建产物。它们共享同一套 Application Support 数据，不是三套独立数据安装。普通构建、测试和启动流程不会自动删除这些构建目录；需要时可在确认路径后人工清理。

## 0.1.17 本机验收记录

2026-07-21 在开发机先完成了不访问真实B站、不调用真实 LLM、不写正式笔记的初步验收；随后用与 OpenHanako 相同的 stdio MCP 协议进行了两条真实图文动态测试，真实输出限制在 `/private/tmp/local-note-opus-vision.MBrmE8`，没有写正式笔记目录。后续复核发现视觉 token 失控、图片日期幻觉和股票代码冲突，因此下列内容只能作为当时的阶段性记录，不能再解读为 `0.1.17` 真实视觉验收通过。

### 当时已通过的静态或阶段性检查

- `/Applications/Local Note Studio.app` 的 Bundle 版本为 `0.1.17`，Bundle ID 为 `studio.local-note`；
- `codesign --verify --deep --strict` 通过；
- 安装版可执行文件与 `target/release` 可执行文件 SHA-256 相同；
- 修复前基线 DMG SHA-256 为 `0441e21b87093cb63dce09dd0d3f8e82eebb3b1a2425cf90b3995ec3664fcf0d`；修复后重打包 DMG SHA-256 为 `4934f073cb386eb632d34c97717c3a341146d07db8659e5cfdf5004144d164e4`；
- 安装包包含 Worker、Agent、Profile、自动化核心和 MCP 模块；
- 使用 Node.js `20.20.2` 与 `course-whisper` Python `3.11.15` 运行完整 Python 回归、`npm run check` 和 `npm run release:check`，前端构建、82 项 Python、10 项 Rust 及发布配置全部通过；
- `/private/tmp` 隔离 Profile 的 `profiles`、`status`、`env-check` 和 `sync-up --dry-run` 通过；
- 直接调用安装包内置 `local_notes_agent.py` 的 Profile 列表和 dry-run 通过；
- stdio MCP 的 initialize、七工具枚举、Profile 查询和同步 dry-run 通过；
- dry-run 没有生成笔记、UP Manifest 或任务历史条目；
- OpenHanako 模拟客户端通过 `local_notes_sync_up(profile=qingfeng_test, limit=1)` 读取正式 Application Support Cookie，真实抓取动态 `1227413653675835411`，调用本地 Qwen，并在隔离目录生成 1 篇 Markdown 和 2 张图片；MCP 返回 `completed`、`created=1`、`failed=0`、`isError=false`；
- 对同一 Profile 立即重跑后返回 `no_changes`、`created=0`、`skipped=1`，未重复调用 Qwen；`local_notes_get_status` 能读回审计记录，返回合同不含 API Key 或 Cookie；
- 动态 `1227413653675835411` 的旧缓存结果曾显示两张图片低相关并在重跑时返回 `no_changes`；但全新缓存复测时第二张简单互动榜持续推理超过 8 分钟，LM Studio 已生成约 `28,314` tokens 仍未结束，因此旧缓存重跑不能作为成功证据；
- 动态 `1215072468872462337` 的旧结果曾识别“72家澄清公告”分组；但正式笔记同时出现了“应为2024年”的无依据年份纠正和错误 `.SH` 后缀，故内容验收并未通过；
- 两条验收后的 `local_notes_get_status` 返回完整审计记录和 `lock: null`；请求历史中的 API Key、Cookie 和浏览器 Profile 均为空，MCP stdout 仍只含 JSON-RPC 协议消息；
- 修复后的 `Local Note Studio_0.1.17_aarch64.dmg` 已完成 `hdiutil verify`，并对只读挂载后的 `.app` 执行严格深度签名、arm64 架构、版本、内置 Worker 脚本和无 `env.local` 检查；
- 用绝对路径启动正式安装包后创建一个主窗口，空闲状态没有遗留 Worker/MCP 子进程。

### 环境发现

- 开发机默认 `/usr/local/bin/node` 为 Node.js 16，会使 Vite 构建报 `crypto.getRandomValues is not a function`；源码测试需使用 Node.js 20 或更新版本；
- Apple Command Line Tools Python 3.9 缺少 `requests`，不能运行完整 Python 回归；应使用兼容 Python/Conda；
- 当前 `course-whisper` 环境已安装 `mlx-whisper 0.4.3`，正常 macOS 权限下可成功导入 `mlx_whisper`，可继续用于无字幕视频的 ASR 回退。隔离的 Agent 验收环境因无法访问 Metal，导入探针曾返回 `No Metal device available`；这是受限执行环境造成的误报，并非依赖缺失；
- 纯 dry-run 隔离测试没有复制正式 Cookie，所以最初环境检查的 Cookie 警告符合预期。真实 MCP 验收显式保留 `LOCAL_NOTE_STUDIO_APP_DATA_DIR` 指向正式 Application Support，以只读方式复用认证和模型配置，同时把 `LOCAL_NOTE_STUDIO_STATE_DIR`、`INDEX_DIR`、Profile 输出目录继续隔离到 `/private/tmp`；只覆盖 State 而不固定 App Data 会使默认 Cookie 路径也随 State 一起迁移；
- 修复前基线 Profile 的 `enable_ocr=false`，两张图片虽已下载且 Markdown 相对引用有效，但 Qwen 只将其标为“图片内容待核验”，未执行视觉/OCR 解读。图片实际是粉丝观看/互动排行榜，与动态正文的交易心态观点无直接关系；该问题现由独立的 `opus_image_analysis` 链路修复；
- 修复前基线中，Qwen 曾将当日 `15:11` 的有效发布时间误写为“与当前实际时间不符/未来预设”。来源元数据本身正确；该问题现由 Python `Asia/Shanghai` 时间解析和确定性来源追溯修复，不再交给模型猜测；
- 当前机器尚未初始化 App-managed runtime，正式 GUI 仍按现有 Conda 高级后端测试；
- 由于 debug/release 构建与正式 APP 使用相同 Bundle ID，`open -a "Local Note Studio"` 可能启动项目中的 debug 包。验收和脚本应使用 `/Applications/Local Note Studio.app` 绝对路径，日常从“应用程序”目录启动正式版。

### 尚未覆盖

- 一条真实公开视频的字幕/ASR、完整性和幂等验证；
- OpenHanako 正式 APP 中实际导入 Connector 并调用工具；
- GUI 真实任务、取消、全局锁竞争及退出后的子进程清理；
- LLM 失败后的 `retry-failed` 恢复；
- App-managed runtime 的安装/修复和无 Conda 运行；
- 正式青枫笔记目录的 `limit=1` 试运行与格式验收。

### 0.1.17 最终结论

`Local Note Studio_0.1.17_aarch64.dmg`（SHA-256 `4934f073cb386eb632d34c97717c3a341146d07db8659e5cfdf5004144d164e4`）只保留为问题基线，不应安装或用于正式自动化。失控任务最终通过终止 MCP 取消；审计、锁释放和完整进程组清理正常。

## 0.1.18 最终小修复与全新缓存复验

2026-07-22 使用新的 `/private/tmp/local-note-opus-018-retry.R6o2TO` output、state、Profile 和图片缓存，通过 OpenHanako-compatible stdio MCP 重新验收。正式 Application Support 只读复用 Cookie；所有笔记、审计和缓存输出都限制在 `/private/tmp`。没有修改 OpenHanako、Stocks、正式青枫笔记目录、认证数据或 `/Applications` 安装版。

- 低相关动态 `1227413653675835411` 首跑 213 秒：2 张图均完成并标为低相关，`model_calls=2`、`failed=0`、`finish_reason=stop`、合计 `completion_tokens=3185`、单次 `max_tokens=4096`、超时 `300` 秒，且相邻图片调用及图片到整理之间均执行 60 秒冷却；没有基于粉丝榜图片扩展金融结论，发布时间保持 `2026-07-21T15:11:51+08:00`。立即复跑 2 秒，返回 `no_changes`、`cache_hits=2`、`model_calls=0`。
- 财经动态 `1215072468872462337` 首跑 188 秒：1 张图标为高相关，`model_calls=1`、`failed=0`、`finish_reason=stop`、`completion_tokens=1561`、`max_tokens=4096`、超时 `300` 秒；“72家澄清公告”的可核验分组进入笔记，图片可见日期保持“2026年6月17日晚间”，发布时间为 `2026-06-18T09:01:45+08:00`。中材科技、山东墨龙、通鼎互联、中核科技分别为 `002080.SZ`、`002490.SZ`、`002491.SZ`、`000777.SZ`，不存在对应错误 `.SH`；来源中的“沃什”没有被补成 `Waller`。立即复跑 1 秒，返回 `no_changes`、`cache_hits=1`、`model_calls=0`。
- 确定性回归覆盖 `finish_reason=length`、空内容和无效 JSON：同一图片在同一任务中最多调用两次；第二次成功会写出包含正文、附件和完整图片分析的笔记，第二次仍失败会写出正文、附件和“图片分析失败/待重试”占位符，失败缓存目录保持为空。两次调用各自仍为 `max_tokens=4096`、`timeout=300`，重试前走统一 60 秒 cooldown。
- 用包含真实字段 `output_path`、`organized_status`、`organized_output_path` 的隔离 Manifest 验证 `local_notes_get_status`：两个正式输出均存在而 staging 草稿不存在时，返回 `completed=2`、`missing_output=0`、`lock=null`。历史合同保留数值型 retry/token/timeout/cache/model-call 诊断，同时省略 API Key、Cookie、浏览器 Profile 和图片 base64。
- 从最终 DMG 的只读挂载资源直接运行 Agent `env-check`，未显式传入 LLM 配置时，内置 LM Studio base、key、model 三项均显示 `[OK]`；key 只显示为 `set`，没有进入日志或合同。MCP EOF 后停止了本次临时启动的 LM Studio API，且没有遗留 MCP、Worker、转换或整理进程。
- Node.js `20.20.2`、course-whisper Python `3.11.15` 下前端构建/兼容检查、102 项 Python 和 10 项 Rust 测试通过。`0.1.18` DMG 的最终路径、大小、SHA-256 和只读挂载验证见 [`release-macos.md`](release-macos.md)。
