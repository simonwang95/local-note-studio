# Environment Setup

## Runtime Selection

Packaged builds default to the app-managed runtime. It is initialized from the desktop UI and is the recommended mode for testers because it does not require a separately installed Conda or Homebrew environment.

An existing Conda/Python environment remains available as an advanced backend for development and compatible user-managed setups. Once a user explicitly selects this backend, the backend, environment name, Python command, and optional Conda executable path persist across launches. Legacy settings without an explicit runtime preference migrate to the managed backend once.

Finder-launched apps do not inherit an interactive shell's complete `PATH`. Local Note Studio therefore searches common Miniforge, Miniconda, Anaconda, Homebrew, and system locations. For a non-standard install, configure an absolute executable path such as `/Users/xxx/miniforge3/bin/conda` in the app.

The current known-good environment is:

```bash
CONDA_ENV="course-whisper"
```

## Local Path Variables

Machine-specific paths belong in `worker/env.local`, not in committed source code or docs. Use placeholder paths in examples, such as `/Users/xxx/Notes`.

Recommended local-only variables:

```bash
LOCAL_NOTE_STUDIO_PROJECT_DIR="/Users/xxx/Files/Agent/local-note-studio"
KNOWLEDGE_BASE_PROJECT_DIR="/Users/xxx/Files/Agent/knowledge-base"
CODEX_PYTHON_BIN="python3"
DEFAULT_OUTPUT_ROOT="/Users/xxx/Notes"
NOTES_DIR="/Users/xxx/Notes"
OBSIDIAN_VAULT_DIR="/Users/xxx/Notes"
```

`worker/env.local` is ignored by git and can contain real user paths. `worker/env.example` should keep only portable defaults or anonymized placeholders.

## Required Tools

The selected environment should provide:

- Python 3.10 or 3.11.
- `yt-dlp`.
- `ffmpeg` available on `PATH`.
- Whisper runtime used by the migrated scripts.
- Python packages used by source conversion, including `pypdf` and `lxml`.
- Optional `opencc` support for traditional-to-simplified conversion.
- Optional `tesseract` and `pdftoppm` as OCR fallback tools.

## Existing Environment Check

```bash
conda run -n course-whisper python3 --version
conda run -n course-whisper python3 -c "import pypdf, lxml"
conda run -n course-whisper yt-dlp --version
ffmpeg -version
```

## LLM API

The app expects an OpenAI-compatible API:

```bash
DEFAULT_LLM_API_BASE="http://127.0.0.1:1234/v1"
DEFAULT_LLM_API_KEY="lm-studio"
DEFAULT_LLM_MODEL="qwen3.6-35b-a3b-nvfp4"
```

For Bilibili summary/proofread workflows, the worker also maps these values to:

```bash
SUMMARY_API_URL
SUMMARY_API_KEY
SUMMARY_MODEL
```

Video task options selected in the UI override the corresponding process environment for that run. Important examples include `EXTRACT_KEYFRAMES`, `ENABLE_DIALOGUE_DETECTION`, `KEEP_ORIGINAL_SUBTITLES`, `FORCE_ASR`, `OVERWRITE_OUTPUT`, and `A_SHARE_TERMS_ENABLED`. The app does not rewrite `worker/env.local` when an option is toggled.

The ASR model directory is persisted in local UI settings, masked by default, and can be revealed or explicitly saved beside the selector. Replaying task history keeps the current Configuration values instead of restoring an old or empty ASR path.

The long-task “model cooldown” field is an explicit per-run override. A blank field keeps environment defaults; `0` disables waiting. A numeric value is exported to `COOLDOWN_DELAY`, `QWEN_ORGANIZE_COOLDOWN_DELAY`, `QWEN_PDF_POLISH_COOLDOWN_DELAY`, `QWEN_QUICKREAD_COOLDOWN_DELAY`, and `SUMMARY_CHUNK_COOLDOWN_DELAY`, so a specialized value from `env.local` cannot mask the UI override.

For image OCR and scanned-PDF OCR, the app now prefers the configured multimodal Qwen/OpenAI-compatible model. Local OCR tools are only fallbacks when a vision-capable model is unavailable.

## Bilibili Cookie

The app supports a Netscape cookie file:

```bash
BILIBILI_COOKIES_FILE="/path/to/bili_cookies.txt"
```

The desktop UI can refresh this file from a selected Chrome Profile. In the Chrome window signed into Bilibili, open `chrome://version/`, copy “Profile Path”, and select that exact leaf directory (`Default` or `Profile N`), then click “授权并刷新 Cookie”. Leaving the Cookie output empty stores it under the app's Application Support directory. The exporter rejects broad directories before yt-dlp can recurse, writes only Bilibili-domain cookies, and immediately checks whether the account is logged in. Cookie and Chrome Profile paths are masked in the UI by default; dependency and refresh results are shown in the log.

On macOS, Chrome refresh may require “other app data” access and a Chrome Safe Storage/Keychain confirmation. It does not require Documents, Downloads, removable-volume, or network-volume permission. Custom Cookie output paths can separately require access to the user-selected destination.

The same operation is available from the command line:

```bash
python3 worker/scripts/export_bilibili_cookies.py \
  --profile "/Users/xxx/Library/Application Support/Google/Chrome/Default" \
  --output ./bili_cookies.txt
```

QR login remains an optional later enhancement; Chrome Profile refresh is the current supported workflow.

## App-Managed Runtime

The managed runtime is implemented for the daily-use package and is initialized from the desktop UI. It is designed to make the main workflows usable without requiring conda or Homebrew; independent clean-Mac acceptance remains a release gate. Managed task requests deliberately clear saved Conda fields so a previous advanced configuration cannot leak into managed execution.

Target layout:

```text
~/Library/Application Support/Local Note Studio/
├── runtime/<version>/   # relocatable Python, pinned packages, ffmpeg/ffprobe, yt-dlp
├── tools/               # optional tools such as pandoc
├── models/              # downloaded or user-selected ASR models
└── state/               # versions, checksums, install logs, rollback metadata
```

Runtime policy:

1. Keep the signed `.app` small; it contains the UI, Rust bridge, worker sources, and runtime manager.
2. Install a relocatable Python runtime and locked dependencies under Application Support without touching system Python.
3. Manage `ffmpeg` / `ffprobe`; allow `yt-dlp` to update independently because B站 extraction changes frequently.
4. Install `pandoc` only when EPUB export is first requested.
5. Provide the ASR engine in the runtime, but download or select large model weights separately and show disk usage.
6. Continue using the configured OpenAI-compatible API for LLM organization and multimodal OCR.
7. Support install progress, integrity checks, upgrade, rollback/repair, and removal.
8. Preserve existing conda selection as an advanced backend.

Planned ownership matrix:

| Component | Distribution plan |
| --- | --- |
| Python and worker packages | App-managed, versioned runtime |
| `ffmpeg` / `ffprobe` | App-managed binaries |
| `yt-dlp` | App-managed with an independent update channel |
| `pandoc` | App-managed, installed on first EPUB use |
| ASR engine | App-managed Python dependency |
| ASR model weights | App-managed optional download or user-selected path |
| LLM organization and multimodal OCR | User-configured OpenAI-compatible API |
| B站 authentication | User account state refreshed from the selected Chrome Profile |
| macOS `textutil`, Quick Look and Vision | Use system-provided frameworks/tools when available |

Packaging acceptance requires a clean Mac without conda or Homebrew to initialize this runtime and complete the main task matrix. External prerequisites are limited to the configured LLM/OCR API service, account/browser state such as B站 Cookie, source/network access, and optional ASR model assets managed through the app.
