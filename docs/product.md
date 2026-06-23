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

- Bundled local LLM runtime.
- Bundled Whisper model files.
- Bilibili QR login UI; current releases refresh Cookie from a selected Chrome Profile.
- Full task database and cloud sync.
- macOS signing/notarization.

## First Usable Flow

1. User confirms conda environment, Python fallback, LLM API settings, model, optional Bilibili cookie file, and optional Chrome Profile path.
2. User runs an environment check and receives dependency installation hints when something is missing.
3. User chooses a default output root, preferably an absolute path outside the app project.
4. User selects a task type.
5. User inputs a URL, file path, or directory path.
6. User previews the worker command.
7. The app runs the Python worker.
8. Logs are shown in the UI.
9. Output Markdown appears under the chosen output root.

The canonical prioritized backlog is [`docs/todo.md`](todo.md).
