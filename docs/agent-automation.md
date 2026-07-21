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
| `opus_image_analysis` | `off`、`ocr` 或 `vision`；旧 Profile 缺省为 `off` |
| `lock_timeout_seconds` | `0` 为锁冲突立即失败；正数为最长等待时间 |
| `execution_timeout_seconds` | 整个任务的硬超时，`0` 表示不设置 |

覆盖优先级固定为：Agent 显式的安全参数（目前仅来源、受上限约束的 `limit`、`dry_run`）高于 Profile；Profile 高于普通 Worker 环境默认值；秘密始终只从 `env.local` 或 Application Support auth 读取。Agent 无法显式覆盖运行时、模型、输出根、覆盖策略或秘密。

`opus_image_analysis` 只影响 B站图文附件，不复用也不改变现有 `enable_ocr` 的含义：

- `off`：仅下载并保留 Markdown 图片引用，不调用图片模型，成本最低；
- `ocr`：按原顺序提取直接可见文字，模糊字符标记待核验，不做扩展推断；
- `vision`：结合正文识别可核验文字、表格、图形、K线、排行榜或截图，并标注相关性、有限整理和不确定项。财经 Profile 推荐使用此模式。

分析只读取已经下载到本地的附件，不重复请求图片。成功结果按图片 SHA-256、模式和模型缓存在 `state/opus-image-analysis-cache/`；命中缓存、`off` 和未进入真实模型调用的项目不会触发冷却。单条动态最多分析 12 张图片，超过部分保留原图并写 warning，避免无上限模型调用。模型失败也会保留正文、原始图片引用和“图片分析失败/待重试”占位，结果合同的 `warnings` 与 `details.opus_image_analysis` 会返回脱敏统计；完整成功的幂等重跑不会覆盖笔记或重复调用模型。

B站正文送入正式 Qwen 整理前会剥离来源 URL、作者/MID、动态 ID、发布时间、转换路径和 SHA256，只保留原文抽取、图片分析与来源类型说明。frontmatter 和“来源追溯”由 Python 确定性生成。发布时间使用 `Asia/Shanghai` 解析，允许 5 分钟时钟偏差；只有程序确认明显晚于当前时间时才产生 warning，模型不得自行生成“时间戳待核验”或“未来预设”。

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

`env-check` 会在 `details.environment_report` 返回脱敏后的运行时检查报告；B站图文任务还可在 `details.opus_image_analysis` 返回模式、图片数、真实模型调用、缓存命中、失败和安全上限统计。图片分析 warning 不含图片内容、Cookie、API Key 或服务端秘密。Profile、状态或历史读取本身失败时也返回上述合同，而不是未结构化的 traceback。

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

## 0.1.17 本机验收记录

2026-07-21 在开发机先完成了不访问真实B站、不调用真实 LLM、不写正式笔记的初步验收；随后通过与 OpenHanako 相同的 stdio MCP 协议补充了两条真实图文动态测试，真实输出仍限制在 `/private/tmp/local-note-opus-vision.MBrmE8`，没有写正式笔记目录。

### 已通过

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
- 修复后用 `vision` 重新验收动态 `1227413653675835411`：两张粉丝观看/互动榜均标为与交易观点低相关，不生成财经扩展；frontmatter 与来源追溯的发布时间均为 `2026-07-21T15:11:51+08:00`，没有“未来预设”。即时重跑命中 2 张图片缓存，图片模型和正式 Qwen 调用均为 0，返回 `no_changes`；
- 单条验收青枫浦上Q动态 `1215072468872462337`：一张“72家澄清公告”行业表被标为高相关，PCB/电子布、玻璃基板、MLCC、半导体/存储、光通信等明确分组进入正式笔记，颜色量化标准和公告细节保留为待核验。即时重跑命中 1 张图片缓存、模型调用为 0、资产复用 1，返回 `no_changes`；
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

下一轮应继续使用隔离输出目录完成一条短视频及 ASR 回退，再在 OpenHanako 正式 APP 中手工导入 Connector 并调用；只有视频幂等、失败恢复和正式客户端调用通过后，才配置正式笔记目录或定时任务。
