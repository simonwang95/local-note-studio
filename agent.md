# Local Note Studio Agent Notes

## Project Identity

Local Note Studio is a macOS desktop app for organizing local notes. It wraps the proven processing scripts migrated from the local `knowledge-base` project, whose machine-specific path is configured as `KNOWLEDGE_BASE_PROJECT_DIR` in `worker/env.local`.

The product is local-first. Source files, generated Markdown, indexes, cookies, and model settings stay on the user's machine. The first release should feel like an operator console for turning videos, web pages, documents, and papers into Obsidian-compatible Markdown.

## Confirmed MVP Direction

- First configure the runtime: existing conda environment, Python fallback, OpenAI-compatible LLM API, model, and optional Bilibili cookie file.
- Then choose a default output root. The app should prefer absolute output paths so generated notes can go directly into an external Obsidian vault or another user-selected notes directory.
- Then choose a task, provide a URL or file path, run a preview, run the real task, inspect logs, and open or locate outputs later.
- Supported first-stage tasks: Bilibili single URL, Bilibili favorites/series test mode, Bilibili opus/charging opus, one-UP opus batch processing, web or WeChat URL, source-file conversion, AI-Chat JSON, paper quick read, local video/audio, and recursive Markdown-to-EPUB export.
- The first version uses a user-managed conda environment, currently `course-whisper`, and an OpenAI-compatible API such as LM Studio.
- The app must check whether runtime dependencies are complete and provide installation hints when they are not.

## Non-Goals For The First Release

- No bundled local LLM runtime.
- No bundled Python, ffmpeg, yt-dlp, Whisper, or model files.
- No cloud sync or account system.
- No full task database until the task contract stabilizes.
- No macOS signing/notarization work before the local workflow is useful.

## Architecture Contract

```text
Tauri UI
  -> Rust command bridge
  -> worker/local_note_studio_worker.py
  -> worker/scripts/*
  -> user-selected Markdown output directory
```

The frontend should stay focused on task selection, settings, validation, and logs. The Rust layer should stay a thin process bridge. The Python worker owns task mapping, environment loading, dependency checks, and calling migrated scripts.

## Local Configuration

- `worker/env.local` is local-only and should not be committed.
- `worker/env.example` documents portable defaults.
- API keys, cookie paths, favorite IDs, local ASR model paths, and personal vault paths belong in local config or UI storage, not committed docs.

## Development Notes

- This directory may start without git history; initialize git before the first committed development checkpoint.
- Keep migrated scripts close to the old `knowledge-base` behavior until reusable modules can be extracted.
- Use absolute paths in task payloads when possible.
- `output_filename` is optional and should only be used for single-output tasks. It is useful when generated image assets should follow a stable final Markdown file name.
- Convert-and-organize tasks must stage conversion drafts in a temporary directory. The user-selected output directory should receive only organized notes and promoted assets after Qwen succeeds.
- Avoid broad refactors while the worker contract is still forming.
