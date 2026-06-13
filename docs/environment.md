# Environment Setup

## MVP Principle

The first version uses a user-managed conda environment. This keeps the app small and avoids packaging native ASR dependencies before the task flow settles.

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

## Bilibili Cookie

MVP supports a Netscape cookie file:

```bash
BILIBILI_COOKIES_FILE="/path/to/bili_cookies.txt"
```

QR login can be added later. Until then, cookie export is the most stable option.

## Future App-Managed Environment

Later versions can add an "Initialize Environment" button that:

1. Creates an app-managed Python environment.
2. Installs pinned worker dependencies.
3. Downloads or verifies ffmpeg/yt-dlp.
4. Lets the user choose Whisper model storage.
