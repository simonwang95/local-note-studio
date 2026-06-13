# Product Plan

## Goal

Local Note Studio provides a Mac desktop workflow for collecting source material and generating local Markdown notes.

The app should feel like an operator console: choose a source, choose an output folder, choose a model/API profile, run the task, inspect logs, and open the result.

## MVP Features

1. Bilibili single URL.
2. Bilibili favorites or series groundwork.
3. WeChat article and general web page URL.
4. Word/PDF source conversion.
5. Paper quick read.
6. Local video/audio transcription.

The "5 features" product grouping treats Word/PDF conversion and paper quick read as two document modes under the same source-ingest product area, but the worker exposes them as separate tasks because their prompts and outputs differ.

## Non-Goals For MVP

- Bundled local LLM runtime.
- Bundled Whisper model files.
- Full Bilibili QR login UI.
- Full task database and cloud sync.
- macOS signing/notarization.

## First Usable Flow

1. User confirms conda environment, Python fallback, LLM API settings, model, and optional Bilibili cookie file.
2. User runs an environment check and receives dependency installation hints when something is missing.
3. User chooses a default output root, preferably an absolute path outside the app project.
4. User selects a task type.
5. User inputs a URL, file path, or directory path.
6. User previews the worker command.
7. The app runs the Python worker.
8. Logs are shown in the UI.
9. Output Markdown appears under the chosen output root.
