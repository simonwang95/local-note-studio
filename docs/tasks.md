# Task Guide

## Common Task Options

The desktop UI persists these options locally and sends them in the worker request. Equivalent CLI flags include:

- `--overwrite-outputs`: overwrite an existing output instead of skipping it.
- `--output-filename "Custom Name.md"`: rename a single-output task without changing the source file.
- `--extract-keyframes`: add image-text key frames to Bilibili/local video notes.
- `--dialogue-detection`: detect interview/discussion content and label speaker changes in the corrected transcript.
- `--no-keep-original-subtitles`: omit the raw subtitle section from the final video note.
- `--recursive-search`: recursively scan a local media directory.
- `--stock-terms`: validate A-share names/codes during Qwen organization.
- `--enable-ocr`: enable image and scanned-PDF OCR for source conversion.

Only options relevant to the selected task are shown in the UI. Task-level options override the matching `worker/env.local` default for that run without editing the file.

## Bilibili Single URL

Use this for one video URL. By default, the worker prioritizes `yt-dlp`-confirmed subtitles, then ASR. Web-player subtitles can be selected explicitly from the desktop UI.
The output file is written directly into `--output-dir`.
For a single video, add `--output-filename "Custom Name.md"` when you want to keep image assets tied to a stable final file name.

```bash
python3 worker/local_note_studio_worker.py \
  --task bilibili-url \
  --source "https://www.bilibili.com/video/BVxxxx/" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper
```

Add `--dialogue-detection` for interviews or panel discussions. This performs an extra Qwen classification call and only adds speaker labels when the text is detected as dialogue.

## Bilibili Favorites Or Series

The migrated scripts already support favorites through configured `BILIBILI_FAV_MEDIA_ID`. Cookie can be refreshed from the selected Chrome Profile in the desktop UI. In-app favorite selection remains pending.
The desktop UI uses `--limit 1` by default for safe testing. Set the favorite test count to `0` for a full incremental run.

```bash
python3 worker/local_note_studio_worker.py \
  --task bilibili-favorite \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper \
  --favorite-limit 1
```

## Bilibili Opus Or Charging Opus

Use this for one `bilibili.com/opus/...` URL. The worker uses the configured Cookie to request the dynamic API, stages the extracted text and images outside the formal output directory, then writes the Qwen-organized note with the complete original at the end.

```bash
python3 worker/local_note_studio_worker.py \
  --task bilibili-opus \
  --source "https://www.bilibili.com/opus/123456" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --cookies "./bili_cookies.txt"
```

## Bilibili UP Opus Batch

Input an UP-space opus page or UID. `--favorite-limit 0` means all discoverable opus posts. Logs use `[抓取 i/n]` and `[整理 i/n]`; already complete notes are skipped before any Qwen cooldown wait. The UI cooldown override is applied to `QWEN_ORGANIZE_COOLDOWN_DELAY`: blank uses the environment default, `0` disables it, and a positive value waits only between adjacent actual Qwen calls.

```bash
python3 worker/local_note_studio_worker.py \
  --task bilibili-up-opus \
  --source "https://space.bilibili.com/123456/upload/opus" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --cookies "./bili_cookies.txt" \
  --favorite-limit 0 \
  --cooldown-delay 30
```

## WeChat Article Or Web Page

This task first converts the page to Markdown, then organizes the just-converted draft with Qwen into the same output directory.
Add `--output-filename` for a single URL when you want a custom final Markdown file name.

```bash
python3 worker/local_note_studio_worker.py \
  --task web-url \
  --source "https://mp.weixin.qq.com/s/..." \
  --output-dir "/path/to/notes/Net/WeChat"
```

## Source File Conversion

Legacy `.doc` files are supported on macOS through `textutil`, then parsed through the existing DOCX converter. If a complex `.doc` loses layout, save it as `.docx` in Word/WPS and run the task again.
The desktop worker converts the source first, then runs `qwen_organize_notes.py` on the converted Markdown. The organized note keeps the extracted original text at the end.
For one source file, `--output-filename` controls the final Markdown name. Image assets are stored under `assets/<final-file-stem>/`.

Current source types include `.doc`, `.docx`, `.pdf`, `.pptx`, `.xlsx`, `.csv`, `.tsv`, local `.html`, and common images. Images and scanned PDFs prefer the configured multimodal Qwen-compatible API; local OCR tools are fallbacks.

```bash
python3 worker/local_note_studio_worker.py \
  --task source-file \
  --source "/path/to/file.doc|/path/to/file.docx|/path/to/file.pdf" \
  --output-dir "/path/to/notes/Inbox"
```

## AI-Chat JSON

LM Studio `.conversation.json` exports are converted to Markdown and organized with Qwen. The final note keeps source/model metadata and the complete conversation after the organized section.

```bash
python3 worker/local_note_studio_worker.py \
  --task ai-chat \
  --source "/path/to/chat.conversation.json" \
  --output-dir "/path/to/notes/AI/AI-Chat"
```

## Paper Quick Read

Quick read uses up to `QWEN_QUICKREAD_MAX_CHARS` extracted PDF characters by default. The current default is `128000`; set it to `0` in `worker/env.local` if you want no proactive truncation. The generated note keeps a full-translation section when extractable text is available.
Use `--output-filename` for a custom single-paper Markdown name.

```bash
python3 worker/local_note_studio_worker.py \
  --task paper-quickread \
  --source "/path/to/paper.pdf" \
  --output-dir "/path/to/notes/AI/_quickread/AI_paper"
```

## Local Video Or Audio

Generated Markdown is written directly into `--output-dir`; the script no longer appends a `local` subdirectory.
`--output-filename` is supported for a single local media file, but not for directory batch mode.
Directory input scans one level by default; add `--recursive-search` to include subdirectories. A same-stem `.srt` file can be preferred over ASR from the desktop subtitle selector.

```bash
python3 worker/local_note_studio_worker.py \
  --task local-video \
  --source "/path/to/video.mp4" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper
```

For an interview-style local video with no raw subtitle section:

```bash
python3 worker/local_note_studio_worker.py \
  --task local-video \
  --source "/path/to/interview.mp4" \
  --output-dir "/path/to/notes/Interviews" \
  --dialogue-detection \
  --no-keep-original-subtitles
```

## Recursive EPUB Export

This task recursively collects Markdown files under one directory and exports them into one EPUB through `pandoc`.

```bash
python3 worker/local_note_studio_worker.py \
  --task epub-export \
  --source "/path/to/notes/folder" \
  --output-dir "/path/to/notes/Exports/EPUB" \
  --output-filename "My Notes.epub"
```

The app-managed runtime installs `pandoc` during Install/Repair. Existing Conda/Python backends still need `pandoc` installed separately:

```bash
brew install pandoc
# or
conda install -n course-whisper -c conda-forge pandoc
```
