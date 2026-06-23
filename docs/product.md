# Product Plan

## Goal

Local Note Studio provides a Mac desktop workflow for collecting source material and generating local Markdown notes.

The app should feel like an operator console: choose a source, choose an output folder, choose a model/API profile, run the task, inspect logs, and open the result.

## MVP Features

1. Bilibili video, favorites/series, opus/charging opus, and one-UP opus batch processing.
2. WeChat article and general web page capture.
3. Office/PDF/HTML/table/image/OCR source conversion.
4. Paper quick read with full translation.
5. AI-Chat JSON conversion.
6. Local video/audio transcription and recursive directory processing.
7. Recursive Markdown-to-EPUB export.

Rich video options include transcript-source priority, key-frame notes, dialogue detection and speaker labeling, A-share terminology validation, and optional raw-subtitle retention.

## Non-Goals For MVP

- Bundled local LLM runtime; LLM and multimodal OCR remain user-configured API services.
- Large ASR model files embedded in the `.app`; models may be downloaded or selected through the app-managed resource directory.
- Bilibili QR login UI; current releases refresh Cookie from a selected Chrome Profile.
- Full task database and cloud sync.
- macOS signing/notarization.

## First Usable Flow

1. User chooses the app-managed runtime or an advanced existing conda environment, then confirms LLM API settings, model, optional Bilibili cookie file, and optional Chrome Profile path.
2. User runs an environment check and receives dependency installation hints when something is missing.
3. User chooses a default output root, preferably an absolute path outside the app project.
4. User selects a task type.
5. User inputs a URL, file path, or directory path.
6. User previews the worker command.
7. The app runs the Python worker.
8. Logs are shown in the UI.
9. Output Markdown appears under the chosen output root.

The canonical prioritized backlog is [`docs/todo.md`](todo.md).

## Distribution Principle

The macOS package depends on a working app-managed runtime. Python, worker packages and command-line tools are installed and versioned under Application Support; they are not expected to be present in conda or Homebrew. ASR models are managed separately as optional large assets. Packaging, signing, and notarization begin only after this lifecycle passes clean-Mac installation, upgrade, repair, and removal tests.
