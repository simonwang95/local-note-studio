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

只有 Tauri 桌面窗口能调用 Rust `run_worker` 命令，从而运行 Python worker、检查依赖、预览命令和执行任务。

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

推荐方式是导出 Netscape `cookies.txt` 格式文件：

1. 在 Chrome 或其他浏览器里登录 B站。
2. 安装一个可信的 `cookies.txt` 导出工具或扩展，选择只导出 `bilibili.com` / `.bilibili.com` 相关 cookie。
3. 保存为本机文件，例如：

```text
/Users/xxx/.local/share/local-note-studio/bili_cookies.txt
```

4. 在 `worker/env.local` 中填写绝对路径：

```bash
BILIBILI_COOKIES_FILE="/Users/xxx/.local/share/local-note-studio/bili_cookies.txt"
BILI_COOKIE_FILE="/Users/xxx/.local/share/local-note-studio/bili_cookies.txt"
```

5. 在应用界面的“B站 Cookie 文件”输入框中也可以填写同一个路径。

注意事项：

- 只导出 B站域名相关 cookie，不要导出全部网站 cookie。
- Cookie 等同于一段时间内的登录凭证，不要提交到 git，不要发给别人。
- 如果 B站退出登录、修改密码或 cookie 过期，需要重新导出。
- `worker/env.local` 已被 `.gitignore` 忽略，真实 cookie 路径应放在这里。

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

### 第二步：默认输出路径

填写“输出根目录”，建议使用 Obsidian Vault 或长期笔记目录，例如：

```text
/Users/xxx/Notes
```

应用会按任务类型自动派生本次输出目录：

| 任务 | 自动派生目录 |
| --- | --- |
| B站单链接 | `Net/BiliBili` |
| B站收藏夹/系列 | `Net/BiliBili` |
| 微信公众号/网页 | `Net/WeChat` |
| Word/PDF整理 | `Inbox` |
| 论文速读 | `AI/_quickread/AI_paper` |
| 本地视频/音频 | `Net/BiliBili` |

点击“保存配置”后，界面配置会保存到本机 `localStorage`。

### 第三步：任务执行

选择任务类型，填写输入源，然后先点“预览命令”。

确认命令没问题后，再点“运行任务”。

## 5. 各任务怎么填

### B站单链接

输入源填写：

```text
https://www.bilibili.com/video/BVxxxx/
```

输出目录作为 B 站笔记根目录。迁移脚本会继续追加月份子目录。

### B站收藏夹/系列

输入源可留空。需要先在 `worker/env.local` 配置：

```bash
BILIBILI_FAV_MEDIA_ID="收藏夹ID"
BILIBILI_COOKIES_FILE="/path/to/bili_cookies.txt"
BILI_COOKIE_FILE="/path/to/bili_cookies.txt"
```

这个功能目前属于 MVP 脚手架阶段。如果收藏夹登录、cookie 或字幕抓取失败，优先用 B站单链接验证环境。

### 微信公众号/网页

输入源填写网页 URL，例如：

```text
https://mp.weixin.qq.com/s/...
```

输出目录通常为：

```text
/Users/xxx/Notes/Net/WeChat
```

### Word/PDF整理

输入源填写本地文件路径：

```text
/path/to/file.docx
/path/to/file.pdf
```

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
