# macOS Release Checklist

The release build produces both `Local Note Studio.app` and a DMG. The app bundle contains the worker source but no mutable Python environment or model weights.

## Internal tester handoff

For a Mac with the same CPU architecture, the DMG is the only Local Note Studio file the tester needs. They do not need the source checkout, Node.js, Rust, Xcode, Homebrew, or conda when using the recommended managed runtime.

The package is not completely self-contained:

- The filename architecture must match the tester Mac: `aarch64` is for Apple Silicon; `x86_64` is for Intel. The current development machine only builds `aarch64`.
- On first use, “应用托管环境 → 安装/修复” downloads the checksum-pinned Python runtime, worker packages, `yt-dlp`, `mlx-whisper`, `ffmpeg`, `ffprobe`, `pandoc`, and the default MLX Whisper model into `~/Library/Application Support/Local Note Studio/`. The tester therefore needs network access to the configured download hosts. Runtime/tool downloads prefer HTTP/1.1 before retrying the default protocol to avoid fragile HTTP/2 paths. Locked Python dependencies are first installed from the default pip/PyPI configuration; TLS/proxy/timeout failures automatically retry through fallback PyPI mirrors. If a test network requires a specific mirror, launch the app with `LOCAL_NOTE_STUDIO_PIP_INDEX_URL=https://.../simple`; if the Python runtime archive itself is mirrored, use `LOCAL_NOTE_STUDIO_PYTHON_RUNTIME_URL=https://.../cpython-...tar.gz`; if a tool archive is mirrored, use `LOCAL_NOTE_STUDIO_PANDOC_URL`, `LOCAL_NOTE_STUDIO_FFMPEG_URL`, or `LOCAL_NOTE_STUDIO_FFPROBE_URL`. Default ASR model downloads try Hugging Face first and then `https://hf-mirror.com`; use `LOCAL_NOTE_STUDIO_HF_ENDPOINT=https://...` for another Hugging Face-compatible endpoint.
- Fresh installs and legacy settings without an explicit runtime preference default to the managed runtime. If the user explicitly selects the advanced Conda backend, that choice, environment name, and optional executable path persist across launches.
- Finder-launched apps do not inherit the interactive shell's `PATH`. The app augments GUI process paths and searches common Miniforge, Miniconda, Anaconda, Homebrew, and system locations. Non-standard Conda installations should be configured with an absolute `.../bin/conda` path in the UI.
- An OpenAI-compatible LLM/OCR service is not bundled. The tester must configure an API URL, key, and model reachable from that Mac. Bilibili private/collection tests additionally need that tester's own Cookie or Chrome Profile; never distribute the developer's credentials.
- The app bundle does not embed ASR model weights, but managed “安装/修复” downloads the default MLX Whisper model into Application Support together with the runtime. Pandoc is installed during “安装/修复” rather than on first EPUB export.
- In managed mode, nested Bilibili ASR scripts are pinned to the app-managed Python executable; dependency checks use real `mlx_whisper` imports rather than package metadata probes.
- Pandoc is best-effort during managed install/repair because it is only required for EPUB export. If the GitHub/CDN route fails, initialization continues for video, document, OCR, Cookie and ASR workflows; status will remain “需要修复” until Pandoc is installed.
- Internal apps use a complete ad-hoc signature and the DMGs are not Developer ID signed or notarized. Give the tester the SHA-256 checksum through a separate trusted channel. After copying the app to `/Applications`, try Control-click → Open first. If Gatekeeper still blocks a package whose checksum they have verified, they may run `xattr -dr com.apple.quarantine "/Applications/Local Note Studio.app"` for this internal build only. Public distribution must use Developer ID signing and notarization instead.

Recommended handoff steps:

1. Send the matching DMG plus its SHA-256 checksum and this checklist. Do not send `worker/env.local`, cookies, indexes, or personal output data.
2. Open the DMG, drag Local Note Studio to Applications, then launch it.
3. In “配置”, choose “应用托管环境”, enter the tester's API/model and output root, then click “安装/修复”.
4. In “校验”, run “检查依赖” and confirm the managed runtime is complete.
5. Start with a small public webpage or local document before testing account-bound Bilibili or long ASR workflows.

For an internal upgrade, quit Local Note Studio and replace the existing `/Applications/Local Note Studio.app` with the new copy from the DMG. Trashing or replacing only the `.app` preserves Application Support runtime/state and WebView settings. A complete reset is a separate operation and should not be used for normal upgrades.

An Apple Silicon DMG cannot validate Intel compatibility. Produce and test a separate `x86_64` or universal package before claiming both architectures are supported.

## 0.1.18 internal Apple Silicon artifact (2026-07-22)

- Version: `0.1.18`
- Architecture: Apple Silicon / `arm64` (`aarch64` artifact suffix)
- Artifact: `src-tauri/target/release/bundle/dmg/Local Note Studio_0.1.18_aarch64.dmg`
- Size: `3,464,315 bytes`
- SHA-256: `cc15118bb6a0d9e502df5386d7b412e25cca30e69824737e6c96cef2336fc31f`
- Build type: optimized release
- Signature: complete ad-hoc app signature with sealed resources; no Developer ID and no notarization

`hdiutil verify` passed. The DMG was then mounted read-only into a dedicated `/private/tmp` directory; `codesign --verify --deep --strict` passed against the mounted app, its executable reported Mach-O 64-bit `arm64`, and both `CFBundleShortVersionString` and `CFBundleVersion` reported `0.1.18`. The mounted resources contain Worker/Agent/MCP entry points, locked requirements, the stock-code reference resource, cache Schema 2.0/rules-version validation, the `4096`/`8192` image token budget, `300`-second timeout, deterministic time/stock guards, and Worker version `0.1.18`; no `env.local` is present.

Before packaging, both designated opuses passed fresh-cache OpenHanako-compatible stdio MCP acceptance and immediate zero-model-call cache reruns in an isolated `/private/tmp` state/output tree. The final source checks passed with Node.js `20.20.2`, course-whisper Python `3.11.15`, 94 Python tests, and 10 Rust tests. This artifact was not installed and did not replace the current `/Applications` copy. Developer ID signing, notarization, Intel/universal packaging, a clean-Mac managed-runtime matrix, and formal OpenHanako UI import remain separate release gates.

## Superseded 0.1.17 problem baseline (2026-07-21)

The following artifact passed static package checks but later failed fresh-cache real visual acceptance. Do not install it or use it for formal automation:

- Version: `0.1.17`
- Architecture: Apple Silicon / `arm64` (`aarch64` artifact suffix)
- Artifact: `Local Note Studio_0.1.17_aarch64.dmg`
- Size: `3,460,489 bytes`
- SHA-256: `4934f073cb386eb632d34c97717c3a341146d07db8659e5cfdf5004144d164e4`
- Build type: optimized release
- Signature: complete ad-hoc app signature with hardened runtime and sealed resources; no Developer ID and no notarization
- Static verification: `npm run release:check`, `hdiutil verify`, and strict deep `codesign` verification against the app mounted from the DMG passed. The mounted app reports version `0.1.17`, contains the arm64 executable, Worker/Agent/MCP entry points, locked requirements, scripts and stock-code reference resource, and contains no `env.local`.

Fresh-cache runtime failure: one simple interaction-ranking image continued for more than eight minutes and about 28,314 generated tokens because the request had no `max_tokens`; other outputs contained an unsupported “应为2024年” correction and wrong A-share exchange suffixes. Cancellation and cleanup succeeded, but the content/performance acceptance did not. The verified `0.1.18` replacement is recorded above; retain this entry only as the problem baseline.

Spotlight may also index `src-tauri/target/release` and `src-tauri/target/debug`. These are release/debug build products, while `/Applications/Local Note Studio.app` is the installed copy; they are not separate data installations and share Application Support state. Normal build/test/start commands must not delete either target directory automatically. Clean them manually only after resolving and confirming the exact project path.

## Build gate

1. Run `npm run release:check`.
2. Run `npm run tauri:build` with the release signing environment configured.
3. Confirm the bundled worker exists inside the `.app` resources and the app launches without a source checkout.
4. Record the artifact filename, architecture, size, and SHA-256 checksum for the tester handoff.

## Signing and notarization

- Configure an Apple Developer ID Application certificate for the Tauri build.
- Sign the application and DMG, submit them to Apple notarization, staple the ticket, then run `spctl --assess` on a clean machine.
- Keep mutable runtimes, tools, indexes, recovery drafts, OCR checkpoints, and models under `~/Library/Application Support/Local Note Studio/`; never modify the signed app bundle.

## First-launch permissions

- File and folder access is requested only when the user selects a source or output path.
- Chrome Profile access is only used after the user explicitly selects browser-session capture or Bilibili Cookie refresh. Cookie refresh accepts only a concrete leaf Profile and defaults its filtered output to the app's Application Support directory.
- Expected Cookie-refresh prompts are other-app-data access and, depending on Chrome state, Safe Storage/Keychain confirmation. Desktop, Documents, Downloads, Apple Music/media-library, network volumes, and removable volumes are not required; broad directories are rejected before extraction and an empty note-output path cannot start output scanning.
- Network access is used for user-requested sources, configured LLM/OCR APIs, and checksum-verified runtime/tool downloads.

## Clean-Mac acceptance matrix

Use a macOS 12+ account without Homebrew or conda:

1. Install from the DMG and initialize the managed runtime.
2. Verify Python, pinned packages including `mlx-whisper`, `yt-dlp`, `ffmpeg`, `ffprobe`, and `pandoc`.
3. Configure an OpenAI-compatible API and optional Bilibili Cookie/Profile.
4. Run one webpage, document/PDF, paper, local media, Bilibili, and EPUB task.
5. Cancel and resume an OCR task; retry a failed organize step from history.
6. Check output actions, batch result lists, Manifest status, runtime disk usage, repair, and uninstall.
7. Upgrade from the previous application version and confirm Application Support state remains intact.

Signing, notarization, and the clean-machine matrix are release gates, not automated development-test substitutes.
