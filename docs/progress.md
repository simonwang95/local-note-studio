# Progress

## 2026-06-13

Confirmed the MVP direction:

- The app is a local-first Mac operator console for generating Markdown notes.
- Runtime configuration comes first.
- Default output root comes second.
- Task execution comes after environment and output configuration.
- The first release uses an existing conda environment and OpenAI-compatible LLM API.
- The app must validate dependencies and show installation hints.

Current repository state:

- Tauri shell exists in `src-tauri/`.
- TypeScript frontend exists in `src/`.
- Python worker exists at `worker/local_note_studio_worker.py`.
- Migrated scripts live under `worker/scripts/`.
- Reference docs from `knowledge-base` are stored under `docs/reference/knowledge-base-docs/`.

Next checkpoint:

- Add worker environment validation.
- Update the UI to match the confirmed flow.
- Run available validation.
- Initialize git and commit the first documented development checkpoint.
