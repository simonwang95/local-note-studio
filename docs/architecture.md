# Architecture

## Recommended Stack

- Desktop shell: Tauri.
- Frontend: Vite + TypeScript.
- Worker: Python scripts migrated from `knowledge-base`.
- LLM: OpenAI-compatible API, including LM Studio, Ollama-compatible proxies, or remote providers.
- ASR: user-managed conda environment in MVP.

## Process Model

```text
Tauri UI
  -> Rust command bridge
  -> worker/local_note_studio_worker.py
  -> worker/scripts/*
  -> Markdown output directory
```

The desktop app should not embed business logic for transcription, conversion, or prompting. It sends a structured task request to the worker and streams or displays logs.

## Worker Contract

The worker accepts either CLI flags or a JSON request. Normal processing tasks use the same core fields:

```json
{
  "task": "paper-quickread",
  "source": "/path/to/paper.pdf",
  "output_dir": "/path/to/notes",
  "conda_env": "course-whisper",
  "api_base": "http://127.0.0.1:1234/v1",
  "api_key": "lm-studio",
  "model": "qwen3.6-35b-a3b-nvfp4",
  "dry_run": false
}
```

The worker also supports an environment-check request:

```json
{
  "task": "env-check",
  "conda_env": "course-whisper",
  "python_bin": "python3",
  "api_base": "http://127.0.0.1:1234/v1",
  "api_key": "lm-studio",
  "model": "qwen3.6-35b-a3b-nvfp4",
  "cookies": "/path/to/bili_cookies.txt",
  "subtitle_strategy": "yt-dlp"
}
```

This request should not run user content processing. It checks the selected runtime, required Python packages, command-line tools, optional ASR helpers, and local path configuration, then returns actionable hints.

For Bilibili tasks, `subtitle_strategy` accepts `yt-dlp`, `web`, or `asr`. For local video tasks, the desktop UI only exposes local subtitle-first and ASR-first choices. The worker maps these to `BILIBILI_PREFER_WEB_SUBTITLE` and `FORCE_ASR` before calling the migrated scripts. `favorite_limit` defaults to `1` for safe favorite-list testing; `0` means full favorite processing.

## Task Mapping

| Task | Worker script |
| --- | --- |
| `bilibili-url` | `worker/scripts/run_bilibili_transcript.py --url` |
| `bilibili-favorite` | `worker/scripts/run_bilibili_transcript.py --favorite --limit N` |
| `local-video` | `worker/scripts/run_bilibili_transcript.py --local-file/--local-dir` |
| `web-url` | `worker/scripts/convert_sources_to_md.py --url`, then `worker/scripts/qwen_organize_notes.py --source` |
| `source-file` | `worker/scripts/convert_sources_to_md.py --source` |
| `paper-quickread` | `worker/scripts/quick_read_pdf.py --source` |
| `env-check` | internal worker dependency validation |

## Later Hardening

- Stream stdout/stderr incrementally instead of waiting for the process to finish.
- Store task history in SQLite.
- Add cancel/retry controls.
- Add Bilibili QR login and managed cookie storage.
- Add app-managed Python environment bootstrap.
