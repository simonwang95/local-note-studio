# Agent 自动化

Local Note Studio 0.1.17 提供受限 Agent CLI 和本地 stdio MCP Server。OpenHanako 等 Agent 只负责选择命名 Profile、触发任务和查询状态；采集、转写、Qwen 整理、Manifest、恢复点和完整性检查仍由 `worker/local_note_studio_worker.py` 及现有脚本完成。

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
| `allowed_input_roots` | `ingest-file` 可读取的绝对根目录 |
| `allowed_domains` | `ingest-url` 可访问的域名及其子域名 |
| `limit` | 单次处理上限；`0` 表示所有未完成内容 |
| `max_limit` | Agent 显式 `--limit` 的硬上限 |
| `overwrite_outputs` | 默认应保持 `false` |
| `lock_timeout_seconds` | `0` 为锁冲突立即失败；正数为最长等待时间 |
| `execution_timeout_seconds` | 整个任务的硬超时，`0` 表示不设置 |

覆盖优先级固定为：Agent 显式的安全参数（目前仅来源、受上限约束的 `limit`、`dry_run`）高于 Profile；Profile 高于普通 Worker 环境默认值；秘密始终只从 `env.local` 或 Application Support auth 读取。Agent 无法显式覆盖运行时、模型、输出根、覆盖策略或秘密。

## Agent CLI

固定入口是：

```bash
scripts/local-notes-agent profiles
scripts/local-notes-agent env-check --profile qingfeng
scripts/local-notes-agent sync-up --profile qingfeng --dry-run
scripts/local-notes-agent sync-up --profile qingfeng --limit 1
scripts/local-notes-agent ingest-url --profile qingfeng --url "https://www.bilibili.com/video/BVxxxx/"
scripts/local-notes-agent ingest-file --profile qingfeng --file "/allowed/inbox/document.pdf"
scripts/local-notes-agent retry-failed --profile qingfeng
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

`env-check` 会在 `details.environment_report` 返回脱敏后的运行时检查报告；同一报告仍作为普通日志写入 CLI 的 stderr。Profile、状态或历史读取本身失败时也返回上述合同，而不是未结构化的 traceback。

稳定错误码包括：`INVALID_REQUEST`、`PROFILE_INVALID`、`PROFILE_NOT_FOUND`、`PATH_NOT_ALLOWED`、`URL_NOT_ALLOWED`、`TASK_LOCKED`、`TASK_TIMEOUT`、`TASK_CANCELLED`、`TASK_INTERRUPTED`、`PARTIAL_FAILURE`、`BILIBILI_AUTH_INVALID`、`BILIBILI_RATE_LIMITED`、`BILIBILI_DISCOVERY_FAILED`、`BILIBILI_SUBTITLE_UNAVAILABLE`、`BILIBILI_DOWNLOAD_FAILED`、`ASR_FAILED`、`LLM_FAILED`、`OUTPUT_INTEGRITY_FAILED`、`OUTPUT_MISSING`、`SOURCE_ACCESS_DENIED`、`SOURCE_NOT_FOUND`、`BATCH_ALL_FAILED`、`STATE_STORAGE_ERROR`、`WORKER_CONTRACT_ERROR`、`UNSUPPORTED_REQUEST` 和兜底 `TASK_FAILED`。是否适合重试由 `retryable` 明确给出，无需解析中文日志。

## stdio MCP 与 OpenHanako

MCP 固定入口为 `scripts/local-notes-mcp`。stdin/stdout 只传输逐行 JSON-RPC；日志只写 stderr。Server 支持初始化、工具枚举、工具调用、ping 和干净 EOF 退出。OpenHanako 关闭 Connector 时会先关闭 stdin，再升级为 SIGTERM/SIGKILL；Server 在终止信号中会清理正在运行的 Worker 进程组。

工具：

- 只读：`local_notes_env_check`、`local_notes_get_status`、`local_notes_list_profiles`；
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
