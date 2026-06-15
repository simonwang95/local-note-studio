# Project

## Goal

Local Note Studio is a Mac desktop app that helps users collect local source material and generate clean Markdown notes for long-term use in Obsidian or any folder-based knowledge base.

The first version should make existing command-line workflows easier and safer to run. It should not hide the local nature of the system: users can see which environment, model, source, and output folder are being used.

## Confirmed First Flow

1. Configure runtime settings:
   - conda environment, default `course-whisper`
   - Python command fallback, default `python3`
   - OpenAI-compatible LLM API base
   - API key
   - model name, default `qwen3.6-35b-a3b-nvfp4`
   - optional Bilibili Netscape cookie file
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
| Bilibili single URL | P0 | Subtitle first, ASR fallback through migrated scripts. |
| WeChat or general web URL | P0 | Convert article/page to Markdown; download assets when configured. |
| Word/PDF source conversion | P0 | Convert local files to Markdown drafts. |
| Paper quick read | P0 | Use Qwen-compatible API to generate paper quick-read notes. |
| Local video/audio | P0 | Transcribe one local file or a local directory. |
| Bilibili favorite/series | P1 | Keep worker support; UI can remain minimal until login/cookie flow improves. |
| Video key-frame notes | P1 | Optional Bilibili/local video mode that extracts representative frames and inserts them into image-text Markdown notes. |

## Product Shape

The app should feel like an operator console rather than a marketing website. Dense, clear controls are preferred over decorative UI. The most important information is:

- current environment
- dependency status
- source path or URL
- output path
- exact command preview
- task logs and errors

## Output Policy

The app should not default to writing user notes inside the application repository. It can suggest a path, but the user should choose a notes root or Obsidian vault path early in the flow. Task-specific output paths can be derived from that root.

Suggested task defaults under an output root:

| Task | Suggested subdirectory |
| --- | --- |
| Bilibili URL | `Net/BiliBili` |
| Bilibili favorite | `Net/BiliBili` |
| Web URL | `Net/WeChat` |
| Source file | `Inbox` |
| Paper quick read | `AI/_quickread/AI_paper` |
| Local video/audio | `Net/BiliBili` |

The worker should receive absolute output directories whenever possible.
