# Progress

## 2026-06-13

Confirmed the MVP direction:

- The app is a local-first Mac operator console for generating Markdown notes.
- Runtime configuration comes first.
- Default output root comes second.
- Task execution comes after environment and output configuration.
- The first release uses an existing conda environment and OpenAI-compatible LLM API.
- The app must validate dependencies and show installation hints.

Current repository state:

- Tauri shell exists in `src-tauri/`.
- TypeScript frontend exists in `src/`.
- Python worker exists at `worker/local_note_studio_worker.py`.
- Migrated scripts live under `worker/scripts/`.
- Reference docs from `knowledge-base` are stored under `docs/reference/knowledge-base-docs/`.

Implemented since the initial checkpoint:

- Worker environment validation with install hints.
- Tauri desktop UI for runtime settings, output paths, task selection, dry-run preview, streaming logs, and cancellation.
- Native file and directory pickers.
- Startup dependency check in the desktop app.
- Cookie status prompt in the runtime panel, including existence and last update time from the current cookie file.
- Bilibili single URL, Bilibili favorite test mode, local video/audio, web/WeChat URL, Word/PDF/DOC conversion, and paper quick-read task wiring.
- Subtitle/transcript priority switching for Bilibili and local media tasks.
- Optional key-frame extraction for Bilibili and local video notes, with image-text insertion into the generated Markdown.
- Optional A-share terminology validation using `docs/code_list_20260612.csv`, wired into Qwen organize/proofread flows.
- Qwen organization for web/WeChat and Word/PDF/DOC conversions, preserving the original extracted text at the end.
- Extended source conversion for local HTML, CSV/TSV, XLSX, PPTX, images, and scanned-PDF OCR fallback through the configured multimodal Qwen/OpenAI-compatible model.
- Paper quick-read full-translation fallback and plain Mermaid mindmap cleanup.
- Direct output into the selected task directory without hidden month/local subdirectories.

## Current Todo Backlog

- Improve Bilibili favorites/series UX: choose favorites, show cookie/login state, display batch progress, and retry failed videos.
- Add task history and recovery: persist logs, statuses, generated Markdown paths, retry actions, and failure diagnostics.
- Add output actions: open generated Markdown, open output folder, and copy output path.
- Surface manifest/index status from migrated scripts inside the UI.
- Add page-level OCR progress and failure diagnostics for very long scanned PDFs or image batches.
- Support more legacy office formats when practical, especially old `.xls` / `.ppt` binaries.
- Enhance webpage capture for pages that require login state, browser rendering, or anti-scraping workarounds.
- Run longer real batches to tune Qwen timeouts, cooldowns, chunk sizes, and output stability.
- Package the desktop app for daily use, including release build workflow, app icon polish, signing, and notarization path.
- Move settings from browser `localStorage` toward explicit local profiles for conda/model/output roots.
- Explore an optional app-managed Python runtime while keeping existing conda selection available.
