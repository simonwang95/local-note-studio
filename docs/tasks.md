# Task Guide

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

## Bilibili Favorites Or Series

The migrated scripts already support favorites through configured `BILIBILI_FAV_MEDIA_ID`. The desktop UI should later add QR login and favorite selection.
The desktop UI uses `--limit 1` by default for safe testing. Set the favorite test count to `0` for a full incremental run.

```bash
python3 worker/local_note_studio_worker.py \
  --task bilibili-favorite \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper \
  --favorite-limit 1
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

## Word/PDF Source Conversion

Legacy `.doc` files are supported on macOS through `textutil`, then parsed through the existing DOCX converter. If a complex `.doc` loses layout, save it as `.docx` in Word/WPS and run the task again.
The desktop worker converts the source first, then runs `qwen_organize_notes.py` on the converted Markdown. The organized note keeps the extracted original text at the end.
For one source file, `--output-filename` controls the final Markdown name. Image assets are stored under `assets/<final-file-stem>/`.

```bash
python3 worker/local_note_studio_worker.py \
  --task source-file \
  --source "/path/to/file.doc|/path/to/file.docx|/path/to/file.pdf" \
  --output-dir "/path/to/notes/Inbox"
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

```bash
python3 worker/local_note_studio_worker.py \
  --task local-video \
  --source "/path/to/video.mp4" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper
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

Install `pandoc` first:

```bash
brew install pandoc
# or
conda install -n course-whisper -c conda-forge pandoc
```
