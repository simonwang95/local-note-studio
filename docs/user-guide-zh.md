# 中文操作手册

这份文档面向 Local Note Studio 的日常使用。目标是先把 Mac 桌面应用跑起来，再用它检查环境、预览命令、执行笔记整理任务。

## 1. 先分清两个启动方式

### 推荐：桌面应用模式

真正运行任务时，请使用：

```bash
npm run tauri:dev
```

这个命令会启动两部分：

- Vite 前端开发服务：`http://127.0.0.1:1420`
- Tauri 桌面窗口：Local Note Studio

只有 Tauri 桌面窗口能调用 Rust `run_worker` 命令，从而运行 Python worker、检查依赖、预览命令和执行任务。桌面窗口启动后会自动运行一次“检查依赖”，日志区会直接显示当前环境是否可用。

### 仅预览界面：浏览器模式

```bash
npm run dev
```

这个命令只会打开普通网页预览。可以检查布局、填写表单，但不能检查依赖或运行任务。浏览器日志区会提示“当前页面运行在普通浏览器预览环境，无法调用 Tauri worker”。

如果你在 Chrome 里看到 `localhost:1420` 或 `localhost:5173`，那通常只是网页预览，不代表 Tauri worker 可用。请以 Tauri 桌面窗口为准。

## 2. 第一次启动前准备

安装前端依赖：

```bash
npm install
```

准备本机配置文件：

```bash
cp worker/env.example worker/env.local
```

然后编辑 `worker/env.local`。真实用户名、Obsidian Vault 路径、cookie 路径、模型路径都只放在这个文件里。它已被 `.gitignore` 忽略，不会提交到 git。

常用配置示例：

```bash
CONDA_ENV="course-whisper"
DEFAULT_LLM_API_BASE="http://127.0.0.1:1234/v1"
DEFAULT_LLM_API_KEY="lm-studio"
DEFAULT_LLM_MODEL="qwen3.6-35b-a3b-nvfp4"
DEFAULT_OUTPUT_ROOT="/Users/xxx/Notes"
NOTES_DIR="/Users/xxx/Notes"
OBSIDIAN_VAULT_DIR="/Users/xxx/Notes"
BILIBILI_OUTPUT_DIR="/Users/xxx/Notes/Net/BiliBili"
BILIBILI_COOKIES_FILE="/path/to/bili_cookies.txt"
```

### B站 Cookie 文件怎么获取

B站 Cookie 文件用于让脚本读取你自己账号可访问的字幕、公开视频信息、私有收藏夹或受限内容。它不是必须项：只处理公开视频时可以先留空；处理收藏夹、会员可见内容或字幕抓取不稳定时，再补充。

推荐方式是使用已经安装好的 `yt-dlp` 从 Chrome 读取登录态并导出 Netscape `cookies.txt` 格式文件：

1. 在 Chrome 或其他浏览器里登录 B站。
2. 在项目根目录运行：

```bash
yt-dlp --cookies-from-browser chrome \
  --cookies ./bili_cookies.txt \
  --skip-download \
  --print title \
  "https://www.bilibili.com/video/BV1DaGy6GEQK/"
```

这条命令会：

- 从 Chrome 读取当前登录态。
- 把 cookie 导出到项目根目录的 `bili_cookies.txt`。
- 用一个 B站视频链接测试 cookie 是否可用。
- 只打印标题，不下载视频。

也可以把 cookie 保存到本机私有目录，例如：

```text
/Users/xxx/.local/share/local-note-studio/bili_cookies.txt
```

3. 在 `worker/env.local` 中填写绝对路径：

```bash
BILIBILI_COOKIES_FILE="/Users/xxx/.local/share/local-note-studio/bili_cookies.txt"
BILI_COOKIE_FILE="/Users/xxx/.local/share/local-note-studio/bili_cookies.txt"
```

如果使用项目根目录的 `bili_cookies.txt`，可以写成：

```bash
BILIBILI_COOKIES_FILE="./bili_cookies.txt"
BILI_COOKIE_FILE="./bili_cookies.txt"
```

4. 在应用界面的“B站 Cookie 文件”输入框中也可以填写同一个路径。

备选方式：安装可信的 `cookies.txt` 导出工具或扩展，选择只导出 `bilibili.com` / `.bilibili.com` 相关 cookie，并保存为 Netscape `cookies.txt` 格式。

注意事项：

- 只导出 B站域名相关 cookie，不要导出全部网站 cookie。
- Cookie 等同于一段时间内的登录凭证，不要提交到 git，不要发给别人。
- 如果 B站退出登录、修改密码或 cookie 过期，需要重新导出。
- `worker/env.local` 已被 `.gitignore` 忽略，真实 cookie 路径应放在这里。
- 项目根目录的 `bili_cookies.txt` 和 `*_cookies.txt` 已加入 `.gitignore`。

## 3. 启动桌面应用

```bash
npm run tauri:dev
```

如果终端反复显示：

```text
Waiting for your frontend dev server...
```

先按 `Ctrl+C` 停止当前命令，再确认没有另一个 Vite 进程占用端口，然后重新运行 `npm run tauri:dev`。

当前项目的 Tauri 开发端口固定为 `127.0.0.1:1420`。不要手动改到 `5173`，否则 Tauri 可能接不上前端。

## 4. 按界面顺序操作

### 第一步：运行环境

填写或确认：

- `Conda 环境`：默认 `course-whisper`
- `Python 命令`：不用 conda 时才会用到，默认 `python3`
- `LLM API Base`：默认 `http://127.0.0.1:1234/v1`
- `API Key`：LM Studio 可用 `lm-studio`
- `模型`：默认 `qwen3.6-35b-a3b-nvfp4`
- `B站 Cookie 文件`：可选，处理私有收藏夹或受限字幕时通常需要

点击“检查依赖”。

检查结果里：

- `[OK]` 表示可用
- `[MISSING]` 表示必须修复
- `[WARN]` 表示可选项缺失，不一定影响所有任务

常见依赖包括：

- Python 3.10 或 3.11
- `pypdf`
- `lxml`
- `requests`
- `yt-dlp`
- `ffmpeg`
- 可选：`mlx-whisper`
- 可选：`opencc`
- 可选：`textutil`，macOS 通常自带；转换旧版 `.doc` 文件时需要
- 可选：`tesseract` 和 `pdftoppm`，当你希望在没有多模态 Qwen 时做图片 / 扫描 PDF OCR 备用
- 条件需要：`ASR_LOCAL_MODEL`，当 B站视频没有字幕、需要本地语音转文字时使用

如果使用 conda，可参考：

```bash
conda install -n course-whisper -c conda-forge ffmpeg
conda run -n course-whisper python3 -m pip install pypdf lxml requests yt-dlp mlx-whisper
```

如果检查结果显示 `pypdf` 缺失，只需要给当前 conda 环境补装：

```bash
conda run -n course-whisper python3 -m pip install pypdf
```

装完后重新点击“检查依赖”。

如果你已经在 LM Studio 或其他 OpenAI-compatible 服务里加载了支持视觉输入的 `qwen3.6` 模型，那么图片 OCR 和扫描版 PDF OCR 会优先直接走当前 LLM 接口，不依赖本机 Swift OCR。`tesseract` / `pdftoppm` 只是备用兜底。

### B站字幕和 ASR 的优先级

B站任务可以在界面“任务执行”里的“字幕/转录优先级”下拉框选择处理顺序：

| 选项 | 行为 | 适合场景 |
| --- | --- | --- |
| `yt-dlp 字幕优先` | 先找当前视频可确认的 CC/AI 字幕，找不到再用 ASR | 默认推荐，能避免网页字幕错配 |
| `网页播放器字幕优先` | 先尝试 B站网页播放器字幕，再回落到 yt-dlp 字幕和 ASR | 只在 yt-dlp 字幕不可用且你确认网页字幕正确时使用 |
| `ASR 语音转写优先` | 跳过字幕检测，直接下载当前视频音频并本地转写 | 字幕明显错误、错配或质量很差时使用 |

选择“本地视频/音频”任务时，这个下拉框会自动切换为本地专用选项：

| 选项 | 行为 |
| --- | --- |
| `同目录 SRT 字幕优先` | 优先使用媒体文件旁边的同名 `.srt` 字幕，找不到再 ASR |
| `ASR 语音转写优先` | 跳过同目录字幕，直接做本地语音转写 |

默认的 `yt-dlp 字幕优先` 会按这个顺序生成转录：

1. 先用 `yt-dlp` 下载当前视频可确认的人工 CC 字幕。
2. 再用 `yt-dlp` 下载当前视频可确认的 B站 AI 字幕。
3. 如果没有可用字幕，下载当前视频音频并使用本地 ASR。

`BILIBILI_PREFER_WEB_SUBTITLE` 默认是 `false`。不建议日常打开它，因为 B站网页播放器字幕接口偶尔会返回与当前 BV 不匹配的字幕，可能造成“标题是这个视频，正文却是另一个视频”的笔记。

只有在 `yt-dlp` 字幕不可用、并且你确认网页播放器字幕稳定时，才在 `worker/env.local` 里临时开启：

```bash
BILIBILI_PREFER_WEB_SUBTITLE="true"
```

如果发现已生成笔记内容和标题不匹配，先把这个值改回 `false`，删除那篇错误笔记后重新运行该 B站链接。

批量处理时，脚本会为每个 B站视频创建独立临时目录：

```text
worker/cache/audio/bilibili_work_<BV号>_<进程ID>_<随机数>/
```

音频下载、16kHz wav 转换、字幕文件和 ASR 中间结果都放在这个目录下。任务结束后会自动清理，避免多个视频复用同一个 `bilibili_audio.mp3` 导致串台。

本地音视频目录批量处理也会为每个文件创建独立的 `local_work_<进程ID>_<序号>_<随机数>/` 临时目录。

### 本地 ASR 模型怎么配置

B站任务会优先找 B站/yt-dlp 字幕。若视频没有可用字幕，就会回落到本地 ASR 语音转文字。

默认配置是：

```bash
ASR_ENGINE="whisper"
ASR_LOCAL_MODEL=""
```

`ASR_ENGINE=whisper` 时，必须把 `ASR_LOCAL_MODEL` 指向本机已有的 Whisper 模型目录，例如：

```bash
ASR_ENGINE="whisper"
ASR_LOCAL_MODEL="/Users/xxx/Models/whisper-large-v3-turbo"
```

如果这个路径为空，带字幕的视频仍可能成功；但没有字幕的视频会在下载音频后失败，并提示需要设置 `ASR_LOCAL_MODEL`。

也可以使用 Qwen3-ASR：

```bash
ASR_ENGINE="qwen3"
```

这种方式需要当前 conda 环境安装 `qwen-asr`，首次运行可能需要下载模型权重。若你已经有本地 Qwen3-ASR 模型，也可以继续设置 `ASR_LOCAL_MODEL` 指向该模型目录。

### 第二步：默认输出路径

填写“输出根目录”，建议使用 Obsidian Vault 或长期笔记目录，例如：

```text
/Users/xxx/Notes
```

应用会按任务类型带出一个常用的“本次输出目录”，你可以直接修改。所有任务都会以界面上的“本次输出目录”为最终写入位置，脚本不会再额外追加月份目录或 `local` 子目录。

| 任务 | 建议目录 |
| --- | --- |
| B站单链接 | `Net/BiliBili` |
| B站收藏夹/系列 | `Net/BiliBili` |
| 微信公众号/网页 | `Net/WeChat` |
| Word/PDF整理 | `Inbox` |
| 论文速读 | `AI/_quickread/AI_paper` |
| 本地视频/音频 | `Net/BiliBili` |

点击“保存配置”后，界面配置会保存到本机 `localStorage`。

“运行环境”区域里的 `API Key` 默认是星号隐藏，可以点击右侧眼睛按钮临时显示；`B站 Cookie 文件` 下方会显示当前 cookie 的状态提示。

### 第三步：任务执行

选择任务类型，填写输入源，然后先点“预览命令”。

确认命令没问题后，再点“运行任务”。

路径输入框支持两种方式：可以直接粘贴路径，也可以在桌面应用里点击“选择”“文件”“目录”按钮打开系统选择器。普通浏览器预览环境无法读取本机路径选择器，只能手动输入。

任务运行时，日志会实时追加到“日志”区域。长任务运行中可以点击“取消任务”；取消会尝试停止当前 Python worker。某些由外部工具启动的子进程可能需要几秒钟才完全退出。

## 5. 各任务怎么填

### B站单链接

输入源填写：

```text
https://www.bilibili.com/video/BVxxxx/
```

输出文件会直接写入“本次输出目录”。如果希望按月份归档，请直接把“本次输出目录”填成类似 `/Users/xxx/Notes/Net/BiliBili/2026-06` 的路径。

如果勾选“关键帧图文笔记”，任务完成后会额外：

- 抽取少量代表性关键帧
- 保存到当前笔记目录下的 `assets/`
- 在 Markdown 中插入 `## 关键帧图文笔记`

视频笔记会按更通用的结构生成：

- `## 一句话概括`
- `## 速读摘要`
- `## 思维导图`
- `## 结构化正文`
- `## 金句/重要原话`
- `## 可复习清单`
- `## 术语与概念`
- `## 校对正文`
- `## 原始字幕` 或折叠的原始字幕块

其中 `结构化正文` 是主笔记，`校对正文` 放在正文类内容之后，`原始字幕` 放在最后。默认会保留原始字幕；如果不希望笔记末尾附带完整字幕，可以取消勾选“保留原始字幕”。取消后，Qwen 仍会用字幕完成整理和校对，但最终 Markdown 不保留原始字幕块。

如果勾选“A股术语校验”，Qwen 在整理和校对时会尽量保留股票名称/代码，并在结果里附带 `## A股术语校验` 小节。

### B站收藏夹/系列

输入源可留空。需要先在 `worker/env.local` 配置：

```bash
BILIBILI_FAV_MEDIA_ID="收藏夹ID"
BILIBILI_COOKIES_FILE="/path/to/bili_cookies.txt"
BILI_COOKIE_FILE="/path/to/bili_cookies.txt"
```

界面会显示“收藏夹测试数量”，默认 `1`，用于只处理收藏夹里的第一条新视频做验证。确认流程稳定后，可以改成一个小批量数字；填 `0` 表示不限制数量，会运行完整收藏夹增量处理。

这个功能目前仍依赖 `BILIBILI_FAV_MEDIA_ID` 和 cookie。如果收藏夹登录、cookie 或字幕抓取失败，优先用 B站单链接验证环境。

### 微信公众号/网页

输入源填写网页 URL，例如：

```text
https://mp.weixin.qq.com/s/...
```

输出目录通常为：

```text
/Users/xxx/Notes/Net/WeChat
```

运行时会先调用 `convert_sources_to_md.py` 抽取正文和图片资产，再自动调用 `qwen_organize_notes.py` 对刚生成的 Markdown 做 Qwen 整理。整理结果会写回同一个输出目录，并回写 `indexes/source-manifest.json` 的 `organized_*` 字段。

整理后的文件会在上方插入 `## Qwen 整理`，末尾保留完整 `## 原文抽取`，也就是“在原文基础上插入整理”，方便回看原文和核对模型摘要。

### Word/PDF整理

输入源填写本地文件路径：

```text
/path/to/file.doc
/path/to/file.docx
/path/to/file.pdf
/path/to/file.pptx
/path/to/file.xlsx
/path/to/file.csv
/path/to/file.tsv
/path/to/file.html
/path/to/file.png
```

旧版 `.doc` 会先通过 macOS 自带 `textutil` 转成临时 `.docx`，再进入 Markdown 抽取流程；如果转换结果层级或表格不理想，建议用 Word/WPS 另存为 `.docx` 后再运行一次。

虽然当前任务名称还是“Word/PDF整理”，但这一项现在已经覆盖：

- Word：`.doc` / `.docx`
- PDF：普通 PDF、扫描版 PDF（勾选“启用 OCR”时）
- 表格：`.csv` / `.tsv` / `.xlsx`
- 演示文稿：`.pptx`
- 本地网页：`.html` / `.htm`
- 图片：`.png` / `.jpg` / `.jpeg` / `.webp` / `.heic` / `.bmp` / `.gif` / `.tif` / `.tiff`

运行时会先生成转换草稿，再自动调用 `qwen_organize_notes.py` 做正式整理。整理后的文件会在上方插入 `## Qwen 整理`，末尾保留完整 `## 原文抽取`，也就是“在原文基础上插入整理”，方便回看原文和核对模型摘要。

如果输入的是图片，程序会直接调用当前多模态 Qwen 做 OCR。
如果输入的是扫描版 PDF，勾选“启用 OCR”后，程序会优先把 PDF 页图交给当前多模态 Qwen 做识别。

输出目录通常为：

```text
/Users/xxx/Notes/Inbox
```

### 论文速读

输入源填写论文 PDF 路径：

```text
/path/to/paper.pdf
```

需要本地 LLM API 可用。输出目录通常为：

```text
/Users/xxx/Notes/AI/_quickread/AI_paper
```

论文速读默认最多向模型提供约 128k 字符的 PDF 抽取文本，适配 128k 上下文模型。需要完整不截断时，可以在 `worker/env.local` 中设置 `QWEN_QUICKREAD_MAX_CHARS="0"`。速读会要求模型在文件末尾输出 `## 全文翻译`；如果首次速读结果缺少这个章节，程序会自动发起一次“全文翻译”补跑并追加到文件末尾。

### 本地视频/音频

输入源可以是单个媒体文件：

```text
/path/to/video.mp4
```

也可以是目录：

```text
/path/to/videos
```

需要 `ffmpeg`、`yt-dlp` 和 ASR 相关依赖可用。

本地视频/音频使用和 B站单链接相同的笔记结构，也支持“关键帧图文笔记”和“保留原始字幕”。如果媒体文件旁边已有同名 `.srt`，可以选择“同目录 SRT 字幕优先”；如果字幕质量差，可以切换为“ASR 语音转写优先”。

## 6. 常见问题

### 点“检查依赖”提示浏览器预览

说明你打开的是普通浏览器页面，不是 Tauri 桌面窗口。

请运行：

```bash
npm run tauri:dev
```

然后在弹出的 Local Note Studio 桌面窗口里操作。

### Tauri 一直等待 frontend dev server

先按 `Ctrl+C` 停掉命令，再重新运行：

```bash
npm run tauri:dev
```

如果仍然卡住，检查是否有旧的 Vite 进程占用了 `1420` 端口。

### 检查依赖显示 Python 版本不对

MVP 建议 Python 3.10 或 3.11。优先选择已经安装好依赖的 conda 环境，例如：

```text
course-whisper
```

### B站任务失败

按顺序检查：

1. `yt-dlp` 是否可用
2. `ffmpeg` 是否可用
3. cookie 文件路径是否正确
4. 单个 B站链接是否能先跑通
5. LLM API 是否启动

### 论文速读失败

按顺序检查：

1. PDF 路径是否存在
2. `pypdf` 是否安装
3. LLM API Base 是否可访问
4. 模型名是否和 LM Studio 中加载的模型一致

## 7. 推荐日常流程

每次使用时按这个顺序：

1. 启动 LM Studio 或其他 OpenAI-compatible LLM 服务。
2. 运行 `npm run tauri:dev`。
3. 在 Tauri 桌面窗口点击“检查依赖”。
4. 填写输出根目录。
5. 选择任务并填写输入源。
6. 点击“预览命令”。
7. 确认无误后点击“运行任务”。
8. 到输出目录查看 Markdown 文件。
