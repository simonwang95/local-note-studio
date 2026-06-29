# Development Plan

## Stage 1: Useful Local Operator Console (Completed)

Goal: make the desktop app able to validate the user's existing environment and run the migrated worker tasks with clear inputs and logs.

Deliverables:

- Runtime configuration section appears before task selection.
- Default output root is configured before per-task output path.
- Environment check runs through the worker and reports required and optional dependencies.
- Dependency failures include installation hints.
- Task preview remains available through dry-run.
- Task execution still uses the migrated Python scripts.

## Stage 2: Better Desktop Ergonomics (Completed)

Goal: reduce manual path entry and make common operations feel native.

Candidate work:

- Add native file and directory pickers. (Done for current path inputs.)
- Add an "open output folder" action. (Done through structured output actions.)
- Store named profiles for conda/model/output roots. (Pending; current settings use localStorage.)
- Stream logs incrementally instead of returning them after completion. (Done for current worker runs.)
- Add cancel/retry controls. (Cancel, rerun, failed-step recovery, and failed-item retry are available.)
- Refresh Bilibili Cookie from a selected Chrome Profile. (Done.)
- Mask API Key, Cookie path, and browser profile path by default. (Done.)

## Stage 3: Task History And Recovery (Completed)

Goal: make long-running and batch tasks easier to manage.

Candidate work:

- Store task history in local persistent storage. (Done.)
- Persist bounded logs per run. (Done.)
- Track generated Markdown paths and batch output lists. (Done.)
- Add task status, rerun, recovery, and failure diagnostics. (Done.)
- Surface manifest/index status from migrated scripts. (Done.)

## Stage 4: Rich Video Notes (First Pass Completed)

Goal: make Bilibili and local video notes more useful when visual context matters.

Candidate work:

- Add an option for Bilibili and local video tasks to extract key frames based on transcript/content structure. (Done for the first pass.)
- Save key frames as local assets next to the generated Markdown. (Done for the first pass.)
- Insert selected frames into the note at relevant transcript sections to create image-text notes. (Done for the first pass.)
- Detect dialogue/interview content and label speaker changes in the corrected transcript. (Done for the first pass.)
- Avoid excessive screenshots by deduplicating visually similar frames and limiting frames per section/video.
- Record extracted frame paths in `video-manifest.json` for later cleanup or regeneration.

## Stage 5: App-Managed Runtime (Implemented, Awaiting Clean-Mac Validation)

Goal: make the main workflows usable on a clean Mac without requiring conda or Homebrew, while preserving existing conda selection for advanced users.

Candidate work:

- Install a relocatable Python runtime and pinned worker dependencies under Application Support rather than inside the signed `.app` bundle.
- Install and verify managed `ffmpeg` / `ffprobe`; make `yt-dlp` independently updateable; install `pandoc` on demand for EPUB export.
- Manage ASR engines and optional model downloads/selections in a separate model directory.
- Keep LLM and multimodal OCR behind the user-configured OpenAI-compatible API.
- Add runtime versioning, install progress, integrity checks, upgrades, repair, removal, disk usage, and rollback behavior.
- Keep advanced users able to select their own conda environment.
- Validate the main task matrix on a clean Mac without conda or Homebrew. (Pending release gate.)

## Stage 6: Signed Daily-Use Package (Development Build Works, Release Gate Open)

Goal: package and distribute the app only after the managed runtime lifecycle works reliably.

Candidate work:

- Create release builds with final icons, version metadata, and upgrade notes.
- Sign and notarize the `.app` / installer.
- Handle first-launch permissions and Application Support data migration.
- Verify installation, runtime initialization, task execution, upgrade, repair, and uninstall on a clean macOS account.

## Open Todo Backlog

The canonical backlog is [`docs/todo.md`](todo.md). It is ordered by P0 reliability work, P1 daily-use improvements, and P2 extensions so this plan does not maintain a second, conflicting list.

## Implementation Principles

- Keep Rust thin.
- Keep heavy processing in Python.
- Prefer worker task contracts over frontend business logic.
- Prefer explicit absolute paths.
- Preserve compatibility with the migrated `knowledge-base` scripts until a shared package exists.
