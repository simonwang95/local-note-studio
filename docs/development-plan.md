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

- Add native file and directory pickers.
- Add an "open output folder" action.
- Store named profiles for conda/model/output roots.
- Stream logs incrementally instead of returning them after completion.
- Add cancel/retry controls.

## Stage 3: Task History And Recovery

Goal: make long-running and batch tasks easier to manage.

Candidate work:

- Store task history in SQLite.
- Persist stdout/stderr logs per run.
- Track generated Markdown paths.
- Add task status, retry, and failure diagnostics.
- Surface manifest/index status from migrated scripts.

## Stage 4: Managed Environment

Goal: optionally bootstrap a known-good runtime while preserving the local-first model.

Candidate work:

- Create app-managed Python environment.
- Install pinned Python packages.
- Verify ffmpeg and yt-dlp.
- Guide the user to install or select ASR model files.
- Keep advanced users able to use their own conda environment.

## Implementation Principles

- Keep Rust thin.
- Keep heavy processing in Python.
- Prefer worker task contracts over frontend business logic.
- Prefer explicit absolute paths.
- Preserve compatibility with the migrated `knowledge-base` scripts until a shared package exists.
