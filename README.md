# Local Note Studio

Local Note Studio is a macOS desktop app for turning videos, web pages, documents, and papers into local Markdown notes.

The first MVP reuses the proven processing scripts from the `knowledge-base` project and wraps them with a small Tauri app plus a Python worker.

## MVP Scope

- Bilibili single video URL.
- Bilibili series/favorites groundwork.
- WeChat article and general web page URL.
- Word/PDF source conversion.
- Paper quick read.
- Local video/audio transcription.

The first version uses an existing user-managed conda environment, such as `course-whisper`, and an OpenAI-compatible LLM API. Bundling Whisper, ffmpeg, and a Python runtime is deferred until the app workflow stabilizes.

## Project Layout

```text
local-note-studio/
  src/                    Tauri frontend
  src-tauri/              macOS desktop shell and worker command bridge
  worker/                 Python worker and migrated processing scripts
  docs/                   Product, architecture, environment, and task docs
  docs/reference/         Reference docs migrated from knowledge-base
```

## Quick Start

1. Copy `worker/env.example` to `worker/env.local`.
2. Set `CONDA_ENV=course-whisper` or another environment that has `yt-dlp`, `ffmpeg`, Whisper dependencies, and Python packages installed.
3. Set LLM API fields:

```bash
DEFAULT_LLM_API_BASE="http://127.0.0.1:1234/v1"
DEFAULT_LLM_API_KEY="lm-studio"
DEFAULT_LLM_MODEL="qwen3.6-35b-a3b-nvfp4"
```

4. Run a worker dry run:

```bash
python3 worker/local_note_studio_worker.py --task paper-quickread --source "/path/to/paper.pdf" --output-dir "/path/to/output" --dry-run
```

5. Start the desktop app after installing frontend dependencies:

```bash
npm install
npm run tauri:dev
```

`npm run dev` starts the Vite web preview only on `http://127.0.0.1:1420`. It is useful for layout checks, but dependency checks and task execution require the Tauri desktop shell because they call the Rust `run_worker` command.

## Current Status

This repository starts as an MVP scaffold. The processing engine is already useful from the Python worker; the Tauri UI is intentionally thin and should grow around stable task contracts.
