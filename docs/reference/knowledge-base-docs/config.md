# 配置说明

项目使用 `env.local` 保存本机配置，使用 `env.example` 作为可提交模板。

## 核心变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NOTES_DIR` | `notes` | Markdown 笔记输出目录 |
| `OBSIDIAN_VAULT_DIR` | 空 | 正式 Obsidian Vault 根目录，待定时可留空 |
| `SOURCE_DIR` | `source` | 原始源文件目录 |
| `SAMPLE_OUTPUT_DIR` | `notes/_samples/source-conversion` | 源文件转换样稿输出目录 |
| `ORGANIZED_OUTPUT_DIR` | `notes/_organized` | Qwen 正式整理笔记输出目录 |
| `INDEX_DIR` | `indexes` | manifest 和索引目录 |
| `AI_PAPER_DRAFT_DIR` | `_drafts/AI_paper` | AI paper PDF 转换草稿目录，不进入 Obsidian 正式笔记 |
| `AI_PAPER_NOTE_DIR` | `notes/AI/AI_paper` | AI paper 正式笔记目录 |
| `AI_PAPER_QUICKREAD_DIR` | `notes/AI/_quickread/AI_paper` | AI paper 速读材料目录 |
| `WEB_FETCH_TIMEOUT_SECONDS` | `30` | 网页/微信公众号抓取超时时间 |
| `WEB_DOWNLOAD_ASSETS` | `true` | 网页/微信公众号转换时默认下载图片资产 |
| `WEB_ASSET_MAX_BYTES` | `52428800` | 单个网页图片资产下载大小上限 |
| `WEB_USER_AGENT` | Chrome UA | 网页抓取使用的 User-Agent |
| `SOURCE_CONVERSION_SAMPLE_LIMIT_DOCX` | `2` | 样稿模式最多转换的 DOCX 数量 |
| `DEFAULT_LLM_PROVIDER` | `lmstudio` | 默认本地模型服务 |
| `DEFAULT_LLM_API_BASE` | `http://127.0.0.1:1234/v1` | OpenAI-compatible API 地址 |
| `DEFAULT_LLM_MODEL` | `qwen3.6-35b-a3b-nvfp4` | 默认主整理模型 |
| `QWEN_PDF_POLISH_MAX_CHARS` | `18000` | PDF 分块交给 Qwen 整理的最大字符数 |
| `QWEN_PDF_POLISH_TIMEOUT_SECONDS` | `180` | 单次 Qwen PDF 整理请求超时时间 |
| `QWEN_PDF_POLISH_COOLDOWN_DELAY` | `60` | PDF 批量转换时，每个 Qwen 分块/每篇 PDF 之间的冷却等待秒数；未配置时继承 `COOLDOWN_DELAY` |
| `QWEN_PDF_POLISH_OVERLAP_PAGES` | `1` | PDF Qwen polish 分块时带上的前序页数，用于降低跨页公式/表格被截断的风险 |
| `QWEN_ORGANIZE_MAX_CHARS` | `22000` | 草稿正式整理时的单块最大字符数 |
| `QWEN_ORGANIZE_OVERLAP_CHARS` | `800` | 草稿正式整理和综合整理时的重叠上下文字符数 |
| `QWEN_ORGANIZE_SYNTHESIS_MAX_CHARS` | `28000` | 多分块综合整理的最大字符数 |
| `QWEN_ORGANIZE_TIMEOUT_SECONDS` | `300` | 单次 Qwen 正式整理请求超时时间 |
| `QWEN_ORGANIZE_MAX_RETRIES` | `2` | Qwen 正式整理遇到 408/429/5xx 等临时错误时的重试次数 |
| `QWEN_ORGANIZE_RETRY_DELAY` | `3` | Qwen 正式整理重试的初始等待秒数，后续指数退避 |
| `QWEN_ORGANIZE_COOLDOWN_DELAY` | `60` | Qwen 批量正式整理每篇之间的冷却等待秒数；未配置时继承 `COOLDOWN_DELAY` |
| `QWEN_QUICKREAD_MAX_CHARS` | `128000` | PDF 速读时一次性喂给 Qwen 的最大抽取文本字符数；设为 `0` 表示不主动截断 |
| `QWEN_QUICKREAD_MAX_TOKENS` | `80000` | PDF 速读请求的输出 token 预算 |
| `QWEN_QUICKREAD_TIMEOUT_SECONDS` | `1200` | PDF 速读单次请求超时时间 |
| `QWEN_QUICKREAD_MAX_RETRIES` | `2` | PDF 速读遇到 408/429/5xx 等临时错误时的重试次数 |
| `QWEN_QUICKREAD_RETRY_DELAY` | `5` | PDF 速读重试初始等待秒数，后续指数退避 |
| `QWEN_QUICKREAD_COOLDOWN_DELAY` | `60` | PDF 批量速读每篇之间的冷却等待秒数；未配置时继承 `COOLDOWN_DELAY` |
| `BILIBILI_OUTPUT_DIR` | `notes/Net/BiliBili` | B 站转写输出目录，脚本会直接写入该目录 |
| `BILIBILI_DEDUPE_DIRS` | `notes` | B 站收藏夹扫描时用于去重的目录，多个目录用系统路径分隔符分开 |
| `VIDEO_MANIFEST_ENABLED` | `true` | 视频后处理时是否写入 `indexes/video-manifest.json`，可用 `--no-video-manifest` 临时跳过 |
| `BILIBILI_STATE_DIR` | `indexes/bilibili-state` | B 站处理状态和报告目录 |
| `BILIBILI_FAV_MEDIA_ID` | 空 | B 站收藏夹 ID，本机配置 |
| `BILIBILI_COOKIES_FILE` | 空 | B 站 Netscape Cookie 文件路径，本机配置 |
| `BILIBILI_OPUS_REQUEST_DELAY_SECONDS` | `0.8` | UP 主图文批量抓取时，每条动态详情请求之间的等待秒数 |
| `BILIBILI_PREFER_WEB_SUBTITLE` | `false` | 是否尝试抓取网页播放器字幕；默认关闭，避免网页字幕接口偶发返回错配字幕 |
| `BILIBILI_WEB_SUBTITLE_LANGS` | `zh-CN,zh-Hans,zh-Hant,zh-TW,ai-zh,en,ai-en,ja,ai-ja,ko,ai-kr` | 网页播放器字幕语言优先级，逗号分隔 |
| `BROWSER_TYPE` | `chrome` | 未配置 Cookie 文件时尝试读取的浏览器 Cookie |
| `SUMMARY_API_URL` | `http://127.0.0.1:1234/v1` | B 站后处理使用的 OpenAI-compatible API |
| `SUMMARY_MODEL` | `qwen3.6-35b-a3b-nvfp4` | B 站摘要、导图、校对模型 |
| `SUMMARY_MAX_TOKENS` | `80000` | 给 Qwen 后处理的输出预算；Qwen reasoning 模型需要较大预算 |
| `LLM_TIMEOUT` | `1800` | B 站后处理单次 LLM 超时时间 |
| `COOLDOWN_DELAY` | `60` | B 站批处理视频之间/模型后处理之间的冷却等待秒数 |
| `SUMMARY_CHUNK_CHARS` | `20000` | B 站摘要/导图/校对的转录文本单块最大字符数 |
| `SUMMARY_CHUNK_OVERLAP_CHARS` | `800` | B 站摘要/导图/校对分块之间的重叠上下文字符数 |
| `SUMMARY_CHUNK_COOLDOWN_DELAY` | `60` | B 站摘要/导图/校对分块之间的冷却等待秒数；默认继承 `COOLDOWN_DELAY` |
| `ASR_ENGINE` | `whisper` | B 站字幕缺失时的 ASR 引擎，支持 `whisper` / `qwen3` |
| `ASR_LOCAL_MODEL` | 空 | 本地 Whisper 或 Qwen3-ASR 模型路径 |
| `ASR_PROGRESS_INTERVAL` | `30` | Whisper 转写进度提示间隔秒数 |
| `PROOFREAD_DOMAINS` | `finance,computer,medical,legal,engineering` | AI 校对时重点关注的术语领域 |

## 配置原则

- `env.local` 不应提交。
- API key、cookie、收藏夹 ID、模型真实路径只放 `env.local`。
- 脚本读取配置时，命令行参数优先级高于 `env.local`，`env.local` 高于内置默认值。
- 当 Obsidian Vault 目录未确定时，默认把样稿写入当前项目的 `notes/`。
