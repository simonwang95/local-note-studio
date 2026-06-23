# Project

## Goal

Local Note Studio is a Mac desktop app that helps users collect local source material and generate clean Markdown notes for long-term use in Obsidian or any folder-based knowledge base.

The first version should make existing command-line workflows easier and safer to run. It should not hide the local nature of the system: users can see which environment, model, source, and output folder are being used.

## Confirmed First Flow

1. Configure runtime settings:
   - current development mode: conda environment, default `course-whisper`, or Python fallback
   - distribution target: app-managed runtime, with existing conda retained as an advanced mode
   - OpenAI-compatible LLM API base
   - API key
   - model name, default `qwen3.6-35b-a3b-nvfp4`
   - optional Bilibili Netscape cookie file
   - optional Chrome Profile path for refreshing the Bilibili cookie file
2. Check environment dependencies and show installation hints.
3. Choose a default output root, preferably an absolute path outside the app project.
4. Choose a task type.
5. Input a URL, local file path, or directory path.
6. Preview the worker command.
7. Run the task and inspect logs.
8. Find the generated Markdown in the selected output directory.

## MVP Tasks

| Task | Priority | Notes |
| --- | --- | --- |
| Bilibili single URL | Implemented | Subtitle/web/ASR priority, key frames, dialogue detection, terminology checks, and optional raw subtitles. |
| Bilibili favorite/series | Partial | Incremental limited/full processing works; in-app favorite selection and failed-item retry remain pending. |
| Bilibili opus/charging opus | Implemented | Cookie-authenticated API capture followed by Qwen organization. |
| Bilibili UP opus batch | Implemented | Page through one UP account, download images, show per-item progress, and organize with Qwen. |
| WeChat or general web URL | Implemented | Convert and organize while retaining the complete extracted original. |
| Document/source conversion | Implemented | DOC/DOCX/PDF/PPTX/XLSX/CSV/HTML/image/OCR conversion and Qwen organization. |
| Paper quick read | Implemented | Structured paper note, restrained Mermaid mindmap, and full translation. |
| AI-Chat JSON | Implemented | Convert LM Studio conversation JSON and retain the complete dialogue. |
| Local video/audio | Implemented | Single-file or recursive directory transcription with the same rich-video options. |
| Recursive EPUB export | Implemented | Recursively package Markdown and relative image assets through pandoc. |

## Product Shape

The app should feel like an operator console rather than a marketing website. Dense, clear controls are preferred over decorative UI. The most important information is:

- current environment
- dependency status
- source path or URL
- output path
- exact command preview
- task logs and errors

## Output Policy

- Conversion drafts are temporary implementation artifacts. For tasks that require Qwen organization, write drafts outside the selected note directory and publish only the completed Markdown plus its assets.

The app should not default to writing user notes inside the application repository. It can suggest a path, but the user should choose a notes root or Obsidian vault path early in the flow. Task-specific output paths can be derived from that root.

Suggested task defaults under an output root:

| Task | Suggested subdirectory |
| --- | --- |
| Bilibili URL | `Net/BiliBili` |
| Bilibili favorite | `Net/BiliBili` |
| Bilibili opus / UP opus | `Net/BiliBili` |
| Web URL | `Net/WeChat` |
| Source file | `Inbox` |
| AI-Chat JSON | `AI/AI-Chat` |
| Paper quick read | `AI/_quickread/AI_paper` |
| Local video/audio | `Net/BiliBili` |
| EPUB export | `Exports/EPUB` |

The worker should receive absolute output directories whenever possible.

Current priorities and acceptance criteria are tracked only in [`docs/todo.md`](todo.md).

The signed daily-use package is gated on the app-managed runtime. Python, worker packages and non-LLM tools should be versioned under Application Support; LLM/multimodal OCR remains API-configured, and optional ASR model assets are managed separately.
