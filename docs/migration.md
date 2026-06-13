# Migration Notes

## Migrated From knowledge-base

The following reusable code has been copied into `worker/scripts/`:

- `convert_sources_to_md.py`
- `quick_read_pdf.py`
- `qwen_organize_notes.py`
- `run_bilibili_transcript.py`
- `bilibili/*`
- Index/frontmatter utilities

Reference documentation from the original project is copied into `docs/reference/knowledge-base-docs/`.

## Important Path Difference

Migrated scripts calculate their project root from their own location. Inside this app, their root is `worker/`.

Therefore:

- `worker/env.local` configures the migrated scripts.
- Relative output paths are relative to `worker/`.
- The desktop app should pass absolute output directories whenever possible.

## Keep In Sync

Until a shared package is extracted, fixes should be ported manually between:

- `KNOWLEDGE_BASE_PROJECT_DIR` from `worker/env.local`
- `LOCAL_NOTE_STUDIO_PROJECT_DIR` from `worker/env.local`

Once the desktop app stabilizes, extract reusable Python modules into a proper package instead of copying scripts.
