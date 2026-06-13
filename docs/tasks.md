# Task Guide

## Bilibili Single URL

Use this for one video URL. By default, the worker prioritizes `yt-dlp`-confirmed subtitles, then ASR. Web-player subtitles can be selected explicitly from the desktop UI.
The output file is written directly into `--output-dir`.

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

```bash
python3 worker/local_note_studio_worker.py \
  --task web-url \
  --source "https://mp.weixin.qq.com/s/..." \
  --output-dir "/path/to/notes/Net/WeChat"
```

## Word/PDF Source Conversion

```bash
python3 worker/local_note_studio_worker.py \
  --task source-file \
  --source "/path/to/file.docx" \
  --output-dir "/path/to/notes/Inbox"
```

## Paper Quick Read

Quick read uses up to `QWEN_QUICKREAD_MAX_CHARS` extracted PDF characters by default. The current default is `128000`; set it to `0` in `worker/env.local` if you want no proactive truncation. The generated note keeps a full-translation section when extractable text is available.

```bash
python3 worker/local_note_studio_worker.py \
  --task paper-quickread \
  --source "/path/to/paper.pdf" \
  --output-dir "/path/to/notes/AI/_quickread/AI_paper"
```

## Local Video Or Audio

Generated Markdown is written directly into `--output-dir`; the script no longer appends a `local` subdirectory.

```bash
python3 worker/local_note_studio_worker.py \
  --task local-video \
  --source "/path/to/video.mp4" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper
```
