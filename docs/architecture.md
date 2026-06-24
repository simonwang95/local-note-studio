# Architecture

## Recommended Stack

- Desktop shell: Tauri.
- Frontend: Vite + TypeScript.
- Worker: Python scripts migrated from `knowledge-base`.
- LLM: OpenAI-compatible API, including LM Studio, Ollama-compatible proxies, or remote providers.
- Runtime/ASR: packaged builds default to the app-managed runtime with optional managed model assets; an explicitly selected user-managed Conda environment remains an advanced backend.

## Process Model

```text
Tauri UI
  -> Rust command bridge
  -> worker/local_note_studio_worker.py
  -> worker/scripts/*
  -> Markdown output directory
```

The desktop app should not embed business logic for transcription, conversion, or prompting. It sends a structured task request to the worker and streams or displays logs.

## Distribution Runtime Boundary

The daily-use package should use a hybrid runtime instead of putting every dependency inside the signed `.app`:

```text
Local Note Studio.app
  -> Tauri UI, Rust bridge, worker sources, runtime manager
  -> ~/Library/Application Support/Local Note Studio/
       runtime/<version>/   relocatable Python, pinned packages, ffmpeg/ffprobe, yt-dlp
       tools/               optional tools such as pandoc
       models/              optional app-managed ASR models
       state/               runtime metadata, checksums, logs, rollback state
  -> user-configured OpenAI-compatible LLM/OCR API
```

Keeping the managed runtime outside the `.app` allows dependency repair and yt-dlp updates without replacing the signed application. Runtime downloads must use pinned versions and checksums. The app must support install, verify, upgrade, rollback/repair, and removal. Existing conda selection remains an advanced execution backend.

LLM inference and multimodal OCR remain external API configuration. ASR code can live in the managed Python environment, while large model weights are downloaded or selected on demand rather than bundled into the installer.

## Worker Contract

The worker accepts either CLI flags or a JSON request. Normal processing tasks use the same core fields:

```json
{
  "task": "paper-quickread",
  "source": "/path/to/paper.pdf",
  "output_dir": "/path/to/notes",
  "output_filename": "optional-custom-name.md",
  "runtime_backend": "conda",
  "conda_env": "course-whisper",
  "conda_bin": "/Users/xxx/miniforge3/bin/conda",
  "api_base": "http://127.0.0.1:1234/v1",
  "api_key": "lm-studio",
  "model": "qwen3.6-35b-a3b-nvfp4",
  "cookies": "/path/to/bili_cookies.txt",
  "browser_profile": "/Users/xxx/Library/Application Support/Google/Chrome/Default",
  "subtitle_strategy": "yt-dlp",
  "extract_keyframes": false,
  "dialogue_detection": false,
  "keep_original_subtitles": true,
  "recursive_search": false,
  "overwrite_outputs": false,
  "stock_terms": false,
  "enable_ocr": false,
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

For Bilibili tasks, `subtitle_strategy` accepts `yt-dlp`, `web`, or `asr`. For local video tasks, the desktop UI only exposes local subtitle-first and ASR-first choices. The worker maps these to `BILIBILI_PREFER_WEB_SUBTITLE` and `FORCE_ASR` before calling the migrated scripts. `favorite_limit` defaults to `1` for safe favorite-list testing; `0` means full favorite or UP-opus processing. Video options are mapped to environment variables consumed by the migrated scripts, including key-frame extraction, dialogue detection, original-subtitle retention, overwrite behavior, and A-share terminology validation.

Cookie refresh uses two internal tasks: `refresh-bilibili-cookies` calls the dedicated exporter with the selected Chrome Profile, and `bilibili-cookie-status` validates the resulting Netscape file without running content processing. Only Bilibili-domain cookies are persisted.

## Task Mapping

| Task | Worker script |
| --- | --- |
| `bilibili-url` | `worker/scripts/run_bilibili_transcript.py --url` |
| `bilibili-favorite` | `worker/scripts/run_bilibili_transcript.py --favorite --limit N` |
| `local-video` | `worker/scripts/run_bilibili_transcript.py --local-file/--local-dir` |
| `web-url` | `worker/scripts/convert_sources_to_md.py --url`, then `worker/scripts/qwen_organize_notes.py --source` |
| `bilibili-opus` | `worker/scripts/convert_sources_to_md.py --url`, then `worker/scripts/qwen_organize_notes.py --source` |
| `bilibili-up-opus` | `worker/scripts/convert_sources_to_md.py --bilibili-up-opus`, then batched `worker/scripts/qwen_organize_notes.py --source` |
| `source-file` | `worker/scripts/convert_sources_to_md.py --source`, then `worker/scripts/qwen_organize_notes.py --source` |
| `ai-chat` | `worker/scripts/convert_sources_to_md.py --source`, then `worker/scripts/qwen_organize_notes.py --source` |
| `paper-quickread` | `worker/scripts/quick_read_pdf.py --source` |
| `epub-export` | `worker/scripts/export_epub.py --source-dir` |
| `env-check` | internal worker dependency validation |

`output_filename` is optional and only intended for single-output tasks. It cannot contain path separators. Directory batch video jobs intentionally reject it to prevent multiple sources writing to the same Markdown file.

## Later Hardening

- Store task history in SQLite.
- Add retry and partial-recovery controls; cancellation and streaming logs are already implemented.
- Add output-integrity checks and surface manifest/index state.
- Optionally add Bilibili QR login while keeping Chrome Profile refresh available.
- Complete the app-managed runtime lifecycle before release packaging, signing, and notarization.

See [`docs/todo.md`](todo.md) for the prioritized backlog and acceptance criteria.
