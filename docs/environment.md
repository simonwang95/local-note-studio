# Environment Setup

## Runtime Selection

Packaged builds default to the app-managed runtime. It is initialized from the desktop UI and is the recommended mode for testers because it does not require a separately installed Conda or Homebrew environment.

An existing Conda/Python environment remains available as an advanced backend for development and compatible user-managed setups. Once a user explicitly selects this backend, the backend, environment name, Python command, and optional Conda executable path persist across launches. Legacy settings without an explicit runtime preference migrate to the managed backend once.

Finder-launched apps do not inherit an interactive shell's complete `PATH`. Local Note Studio therefore searches common Miniforge, Miniconda, Anaconda, Homebrew, and system locations. For a non-standard install, configure an absolute executable path such as `/Users/xxx/miniforge3/bin/conda` in the app.

The current known-good environment is:

```bash
CONDA_ENV="course-whisper"
```

Source builds and regression checks also require Node.js 20 or newer. On machines with multiple Node/Python installations, verify the selected executables before attributing a build failure to the application:

```bash
node --version
python3 --version
python3 -c "import requests, pypdf, lxml"
```

The July 2026 development-machine acceptance used Node.js `20.20.2` and the `course-whisper` Python `3.11.15`. The older `/usr/local/bin/node` 16 and Apple Command Line Tools Python 3.9 on that machine are not suitable for the full source regression command.

The same `course-whisper` environment contains `mlx-whisper 0.4.3`; a normal macOS process can import `mlx_whisper` successfully. Because importing MLX initializes Metal, an environment check executed inside a headless or GPU-restricted sandbox may report `No Metal device available`. Treat that message as a runtime-permission limitation rather than a missing-package result, and repeat the import from Terminal or the installed GUI context.

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

Agent automation Profiles are non-secret and live under `~/Library/Application Support/Local Note Studio/config/automation-profiles.json`. Start from `worker/automation-profiles.example.json`; do not add API keys, Cookie content, Chrome Profiles or arbitrary executable arguments. The Agent request may override only its explicit source, bounded limit and dry-run flag. Profile values override ordinary Worker defaults, while secrets continue to come only from protected local configuration.

Automation state is stored under `~/Library/Application Support/Local Note Studio/state/`: `global-task.*` for the active cross-process lock, `automation-history.sqlite3` for redacted audit history, `up-sync/` for per-UP incremental status, and `opus-image-analysis-cache/` for SHA-256 keyed Bilibili attachment OCR/vision results. `LOCAL_NOTE_STUDIO_STATE_DIR` and `LOCAL_NOTE_STUDIO_PROFILES_FILE` are intended for isolated development/tests; overriding State also moves the image-analysis cache, while `LOCAL_NOTE_STUDIO_APP_DATA_DIR` must remain explicit when an isolated test read-only reuses the normal Cookie/model configuration.

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

The ASR model directory is persisted in local UI settings, masked by default, and can be revealed or explicitly saved beside the selector. Managed install/repair downloads the default MLX Whisper model into Application Support and the UI auto-fills that model path when the field is empty. Replaying task history keeps the current Configuration values instead of restoring an old or empty ASR path.

The long-task “model cooldown” field is an explicit per-run override. A blank field keeps environment defaults; `0` disables waiting. A numeric value is exported to `COOLDOWN_DELAY`, `QWEN_ORGANIZE_COOLDOWN_DELAY`, `QWEN_PDF_POLISH_COOLDOWN_DELAY`, `QWEN_QUICKREAD_COOLDOWN_DELAY`, `SUMMARY_CHUNK_COOLDOWN_DELAY`, and `OPUS_IMAGE_ANALYSIS_COOLDOWN_DELAY`, so a specialized value from `env.local` cannot mask the UI override. Waiting occurs only between adjacent real calls; Bilibili attachment `off` mode and SHA-256 cache hits do not wait.

For image OCR and scanned-PDF OCR, the app now prefers the configured multimodal Qwen/OpenAI-compatible model. Local OCR tools are only fallbacks when a vision-capable model is unavailable.

Bilibili opus attachment analysis is a separate Profile option, `opus_image_analysis`, with `off`, `ocr`, and `vision` modes. It does not change `ENABLE_OCR`, which continues to serve direct image sources and sparse/scanned PDFs. Finance Profiles should normally choose `vision`; each uncached attachment can consume one multimodal request, up to 12 images per post.

Attachment OCR/vision has independent request limits:

```bash
OPUS_IMAGE_ANALYSIS_MAX_TOKENS="4096"
OPUS_IMAGE_ANALYSIS_TIMEOUT_SECONDS="300"
```

The output budget is always sent as `max_tokens`, is hard-capped at `8192`, and never inherits `SUMMARY_MAX_TOKENS`, `QWEN_QUICKREAD_MAX_TOKENS`, or PDF/long-document budgets. An explicit Profile `timeout_seconds` overrides the image timeout and the existing webpage/PDF/Quick Read/organize timeouts for that run, but it does not change the image token budget. Schema/rules-versioned successful results are cached by image, mode, model, and read-only post/time context. Truncated, empty, invalid-JSON, timed-out, cancelled, or temporary-error results are not cached. Failures retain the downloaded image, original body, and a retry placeholder; a failed retry cannot overwrite an existing complete note.

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

The managed runtime is implemented for the daily-use package and is initialized from the desktop UI. It is designed to make the main workflows usable without requiring conda or Homebrew; independent clean-Mac acceptance remains a release gate. Managed task requests deliberately clear saved Conda fields and legacy `CONDA_ENV` values before invoking nested Bilibili scripts so a previous advanced configuration cannot leak into managed execution.

Target layout:

```text
~/Library/Application Support/Local Note Studio/
├── runtime/<version>/   # relocatable Python, pinned packages, yt-dlp, mlx-whisper, ffmpeg/ffprobe, pandoc
├── tools/               # reserved for optional tool metadata
├── models/              # downloaded or user-selected ASR models
└── state/               # versions, checksums, install logs, rollback metadata
```

Runtime policy:

1. Keep the signed `.app` small; it contains the UI, Rust bridge, worker sources, and runtime manager.
2. Install a relocatable Python runtime and locked dependencies under Application Support without touching system Python. Runtime and tool downloads use quiet curl output, prefer HTTP/1.1 first, and then retry the default protocol to avoid test-network HTTP/2 framing failures. Advanced testers can override the Python runtime archive URL with `LOCAL_NOTE_STUDIO_PYTHON_RUNTIME_URL=https://.../cpython-...tar.gz`; tool archives can be overridden independently with `LOCAL_NOTE_STUDIO_PANDOC_URL`, `LOCAL_NOTE_STUDIO_FFMPEG_URL`, or `LOCAL_NOTE_STUDIO_FFPROBE_URL`. The SHA-256 check still applies to every mirrored archive.
3. Install locked Python dependencies with `pip --prefer-binary` and conservative retry/timeout settings. If the default PyPI route fails because of TLS, proxy, DNS, or timeout problems, the installer retries the same locked requirements through fallback PyPI mirrors. Advanced testers can override the first package index by launching with `LOCAL_NOTE_STUDIO_PIP_INDEX_URL=https://.../simple`.
4. Manage `ffmpeg` / `ffprobe`; allow `yt-dlp` to update independently because B站 extraction changes frequently.
5. Install `pandoc` during “安装/修复” so EPUB export works without a separate Homebrew or Conda step. Pandoc is best-effort because it only gates EPUB export: download failures are logged as repair warnings and should not block video, document, OCR, Cookie, ASR, or Bilibili workflows.
6. Provide the ASR engine (`mlx-whisper` on Apple Silicon) in the runtime, but download or select large model weights separately and show disk usage. Managed ASR model downloads try Hugging Face first and then `https://hf-mirror.com` when no explicit endpoint is configured; advanced testers can use `LOCAL_NOTE_STUDIO_HF_ENDPOINT=https://...` or standard `HF_ENDPOINT=https://...` for a specific Hugging Face-compatible mirror.
7. Continue using the configured OpenAI-compatible API for LLM organization and multimodal OCR.
8. Support install progress, integrity checks, upgrade, rollback/repair, and removal. A missing managed component marks the runtime as “需要修复” and should direct users back to Install/Repair rather than Homebrew or pip.
9. Pin nested Bilibili ASR scripts to the same managed Python executable as the outer worker, and validate managed ASR packages with real imports so a stale Conda, `.venv`, or GUI `PATH` cannot hide `mlx-whisper` problems.
10. Preserve existing conda selection as an advanced backend.

Planned ownership matrix:

| Component | Distribution plan |
| --- | --- |
| Python and worker packages | App-managed, versioned runtime |
| `ffmpeg` / `ffprobe` | App-managed binaries |
| `yt-dlp` | App-managed with an independent update channel |
| `pandoc` | App-managed during install/repair; best-effort and only required for EPUB export |
| ASR engine | App-managed Python dependency (`mlx-whisper` on Apple Silicon) |
| ASR model weights | Default MLX Whisper model installed during managed install/repair with `hf-mirror.com` fallback; user-selected path remains supported |
| LLM organization and multimodal OCR | User-configured OpenAI-compatible API |
| B站 authentication | User account state refreshed from the selected Chrome Profile |
| macOS `textutil`, Quick Look and Vision | Use system-provided frameworks/tools when available |

Packaging acceptance requires a clean Mac without conda or Homebrew to initialize this runtime and complete the main task matrix. External prerequisites are limited to the configured LLM/OCR API service, account/browser state such as B站 Cookie, source/network access, and network access for the managed ASR model download.
