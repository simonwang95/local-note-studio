# Local Note Studio Agent Notes

## Project Identity

Local Note Studio is a macOS desktop app for organizing local notes. It wraps the proven processing scripts migrated from the local `knowledge-base` project, whose machine-specific path is configured as `KNOWLEDGE_BASE_PROJECT_DIR` in `worker/env.local`.

The product is local-first. Source files, generated Markdown, indexes, cookies, and model settings stay on the user's machine. The first release should feel like an operator console for turning videos, web pages, documents, and papers into Obsidian-compatible Markdown.

## Confirmed MVP Direction

- First configure the runtime: existing conda environment, Python fallback, OpenAI-compatible LLM API, model, and optional Bilibili cookie file.
- Bilibili Cookie refresh can use a user-selected Chrome profile path; only Bilibili-domain cookies are persisted, followed by an immediate login-state check.
- Bilibili and local video tasks can optionally detect interview/discussion content and ask Qwen to label speaker changes in the corrected transcript.
- Then choose a default output root. The app should prefer absolute output paths so generated notes can go directly into an external Obsidian vault or another user-selected notes directory.
- Then choose a task, provide a URL or file path, run a preview, run the real task, inspect logs, and open or locate outputs later.
- Supported first-stage tasks: Bilibili single URL, Bilibili favorites/series test mode, Bilibili opus/charging opus, one-UP opus batch processing, web or WeChat URL, source-file conversion, AI-Chat JSON, paper quick read, local video/audio, and recursive Markdown-to-EPUB export.
- Current development uses a user-managed conda environment, currently `course-whisper`. The daily-use package should instead manage Python and non-LLM tools under Application Support while retaining conda as an advanced backend.
- The app must check whether runtime dependencies are complete and provide installation hints when they are not.

## Non-Goals For The First Release

- No bundled local LLM runtime; LLM and multimodal OCR remain OpenAI-compatible API configuration.
- Do not place mutable runtimes or large ASR model files directly inside the signed `.app`; manage them as versioned Application Support resources.
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

- `docs/todo.md` is the canonical backlog. Do not maintain a second independent todo list in progress or planning docs.
- Every behavior, feature, configuration, installation, packaging, or release change must update the project documentation in the same change. Always review and update `README.md` plus the relevant files under `docs/` (especially `todo.md`, `progress.md`, user/environment guides, and release notes); do not treat documentation as a later follow-up. Before committing or handing off a build, verify that user-facing instructions, version numbers, artifact metadata, and known limitations match the implementation.
- Prefer one conversation/thread per independent feature or todo ID. Continue in the same thread for implementation, debugging, and acceptance of that feature; start a new thread when moving to a separate feature with a different acceptance boundary.
- At the start of a new development thread, read `agent.md`, `docs/todo.md`, `docs/progress.md`, and the latest git status/log before editing. The user should reference the todo ID or a concise objective; committed code and project docs are the handoff source of truth rather than old chat context.
- This directory may start without git history; initialize git before the first committed development checkpoint.
- Keep migrated scripts close to the old `knowledge-base` behavior until reusable modules can be extracted.
- Use absolute paths in task payloads when possible.
- `output_filename` is optional and should only be used for single-output tasks. It is useful when generated image assets should follow a stable final Markdown file name.
- Convert-and-organize tasks must stage conversion drafts in a temporary directory. The user-selected output directory should receive only organized notes and promoted assets after Qwen succeeds.
- Avoid broad refactors while the worker contract is still forming.
