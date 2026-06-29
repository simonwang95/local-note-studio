# 中文操作手册

这份文档面向 Local Note Studio 的日常使用。目标是先把 Mac 桌面应用跑起来，再用它检查环境、预览命令、执行笔记整理任务。

## 1. 先分清三种启动方式

### 推荐给其他 Mac 测试：DMG 安装包

同架构的测试 Mac 只需要对应的 DMG，不需要项目源码、Node.js、Rust、Xcode、Homebrew 或 conda。`aarch64` 安装包只适用于 Apple Silicon，`x86_64` 适用于 Intel；当前开发机构建的是 `aarch64`。

安装包包含 App 和 Python worker，但不会捆绑个人配置、Cookie、本地索引、LLM 服务或内嵌大型 ASR 模型。第一次启动后请：

1. 把 App 从 DMG 拖入“应用程序”。
2. 在“配置”选择“应用托管环境”，填写该测试 Mac 能访问的 LLM API、模型和输出根目录。
3. 点击“安装/修复”，联网安装校验过的 Python、依赖、Whisper 运行库、默认 Whisper ASR 模型、媒体工具和 Pandoc。安装过程中日志会实时显示当前阶段。
4. 到“校验”运行“检查依赖”，再执行一个小任务。

从旧版本升级后，如果“查看状态”出现 `状态：需要修复` 或 `[MISSING] pandoc`，直接回到“配置”点击“安装/修复”补齐新增托管组件；不需要为托管环境另外执行 `brew install pandoc`。

如果“安装/修复”在 `pypi.org` 处出现 `SSLEOFError`、证书、代理或超时错误，通常是测试网络把 PyPI/TLS 链路截断，不代表 `pypdf` 等锁定版本不存在。0.1.6 起，应用会用相同的锁定版本自动重试备用 PyPI 镜像。特殊网络仍失败时，可以临时从终端指定可访问镜像再启动 App：

```bash
LOCAL_NOTE_STUDIO_PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple" \
  open -a "Local Note Studio"
```

如果失败发生在更早的 “Python 运行时下载” 阶段，并看到 `curl: (16) Error in the HTTP2 framing layer`，通常是 GitHub Release 下载链路的 HTTP/2 被网络设备或代理截断。0.1.7 起，应用会优先用 HTTP/1.1 下载运行时和工具，并关闭无意义的百分比刷屏；如果测试网络必须使用镜像，可以指定同一个 Python 运行时压缩包的镜像 URL：

```bash
LOCAL_NOTE_STUDIO_PYTHON_RUNTIME_URL="https://你的镜像/cpython-3.11.15+20260610-aarch64-apple-darwin-install_only_stripped.tar.gz" \
  open -a "Local Note Studio"
```

如果失败发生在 `pandoc 下载失败`，它只影响“目录导出 EPUB”。0.1.12 起，应用会继续完成托管环境初始化；B站、文档、OCR、Cookie、ASR、普通视频任务仍可使用，状态页会保留 `[MISSING] pandoc` / “需要修复”提醒。网络恢复后再点一次“安装/修复”即可补齐。特殊网络也可以单独指定工具压缩包镜像：

```bash
LOCAL_NOTE_STUDIO_PANDOC_URL="https://你的镜像/pandoc.zip" \
  open -a "Local Note Studio"
```

`ffmpeg` / `ffprobe` 工具包也支持相同模式，变量名分别是 `LOCAL_NOTE_STUDIO_FFMPEG_URL` 和 `LOCAL_NOTE_STUDIO_FFPROBE_URL`。这些工具镜像仍会执行 SHA-256 校验，因此镜像内容必须与应用期望的原始压缩包完全一致。

全新安装默认使用托管环境。若用户主动切换到“现有 Conda / Python（高级）”，所选后端、环境名和 Conda 可执行文件路径会保存在这台 Mac，后续启动继续使用，不会自动切回托管环境。

从 Finder 启动的 App 不会继承终端 shell 的完整 `PATH`。应用会自动查找 `~/miniforge3`、`~/miniconda3`、`~/anaconda3`、Homebrew 等常见位置；如果 Conda 安装在自定义目录，请在配置页填写完整路径，例如：

```text
/Users/xxx/miniforge3/bin/conda
```

内部测试包暂未签名和公证。应先通过可信渠道核对开发者提供的 SHA-256；然后优先使用右键/Control-click →“打开”。如果 Gatekeeper 仍拦截已核对的内部包，可仅对该测试 App 执行：

```bash
xattr -dr com.apple.quarantine "/Applications/Local Note Studio.app"
```

正式公开分发不能依赖这个操作，必须完成 Developer ID 签名和 Apple 公证。更完整的交付边界见 [`release-macos.md`](release-macos.md)。

升级内部测试包时，先退出 App，再把旧的 `/Applications/Local Note Studio.app` 拖入废纸篓并从新 DMG 拖入，或直接覆盖替换。只替换 `.app` 会保留 Application Support 中的托管环境、索引和恢复状态，也会保留界面配置；普通升级不要删除这些数据目录。

### 源码开发：桌面应用模式

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

如果你在 Chrome 里看到 `localhost:1420` 或 `localhost:5173`，那通常只是网页预览，不代表 Tauri worker 可用。请以 Tauri 桌面窗口为准。当前项目的 `npm run dev` 会通过清理包装器启动 Vite，父进程退出时会一并停止；如果活动监视器里看到旧版本遗留的 `node .../node_modules/.bin/vite` 长时间占用 CPU，可先运行：

```bash
npm run dev:stop
```

或在活动监视器里退出该 `node` / `npm run dev` 进程。活动监视器显示的“虚拟内存”通常是 Node/V8 预留地址空间，不等于真实内存占用；判断异常时优先看 CPU 和“实际内存大小”。

## 2. 源码开发模式第一次启动前准备

使用 DMG 的测试者跳过本节；下面的 `npm install` 和 `worker/env.local` 只用于源码开发。

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

#### 推荐：在桌面 App 内刷新

这一操作必须在 `npm run tauri:dev` 启动的桌面窗口中完成；普通浏览器预览页不能读取 Chrome Profile 或运行 Cookie 导出脚本。

1. 在 Chrome 中登录 B站，并确认当前账号能打开目标收藏夹、充电视频或充电动态。
2. 在这个已登录 B站的 Chrome 窗口打开 `chrome://version/`，找到并复制“个人资料路径”。路径末尾通常是 `Default`、`Profile 1` 或 `Profile 2`。
3. 启动 Local Note Studio 桌面应用。“B站 Cookie 文件”建议留空，刷新后会安全保存到应用自己的 Application Support 目录；只有确实需要时才填写其他绝对路径。
4. 在“Chrome 个人资料路径”右侧点击“选择”，选择上一步看到的完整 Profile 目录。例如：

```text
/Users/xxx/Library/Application Support/Google/Chrome/Default
```

5. 点击“授权并刷新 Cookie”。应用会先说明授权范围；随后 macOS 可能询问“访问其他 App 的数据”，并弹出 Chrome Safe Storage/钥匙串确认。这两项与读取所选 Profile 有关。刷新 Cookie 不需要“桌面”“文稿”“下载”“Apple Music/媒体资料库”“网络宗卷”或“可移动宗卷”权限，出现时一律拒绝；0.1.3 已修复空输出目录误触发全目录扫描的问题。
6. 查看下方日志。成功时应依次看到 Cookie 提取、B站域筛选、登录态校验和保存路径，最后包含类似信息：

```text
登录态校验通过: 已登录 mid=...
[OK] Bilibili cookie file - ...；已登录 mid=...
```

刷新过程只保留 `.bilibili.com` 和 `.bilibili.cn` Cookie，不会把其他网站 Cookie 写入文件，也不会访问具体视频。只有出现 `已登录 mid=...`，才表示这份文件可以继续用于收藏夹、充电视频和充电动态的权限验证。

应用只接受直接包含 `Cookies` 或 `Network/Cookies` 的末级 Profile。若误选用户主目录、`Google/Chrome` 根目录或其他宽泛目录，会在调用 yt-dlp 前直接拒绝，不再递归扫描文稿、下载或外接磁盘。

“B站 Cookie 文件”和“Chrome 个人资料路径”默认以密码形式隐藏，点击右侧眼睛按钮可以临时显示或再次隐藏。输入内容会保存在 App 的本地配置中，不必再重复写入 `worker/env.local`。如果 Cookie 文件留空，Cookie 刷新、依赖检查、目标权限验证和任务运行都会优先使用应用数据目录里的默认 Cookie 文件。点击“检查依赖”可以随时重新检查 Cookie 文件和登录态；详细状态只显示在日志中。

如果刷新失败，按日志依次检查：

- `Chrome 个人资料路径` 是否精确指向 `Default` / `Profile N` 目录，而不是它们的上级目录。
- 当前 Profile 中是否已经登录 B站，以及登录账号是否真的拥有目标内容权限。
- 若使用“现有 Conda / Python（高级）”，确认所选 conda 环境安装了 `yt-dlp`；缺失时运行 `conda run -n course-whisper python3 -m pip install -U yt-dlp`。应用托管环境不会再调用 conda，缺组件时请回到“配置”点击“安装/修复”。
- macOS 是否拒绝了 Chrome Safe Storage/钥匙串访问。

#### 备用：使用命令行导出

桌面刷新不可用时，可以在项目根目录运行专用脚本。它与 App 使用相同的筛选和登录态校验逻辑：

```bash
conda run --no-capture-output -n course-whisper \
  python3 worker/scripts/export_bilibili_cookies.py \
  --browser chrome \
  --profile "/Users/xxx/Library/Application Support/Google/Chrome/Default" \
  --output ./bili_cookies.txt
```

也可以把 Cookie 保存到本机私有目录，例如：

```text
/Users/xxx/.local/share/local-note-studio/bili_cookies.txt
```

仅在主要使用命令行而不是桌面 App 时，才需要在 `worker/env.local` 中填写路径：

```bash
BILIBILI_COOKIES_FILE="/Users/xxx/.local/share/local-note-studio/bili_cookies.txt"
BILI_COOKIE_FILE="/Users/xxx/.local/share/local-note-studio/bili_cookies.txt"
```

如果使用项目根目录的 `bili_cookies.txt`，可以写成：

```bash
BILIBILI_COOKIES_FILE="./bili_cookies.txt"
BILI_COOKIE_FILE="./bili_cookies.txt"
```

#### 遇到 HTTP 412 怎么办

旧命令把“导出 Cookie”和“读取视频元数据”绑在一起，例如：

```bash
yt-dlp --cookies-from-browser chrome --cookies ./bili_cookies.txt \
  --skip-download --print title "https://www.bilibili.com/video/BV.../"
```

其中 `HTTP Error 412: Precondition Failed` 表示 B站拦截了 yt-dlp 的视频元数据请求，不等于账号没有充电权限。该命令还可能把浏览器中其他站点的 Cookie 一并写入文件，因此不再推荐。请改用上面的 `export_bilibili_cookies.py`；导出成功后，再回到应用里单独运行目标任务。

备选方式：安装可信的 `cookies.txt` 导出工具或扩展，选择只导出 `bilibili.com` / `.bilibili.com` 相关 cookie，并保存为 Netscape `cookies.txt` 格式。

注意事项：

- 只导出 B站域名相关 cookie，不要导出全部网站 cookie。
- Cookie 等同于一段时间内的登录凭证，不要提交到 git，不要发给别人。
- 如果 B站退出登录、修改密码或 cookie 过期，需要重新导出。
- `worker/env.local` 已被 `.gitignore` 忽略；命令行工作流可把真实 Cookie 路径写在这里，桌面 App 则使用本地保存的界面配置。
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

- `运行时后端`：安装版默认“应用托管环境”；首次使用先点击“安装/修复”
- `Conda 环境`：仅高级后端使用，预填 `course-whisper`，可改为自己的环境名
- `Conda 可执行文件`：通常留空自动查找；自定义安装位置请填写绝对路径
- `Python 命令`：仅高级后端且不使用 Conda 环境时使用，默认 `python3`
- `LLM API Base`：默认 `http://127.0.0.1:1234/v1`
- `API Key`：LM Studio 可用 `lm-studio`
- `模型`：默认 `qwen3.6-35b-a3b-nvfp4`
- `ASR 模型目录`：选择后会自动保存在本机，也可点击旁边的“保存配置”；默认隐藏，眼睛按钮可临时显示。历史任务重新运行不会再用旧的空路径覆盖当前配置
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
- `mlx-whisper`（托管环境会安装；高级 Conda/Python 后端可按需安装）
- 可选：`opencc`
- 可选：`textutil`，macOS 通常自带；转换旧版 `.doc` 文件时需要
- 可选：`tesseract` 和 `pdftoppm`，当你希望在没有多模态 Qwen 时做图片 / 扫描 PDF OCR 备用
- `ASR_LOCAL_MODEL`：托管环境会在安装/修复后自动填入默认模型路径；高级 Conda/Python 后端需自行选择

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

`ASR_ENGINE=whisper` 时，`ASR_LOCAL_MODEL` 必须指向本机已有的 Whisper 模型目录。使用托管环境时，“安装/修复”会下载默认模型并自动填入配置页；也可以手动选择其他模型目录，例如：

```bash
ASR_ENGINE="whisper"
ASR_LOCAL_MODEL="/Users/xxx/Models/whisper-large-v3-turbo"
```

如果这个路径为空，带字幕的视频仍可能成功；但主动选择“ASR 语音转写优先”时，应用会在下载音频前提示先选择模型目录或点击“安装/修复”下载默认模型。

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
| B站动态/充电动态 | `Net/BiliBili` |
| B站 UP 主图文批量 | `Net/BiliBili` |
| 微信公众号/网页 | `Net/WeChat` |
| Word/PDF整理 | `Inbox` |
| AI-Chat JSON | `AI/AI-Chat` |
| 论文速读 | `AI/_quickread/AI_paper` |
| 本地视频/音频 | `Net/BiliBili` |
| 目录导出 EPUB | `Exports/EPUB` |

点击“保存配置”后，界面配置会保存到本机 `localStorage`。

“运行环境”区域里的 `API Key` 默认是星号隐藏，可以点击右侧眼睛按钮临时显示；`B站 Cookie 文件` 下方会显示当前 cookie 的状态提示。

### 第三步：任务执行

选择任务类型，填写输入源，然后先点“预览命令”。

确认命令没问题后，再点“运行任务”。

路径输入框支持两种方式：可以直接粘贴路径，也可以在桌面应用里点击“选择”“文件”“目录”按钮打开系统选择器。普通浏览器预览环境无法读取本机路径选择器，只能手动输入。

任务运行时，日志会实时追加到“日志”区域。长任务运行中可以点击“取消任务”；取消会停止当前 Python worker 所在进程组，尽量连同 `ffmpeg`、`yt-dlp` 等外部工具一起退出。直接退出 App 时也会先尝试清理正在运行的 worker 子进程。

默认不会覆盖同名输出文件。需要重跑并替换已有 Markdown 时，勾选“覆盖同名文件”。

如不希望本次任务读取或更新 Manifest、B站已处理列表等增量记录，可勾选“隐身模式”。隐身模式仍会生成笔记，并保留“任务历史与恢复”中的本地历史；它只关闭 source/video/quickread Manifest、关键帧 Manifest、B站已处理列表、转录报告和批量失败状态。由于“只重试失败项”依赖批量失败状态，因此不能与隐身模式同时使用。

“输出文件名（可选）”只建议用于单个文件、单个 URL 或单个目录导出任务。留空时程序保持默认命名；填写时可以省略 `.md` 或 `.epub` 后缀。文件名不能包含目录分隔符，目录批量视频任务不支持自定义同一个输出名，避免多个视频写到同一个文件。

如果任务会生成图片资产，例如 Word 图片、网页图片、B站动态图片、图片 OCR 或关键帧，资产目录会跟随最终 Markdown 文件名生成在当前输出目录的 `assets/<文件名>/` 下。只要同步移动 Markdown 文件和同级 `assets` 目录，相对图片链接仍可解析。

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

当前关键帧保存为 JPEG，截图命令不做额外缩放。本地视频会按源视频分辨率截图；B站视频为了节省下载和处理时间，会临时下载最高约 480p 的视频，因此关键帧通常不超过 480p。

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

如果视频属于访谈、问答或多人讨论，可以勾选“对话检测与角色标注”。程序会先用 Qwen 判断内容是否具有多人轮流发言特征；确认是对话后，在校对正文中尽量标注主持人、嘉宾或“说话人 A/B”。该选项会增加一次模型调用，普通单人讲解建议保持关闭。

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

### B站动态/充电动态

输入源填写 B站动态 URL，例如：

```text
https://www.bilibili.com/opus/1214533678103789602
```

这个任务会使用“运行环境”里的 `B站 Cookie 文件` 请求 B站动态接口，而不是普通网页抽取。账号确实有权限时，可以读取动态正文和图片；如果 cookie 未配置、已过期，或当前账号无权查看充电内容，任务会报错提示权限问题，而不会只保存“充电可见”。

如果日志显示“B站接口返回权限占位”，说明接口本身没有返回正文。日志里会包含 B站返回的解锁提示和档位，例如“168元充电”。这时需要确认导出 cookie 的浏览器账号已经加入对应 UP 主的对应充电档位；仅登录 B站账号还不够。

### B站 UP 主图文批量

输入 UP 主空间的图文页链接或纯数字 UID，例如：

```text
https://space.bilibili.com/1420210197/upload/opus
```

程序会使用当前 B站 Cookie 分页读取空间动态，只保留图文 `opus`，自动排除视频动态，然后逐篇：

- 抽取标题、作者、发布时间和完整正文
- 将动态图片下载到当前输出目录的相对 `assets/` 目录
- 调用 Qwen 生成结构化整理，并在文末保留完整原文和图片
- 写入来源链接和动态 ID，便于回查

“图文处理数量”默认是 `1`，适合先验证一篇；填写一个正整数会处理最新的对应数量，填写 `0` 会持续翻页直到处理完该账号当前可读取的全部图文。批量任务不支持统一自定义文件名，避免多篇内容写入同一个文件。

默认不覆盖同名文件；再次运行时，已有同名输出会直接跳过。默认文件名包含动态 ID，即使标题相同也不会互相覆盖。需要重新抓取和整理时勾选“覆盖同名文件”。充电专属图文仍取决于当前 Cookie 对对应档位的访问权限。

批量日志分成两个阶段：`[抓取 i/n]` 显示动态正文和图片的获取进度，`[整理 i/n]` 显示完整性检查、Qwen 分块、合并和写入进度。多篇连续调用 Qwen 时，日志每 10 秒显示一次冷却剩余时间；如果下一篇已经存在完整笔记，会立即跳过，不会先等待冷却。

“长任务参数覆盖 → 模型冷却（秒）”的规则如下：留空时使用 `env.local` 或程序稳定默认值；填写 `0` 时禁用等待；填写正数时会覆盖 UP 图文整理实际使用的 `QWEN_ORGANIZE_COOLDOWN_DELAY`。冷却只发生在两次真实 Qwen 整理调用之间，因此第一篇之前、最后一篇之后以及跳过已有完整笔记时都不会等待。任务开始日志会打印本次覆盖值，便于确认配置已生效。

“预览命令”只显示两阶段流程，其中 `<converted-markdown-path>` 是占位符，不会执行抓取或 Qwen。请点击“运行任务”开始正式处理。正式运行时，转换草稿只写入系统临时目录；Qwen 成功后才会把正式 Markdown 和图片资产写入“本次输出目录”，最终笔记不会包含已删除的临时草稿路径。正式笔记统一按“标题 → 来源追溯 → Qwen 整理 → 完整原文”的顺序输出。

如果输出目录里存在旧版生成的 `NOTE-*` 正式笔记和对应 `BILI-OPUS-*` 草稿，重新运行该 UP 主任务时会先按 `source_url` 将草稿中的完整原文补到旧正式笔记末尾，再把旧草稿移动到 `.local-note-studio-legacy-drafts/` 隐藏备份目录，不会直接删除历史内容。

输出目录通常为：

```text
/Users/xxx/Notes/Net/BiliBili
```

### AI-Chat JSON

输入源填写 LM Studio 导出的 `.conversation.json` 文件，例如：

```text
/path/to/example.conversation.json
```

输出目录通常为：

```text
/Users/xxx/Notes/AI/AI-Chat
```

这一任务会先把对话 JSON 转成 Markdown，保留来源信息、模型信息、消息数和完整对话正文，然后自动调用 Qwen 整理。整理后的文件会在上方插入 `## Qwen 整理`，末尾保留完整对话正文，适合把可复用结论沉淀成长期笔记。

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

### 目录导出 EPUB

输入源填写一个 Markdown 笔记目录：

```text
/Users/xxx/Notes/SomeFolder
```

输出目录通常为：

```text
/Users/xxx/Notes/Exports/EPUB
```

这个任务会递归收集输入目录下的 `.md` 文件，按路径排序后调用 `pandoc` 合并导出为一个 EPUB。它会跳过 `.git`、`.obsidian`、`indexes`、`cache` 等内部目录，并尝试通过 Markdown 相对路径打包图片资源。

托管环境会在“安装/修复”阶段安装 `pandoc`；使用托管环境时无需再通过 Homebrew 或 Conda 单独安装。如果安装期因为 GitHub/CDN 网络问题暂时缺少 pandoc，其他任务仍可继续使用，但 EPUB 导出会提示先回到“配置”执行“安装/修复”。高级 Conda/Python 后端仍需自行安装：

```bash
brew install pandoc
```

或安装到当前 conda 环境：

```bash
conda install -n course-whisper -c conda-forge pandoc
```

如果想指定 EPUB 文件名，可以填写“输出文件名（可选）”，例如 `青枫课程笔记.epub`。

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

本地媒体时长由 `ffprobe` 直接读取，统一显示为“X分Y秒”或“X小时Y分Z秒”。旧版本生成的笔记如果仍显示“未知”，可以只修复时长而不重跑转录和 Qwen：

```bash
conda run --no-capture-output -n course-whisper \
  python3 worker/scripts/run_bilibili_transcript.py \
  --repair-local-durations "/Users/xxx/Notes/LocalVideos"
```

目录输入默认只扫描当前目录。需要扫描子目录时，勾选“递归扫描目录”。

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

## 8. 当前限制与后续计划

当前版本已完成 P0 稳定性基线和 P1 日常使用能力，包括收藏夹/系列、受限内容诊断、任务历史与恢复、输出快捷操作、OCR 续跑、Manifest 状态、隐身模式和应用托管运行环境。签名、公证与独立干净 Mac 验收仍是发布门槛。完整优先级和验收标准见 [`docs/todo.md`](todo.md)。
