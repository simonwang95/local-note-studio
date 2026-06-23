# Progress

## 2026-06-23

Confirmed the MVP direction:

- The app is a local-first Mac operator console for generating Markdown notes.
- Runtime configuration comes first.
- Default output root comes second.
- Task execution comes after environment and output configuration.
- Current development uses an existing conda environment and OpenAI-compatible LLM API.
- The daily-use package will require an app-managed runtime for Python and non-LLM tools; existing conda selection remains an advanced mode.
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
- Bilibili Cookie status in dependency logs, Chrome Profile selection, safe Cookie refresh, and masked Cookie/profile path fields.
- Bilibili single URL, favorite test mode, single/charging opus, one-UP opus batch, local video/audio, web/WeChat URL, source-file conversion, AI-Chat JSON, paper quick read, and recursive EPUB export.
- Subtitle/transcript priority switching for Bilibili and local media tasks.
- Optional key-frame extraction for Bilibili and local video notes, with image-text insertion into the generated Markdown.
- Optional dialogue detection and speaker labeling for Bilibili and local video notes.
- Optional A-share terminology validation using `docs/code_list_20260612.csv`, wired into Qwen organize/proofread flows.
- Optional raw-subtitle retention, recursive local-media scanning, overwrite control, and custom output names for single-file tasks.
- Qwen organization for web/WeChat, Bilibili opus, office/document and AI-Chat conversion, preserving complete extracted source text at the end.
- Extended source conversion for local HTML, CSV/TSV, XLSX, PPTX, images, and scanned-PDF OCR fallback through the configured multimodal Qwen/OpenAI-compatible model.
- Paper quick-read full-translation fallback and plain Mermaid mindmap cleanup.
- Direct output into the selected task directory without hidden month/local subdirectories.
- Temporary staging for conversion drafts so the final directory only receives organized notes and promoted assets.
- Incremental `[抓取 i/n]` and `[整理 i/n]` progress for one-UP Bilibili opus batches.
- Local-media duration probing through `ffprobe` and task-specific temporary media caches.
- P0 automated regression suite with local fixtures for worker request/command contracts, naming and overwrite flags, original-text retention, relative image assets, restricted-content payloads, batch failure persistence, and Rust worker cancellation.
- One-command pre-commit verification through `npm run check` (TypeScript/Vite build, Python compile and regression tests, Rust tests).
- In-app Bilibili favorite/series discovery and selection for the logged-in account, replacing the normal need to edit `BILIBILI_FAV_MEDIA_ID` by hand.
- Isolated favorite/series batch processing with `[转录 i/n]` and `[Qwen i/n]` progress, `COOLDOWN_DELAY` between Qwen calls, structured totals, persistent failed-entry records, and retry-failures-only execution.
- Separate Bilibili diagnostics for expired login, missing charging/private-content permission, HTTP 412 risk control, empty opus content, and failed video extraction, plus independent login/target access verification.
- Worker-level output integrity gates covering temporary draft leakage, source traceability, Qwen organization, complete source/translation sections, raw-subtitle preference, EPUB non-empty output, and local Markdown image resolution.

## Current Todo Backlog

The canonical, prioritized backlog is maintained in [`docs/todo.md`](todo.md). P0 stability and verifiability work is complete; the next work starts at P1 daily-use experience.

Packaging decision: T-108 app-managed runtime must be completed before T-109 release packaging. A clean Mac without conda or Homebrew should run the main workflows after in-app initialization; LLM/OCR remains API-configured and ASR model assets are managed separately.
