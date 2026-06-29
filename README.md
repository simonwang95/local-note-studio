# Local Note Studio

Local Note Studio is a local-first macOS desktop app that turns Bilibili videos and posts, web pages, documents, papers, local media, and AI-Chat exports into Obsidian-compatible Markdown.

The app uses a Tauri desktop shell, a thin Rust process bridge, and a packaged Python worker. Notes, task history, indexes, cookies, runtime files, and model settings stay on the user's Mac.

## Current capabilities

- Three-tab desktop workspace: Configuration, Tasks, and Validation, with persistent output and log panels.
- Bilibili single video, favorites/series, opus/charging opus, and one-UP opus batch workflows.
- Web/WeChat, Word/PDF/Office/image/OCR, paper quick-read, AI-Chat JSON, local video/audio, and recursive EPUB export.
- Task history and recovery, structured output actions, progress/cancellation, incognito mode, and editable/batch Manifest state.
- App-managed Python 3.11 runtime with locked packages, `yt-dlp`, `ffmpeg`/`ffprobe`, and on-demand Pandoc.
- Advanced existing-Conda backend for development or users who already maintain a compatible environment.

## Install a test build

For another Mac with the same CPU architecture, the DMG is the only Local Note Studio file that needs to be transferred. The intended managed-runtime path should not require this source checkout, Node.js, Rust, Xcode, Homebrew, or conda, but this path still needs independent clean-Mac validation before it is treated as release-ready.

1. Open the matching DMG (`aarch64` for Apple Silicon, `x86_64` for Intel) and drag Local Note Studio to Applications.
2. Open Configuration, keep **App-managed runtime**, enter the tester's LLM API/model and output root, then click **Install/Repair**.
3. Open Validation and run **Check dependencies** before the first task.

The DMG contains the app and worker, but not an LLM service, personal cookies, indexes, output data, or large ASR models. First-time managed-runtime setup requires network access.

Internal test packages are currently ad-hoc signed rather than Developer ID signed/notarized. Verify the published SHA-256 first, then use Control-click → Open. See [macOS release and tester handoff](docs/release-macos.md) and the [Chinese user guide](docs/user-guide-zh.md) for exact steps.

To upgrade an internal build, quit the app and replace `/Applications/Local Note Studio.app` with the copy from the new DMG. Replacing or trashing only the `.app` preserves settings and managed data under `~/Library/Application Support/Local Note Studio/`. Do not remove that directory during a normal upgrade.

## Runtime selection and persistence

Fresh installs default to the app-managed runtime. Legacy settings that never recorded an explicit runtime preference migrate to managed once.

If a user explicitly selects **Existing Conda / Python (Advanced)**, the selected backend, environment name, Python command, and optional Conda executable path are saved locally and remain selected on the next launch. Finder-launched apps do not inherit the terminal's full `PATH`, so Local Note Studio searches common Miniforge, Miniconda, Anaconda, Homebrew, and system locations. A non-standard installation can be configured with an absolute path such as:

```text
/Users/xxx/miniforge3/bin/conda
```

Managed-runtime requests never pass a saved Conda environment to the worker.

The selected ASR model directory is saved locally, masked by default, and can be revealed or explicitly saved from Configuration. Replaying an old task applies only task parameters; it no longer replaces the current runtime, API, model, ASR, or Cookie configuration with historical values.

The optional model-cooldown override applies to the task's generic and specialized Qwen cooldown variables. Leave it empty to use the stable environment defaults, or set `0` to disable waiting. UP-opus batches wait only between two actual Qwen organization calls—not before the first call, after the last call, or for entries skipped because a complete note already exists.

Cookie refresh follows a least-privilege path: in the signed-in Chrome window open `chrome://version/`, copy “Profile Path”, and select that concrete `Default` or `Profile N` directory. Leave the Cookie file field empty to store filtered Bilibili cookies under the app's Application Support directory, then choose “Authorize and refresh Cookie”. macOS may request access to other app data and Chrome Safe Storage; Local Note Studio does not need Documents, Desktop, Downloads, Apple Music/media-library, network-volume, or removable-volume access for Cookie refresh. Broad profile directories are rejected before any recursive search, and operational tasks with no note-output directory bypass the Markdown output scanner entirely.

## Source development

Requirements: macOS 12+, Node.js, Rust/Tauri prerequisites, and either the managed runtime or a compatible Python/Conda environment.

```bash
npm install
npm run tauri:dev
```

`npm run dev` starts only the Vite preview at `http://127.0.0.1:1420`; it cannot invoke the worker. It is now launched through a small cleanup wrapper so the Vite child exits when its parent process disappears. If an older orphaned development server is already running, stop it with `npm run dev:stop` or Activity Monitor before starting a new desktop session. Machine-specific paths and credentials belong in ignored local settings or `worker/env.local`, never in committed files.

Run all frontend, Python, and Rust regression checks with:

```bash
npm run check
```

Run release configuration checks with:

```bash
npm run release:check
```

## Project layout

```text
local-note-studio/
  src/                    TypeScript UI and shared view state
  src-tauri/              macOS shell, process bridge, runtime manager
  worker/                 Python worker and processing scripts
  tests/                  Frontend and worker regression tests
  docs/                   Product, architecture, environment, release guides
```

## Release status

Development-side `.app`/DMG packaging is working. Developer ID signing, Apple notarization, Intel/universal packaging, and the documented clean-Mac task matrix remain release gates before public distribution.
