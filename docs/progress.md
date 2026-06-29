# Progress

## 2026-06-29

- Confirmed the current implementation has completed the P0 reliability and P1 daily-use backlog, including automated regression checks, Bilibili favorites/series UX, restricted-content diagnostics, output integrity gates, task history/recovery, output actions, Manifest UI, OCR progress, semantic keyframes, browser-state capture, long-batch tuning, incognito mode, processing-record management, and the desktop tab workspace.
- Added user-facing feature work beyond the original first-stage scope: app-side Bilibili Cookie refresh from a selected Chrome Profile, masked sensitive paths, dialogue detection controls, single-file custom output names, one-UP opus batching, recursive EPUB export, richer document/OCR support, and Qwen organization that preserves originals where required.
- Managed runtime and macOS packaging are implemented on the development side, but the app-managed environment has not yet been validated on a clean Mac. Treat T-108 and T-109 as release gates until independent clean-Mac runtime initialization, task execution, signing, and notarization checks are complete.
- Hardened clean-Mac managed-runtime installation against PyPI/TLS failures. Locked Python dependencies now install with longer pip retry/timeout settings, retry the same requirements through fallback PyPI mirrors when the default source fails with network-like errors, and report the failure as a network/proxy/TLS issue rather than implying the pinned package version is missing. Documented the `LOCAL_NOTE_STUDIO_PIP_INDEX_URL` override for special test networks. Built and verified the 0.1.6 arm64 DMG with SHA-256 `8881763f64b2bd84076a37060bf435d64a71f9f42657ee45a12f07f6e4feedec`.
- Hardened the earlier Python runtime/tool archive download stage after a clean-Mac failure with `curl: (16) Error in the HTTP2 framing layer`. Runtime and tool downloads now prefer HTTP/1.1, retry the default protocol, suppress progress-meter spam, and document `LOCAL_NOTE_STUDIO_PYTHON_RUNTIME_URL` for same-file mirrors while preserving SHA-256 verification. Built and verified the 0.1.7 arm64 DMG with SHA-256 `2ce4e11b9f91bb402e16abbdeea6551018219b0b40bd016820e725246d8a2a69`.
- Fixed clean-Mac managed-runtime UX gaps: install/repair now streams progress logs, installs Pandoc during setup, reports Whisper (`mlx-whisper`) and Python package status explicitly, and Cookie refresh uses the current managed Python interpreter so `yt_dlp` is available. Built and verified the `0.1.8` arm64 DMG with SHA-256 `ff4850441fa7d638e71d7778eb5dd8a8cd7095446202422c0bc17b1c938bb464`.
- Tightened managed-runtime status and dependency messaging after clean-Mac testing: missing app-managed components now mark the runtime as “需要修复”, dependency checks point users to “安装/修复” instead of Homebrew/pip, and EPUB task copy reflects the managed Pandoc install path. Built and verified the `0.1.9` arm64 DMG with SHA-256 `8cd1f16baddb5b0e59d915e6f2b4887fa7481a3306ac03ce2c7d1a1b00e0aefe`.

## 2026-06-26

- Fixed development-process leftovers and worker shutdown behavior: `npm run dev` now starts Vite through a cleanup wrapper, `npm run dev:stop` can stop old project-local Vite servers, and the Rust bridge runs worker tasks in a process group that is terminated on cancel or app exit. Documented that Activity Monitor's huge Node virtual-memory number is usually reserved address space; high CPU from an orphaned `node .../.bin/vite` is the actionable symptom. Built and verified the 0.1.5 arm64 DMG with SHA-256 `8590939e74b5f81ef6be1478eeade921b915d4519f8d75b4b4d11581efa0f3d2`.

## 2026-06-24

- Added safe in-app maintenance for Manifest and incremental records: all records can be filtered, JSON records can be manually classified or returned to automatic detection, and obsolete records can be deleted without deleting source/output files.
- Clarified task-history scope as all local history (up to 100 entries, including the active task), with counts, status filtering, single-entry deletion, and confirmed clear-all behavior.
- Built and verified the optimized 0.1.4 3.3 MiB arm64 release DMG, inspected its bundled worker resources, and documented its SHA-256 plus same-architecture clean-Mac handoff steps. Developer ID signing, notarization, Intel/universal packaging, and independent clean-Mac acceptance remain release gates.
- Completed T-112 with Configuration, Tasks, and Validation tabs, keyboard navigation, a persistent output/log inspector, and a responsive bottom dock at narrow app widths.
- Added per-record checkboxes, select-visible controls, atomic batch status/delete operations, and persisted Manifest card expansion/filter state across refreshes.
- Fixed packaged-app runtime selection: new/legacy-unconfirmed installs use the managed backend, explicit Conda choices persist, managed requests no longer leak the saved Conda environment, and Finder launches discover common Conda locations or accept an absolute executable path.
- Reduced Cookie refresh to least privilege: exact Chrome leaf profiles are validated before extraction, broad directory traversal is blocked, the default filtered Cookie file lives in Application Support, legacy relative output is migrated, and the UI explains the only expected macOS prompts before access.
- Fixed the actual multi-permission trigger found through TCC logs and a live process sample: Cookie refresh supplied an empty note-output directory, which the generic snapshot code interpreted as the current directory and recursively scanned. Empty snapshots are now hard no-ops, Cookie refresh bypasses the snapshot pipeline, and packaged worker resources take precedence over a local source checkout.
- Persisted and masked the ASR model directory with an explicit save action; history replay now preserves current Configuration values instead of restoring historical empty ASR paths. Long-task cooldown overrides now reach every specialized Qwen cooldown variable, including UP-opus organization, with blank/default and explicit-zero semantics. Chrome Profile help now points to `chrome://version/` and its “Profile Path”.

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
- P1 local task history with completed/failed/cancelled/interrupted states, bounded logs, output lists, rerun, and conversion-draft recovery for organize-step retries.
- Output quick actions for Markdown/Finder/path copy, plus structured single and batch result lists.
- In-app Manifest status for video/source/quickread indexes, Bilibili processed state, batch failures, skipped entries, and missing outputs that require rebuild.
- Structured file/page progress for OCR with explicit Qwen Vision, Tesseract/Poppler, and macOS Vision backends; persistent page checkpoints support interrupted-task resume.
- Semantic keyframe selection aligned to structured note sections, black/transition/near-duplicate filtering, stale-frame cleanup, and detailed keyframe manifests.
- Explicit static-HTTP versus selected-Chrome-Profile browser capture for authenticated or JavaScript-rendered webpages.
- Per-task timeout, retry, cooldown, and chunk-size overrides for long batches.
- Versioned Application Support runtime management with checksum-pinned standalone Python, locked packages, yt-dlp, mlx-whisper, ffmpeg/ffprobe, Pandoc setup, disk usage, repair/uninstall, and advanced Conda fallback.
- macOS `.app`/DMG bundle configuration, generated icon set, worker resource whitelist, release checks, and a signing/notarization/clean-Mac acceptance checklist. Debug `.app` and arm64 DMG bundles were built successfully; Developer ID notarization and independent clean-Mac acceptance remain release gates.
- Startup dependency checks now tolerate legacy task-history records and use the public Tauri runtime detector; the check runs automatically on app launch and remains manually retryable.
- Optional incognito tasks generate normal outputs and local task history without reading or writing source/video/quickread/keyframe manifests or Bilibili incremental/failure state.

## Current Todo Backlog

The canonical, prioritized backlog is maintained in [`docs/todo.md`](todo.md). P0 and P1 workflows, including the T-112 desktop Tab layout, are implemented. T-108/T-109 still require the documented independent clean-Mac, Developer ID signing, and notarization release gates before distribution.

Packaging decision: T-108 app-managed runtime must be completed before T-109 release packaging. A clean Mac without conda or Homebrew should run the main workflows after in-app initialization; LLM/OCR remains API-configured and ASR model assets are managed separately.
