# Development Plan

## Stage 1: Useful Local Operator Console

Goal: make the desktop app able to validate the user's existing environment and run the migrated worker tasks with clear inputs and logs.

Deliverables:

- Runtime configuration section appears before task selection.
- Default output root is configured before per-task output path.
- Environment check runs through the worker and reports required and optional dependencies.
- Dependency failures include installation hints.
- Task preview remains available through dry-run.
- Task execution still uses the migrated Python scripts.

## Stage 2: Better Desktop Ergonomics

Goal: reduce manual path entry and make common operations feel native.

Candidate work:

- Add native file and directory pickers. (Done for current path inputs.)
- Add an "open output folder" action.
- Store named profiles for conda/model/output roots.
- Stream logs incrementally instead of returning them after completion. (Done for current worker runs.)
- Add cancel/retry controls. (Cancel is available; retry remains pending.)

## Stage 3: Task History And Recovery

Goal: make long-running and batch tasks easier to manage.

Candidate work:

- Store task history in SQLite.
- Persist stdout/stderr logs per run.
- Track generated Markdown paths.
- Add task status, retry, and failure diagnostics.
- Surface manifest/index status from migrated scripts.

## Stage 4: Rich Video Notes

Goal: make Bilibili and local video notes more useful when visual context matters.

Candidate work:

- Add an option for Bilibili and local video tasks to extract key frames based on transcript/content structure. (Done for the first pass.)
- Save key frames as local assets next to the generated Markdown. (Done for the first pass.)
- Insert selected frames into the note at relevant transcript sections to create image-text notes. (Done for the first pass.)
- Avoid excessive screenshots by deduplicating visually similar frames and limiting frames per section/video.
- Record extracted frame paths in `video-manifest.json` for later cleanup or regeneration.

## Stage 5: Managed Environment

Goal: optionally bootstrap a known-good runtime while preserving the local-first model.

Candidate work:

- Create app-managed Python environment.
- Install pinned Python packages.
- Verify ffmpeg and yt-dlp.
- Guide the user to install or select ASR model files.
- Keep advanced users able to use their own conda environment.

## Open Todo Backlog

- Improve Bilibili favorites/series UX: favorite selection, cookie state, batch progress, and per-video retry.
- Add task history and recovery: persisted logs, generated file paths, status, retry, and failure diagnostics.
- Add output actions: open generated Markdown, open output folder, and copy output path.
- Surface manifest/index status in the UI instead of relying only on logs.
- Improve OCR ergonomics: page-level progress, better fallback diagnostics, and larger scanned-PDF batch handling.
- Consider legacy binary office support (`.xls`, `.ppt`) when there is a reliable local conversion path.
- Enhance web capture for pages that require login, dynamic rendering, or browser state.
- Observe longer real batches to tune Qwen timeouts, cooldowns, chunk sizes, and output stability.
- Package the desktop app for daily use, including icon, signing/notarization path, and release build workflow.
- Replace ad-hoc localStorage settings with a more explicit local config/profile store.
- Explore an optional app-managed Python runtime while keeping existing conda selection available.

## Implementation Principles

- Keep Rust thin.
- Keep heavy processing in Python.
- Prefer worker task contracts over frontend business logic.
- Prefer explicit absolute paths.
- Preserve compatibility with the migrated `knowledge-base` scripts until a shared package exists.
