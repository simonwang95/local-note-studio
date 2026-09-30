# Development Plan

Updated: 2026-09-29. Baseline: `0.1.28` / `6c1bb1b`.

This document describes development stages. Task status, dependencies, scope, and acceptance criteria are maintained only in the [canonical backlog](todo.md). “Baseline completed” means the initial workflow is available; it does not imply that the newly reviewed reliability gaps are resolved.

## Stage 1: Useful Local Operator Console (Completed)

Goal: make the desktop app able to validate the user's existing environment and run the migrated worker tasks with clear inputs and logs.

Deliverables:

- Runtime configuration section appears before task selection.
- Default output root is configured before per-task output path.
- Environment check runs through the worker and reports required and optional dependencies.
- Dependency failures include installation hints.
- Task preview remains available through dry-run.
- Task execution still uses the migrated Python scripts.

## Stage 2: Better Desktop Ergonomics (Baseline Completed)

Goal: reduce manual path entry and make common operations feel native.

Delivered baseline:

- Add native file and directory pickers. (Done for current path inputs.)
- Add an "open output folder" action. (Done through structured output actions.)
- Stream logs incrementally instead of returning them after completion. (Done for current worker runs.)
- Add cancel/retry controls. (Cancel, rerun, failed-step recovery, and failed-item retry are available.)
- Refresh Bilibili Cookie from a selected Chrome Profile. (Done.)
- Mask API Key, Cookie path, and browser profile path by default. (Done.)

Delivered on `codex/p0-p1-backlog`: T-115 stage-specific effective parameters and T-201 named desktop configuration sets. These desktop configurations preserve the separate permission boundaries of Agent Profiles.

## Stage 3: Task History And Recovery (Baseline Completed)

Goal: make long-running and batch tasks easier to manage.

Delivered baseline:

- Store task history in local persistent storage. (Done.)
- Persist bounded logs per run. (Done.)
- Track generated Markdown paths and batch output lists. (Done.)
- Add task status, rerun, recovery, and failure diagnostics. (Done.)
- Surface manifest/index status from migrated scripts. (Done.)

Delivered on `codex/p0-p1-backlog`: T-114 resumable proofreading checkpoints and bounded quality splits, plus the T-205 serial task queue. The queue reuses the existing Worker and global write lock; it does not introduce a second execution system.

## Stage 4: Rich Video Notes (First Pass Completed)

Goal: make Bilibili and local video notes more useful when visual context matters.

Delivered baseline:

- Add an option for Bilibili and local video tasks to extract key frames using note-section position estimates and scene times. (Done for the first pass; transcript timestamp alignment is a follow-up.)
- Save key frames as local assets next to the generated Markdown. (Done for the first pass.)
- Insert selected frames into the note at relevant transcript sections to create image-text notes. (Done for the first pass.)
- Detect dialogue/interview content and label speaker changes in the corrected transcript. (Done for the first pass.)
- Avoid excessive screenshots by deduplicating visually similar frames and limiting frames per section/video. (Done.)
- Record extracted frame paths in `video-manifest.json` for later cleanup or regeneration. (Done.)

Delivered on `codex/p0-p1-backlog`: T-116 content-change review prompts and T-117 transcript timestamp alignment. Mechanical checks and human review prompts do not guarantee semantic accuracy; weak or absent timestamp matches remain explicitly estimated.

## Stage 5: App-Managed Runtime (Implemented, Awaiting Clean-Mac Validation)

Goal: make the main workflows usable on a clean Mac without requiring conda or Homebrew, while preserving existing conda selection for advanced users.

Candidate work:

- Install a relocatable Python runtime and pinned worker dependencies under Application Support rather than inside the signed `.app` bundle.
- Install and verify managed `ffmpeg` / `ffprobe`; make `yt-dlp` independently updateable; install `pandoc` during install/repair as a best-effort EPUB component whose failure does not block other workflows.
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

The current development branch has completed P0 and P1 work for completion status, safe video output promotion, resumable proofreading, stage-specific configuration, transcript review and alignment, desktop profiles, and the serial task queue. The branch review fixes and deterministic regressions are recorded in `docs/progress.md`; live-model and independent GUI acceptance remain outside that evidence. These changes are not in the published 0.1.28 package. T-118 cache management and diagnostics is the next planned backlog item; exact ordering and acceptance criteria are maintained in [`docs/todo.md`](todo.md).

The independent runtime and signed-package acceptance gates remain separate from this development sequence. An internal DMG build does not close those gates.

## Implementation Principles

- Keep Rust thin.
- Keep heavy processing in Python.
- Prefer worker task contracts over frontend business logic.
- Prefer explicit absolute paths.
- Preserve compatibility with the migrated `knowledge-base` scripts until a shared package exists.
