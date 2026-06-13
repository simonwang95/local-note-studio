# Task Guide

## Bilibili Single URL

Use this for one video URL. The worker prioritizes web-player subtitles, then `yt-dlp` subtitles, then ASR.

```bash
python3 worker/local_note_studio_worker.py \
  --task bilibili-url \
  --source "https://www.bilibili.com/video/BVxxxx/" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper
```

## Bilibili Favorites Or Series

The migrated scripts already support favorites through configured `BILIBILI_FAV_MEDIA_ID`. The desktop UI should later add QR login and favorite selection.

```bash
python3 worker/local_note_studio_worker.py \
  --task bilibili-favorite \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper
```

## WeChat Article Or Web Page

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

```bash
python3 worker/local_note_studio_worker.py \
  --task paper-quickread \
  --source "/path/to/paper.pdf" \
  --output-dir "/path/to/notes/AI/_quickread/AI_paper"
```

## Local Video Or Audio

```bash
python3 worker/local_note_studio_worker.py \
  --task local-video \
  --source "/path/to/video.mp4" \
  --output-dir "/path/to/notes/Net/BiliBili" \
  --conda-env course-whisper
```
