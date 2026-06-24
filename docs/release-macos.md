# macOS Release Checklist

The release build produces both `Local Note Studio.app` and a DMG. The app bundle contains the worker source but no mutable Python environment or model weights.

## Internal tester handoff

For a Mac with the same CPU architecture, the DMG is the only Local Note Studio file the tester needs. They do not need the source checkout, Node.js, Rust, Xcode, Homebrew, or conda when using the recommended managed runtime.

The package is not completely self-contained:

- The filename architecture must match the tester Mac: `aarch64` is for Apple Silicon; `x86_64` is for Intel. The current development machine only builds `aarch64`.
- On first use, “应用托管环境 → 安装/修复” downloads the checksum-pinned Python runtime, worker packages, `yt-dlp`, `ffmpeg`, and `ffprobe` into `~/Library/Application Support/Local Note Studio/`. The tester therefore needs network access to the configured download hosts.
- Fresh installs and legacy settings without an explicit runtime preference default to the managed runtime. If the user explicitly selects the advanced Conda backend, that choice, environment name, and optional executable path persist across launches.
- Finder-launched apps do not inherit the interactive shell's `PATH`. The app augments GUI process paths and searches common Miniforge, Miniconda, Anaconda, Homebrew, and system locations. Non-standard Conda installations should be configured with an absolute `.../bin/conda` path in the UI.
- An OpenAI-compatible LLM/OCR service is not bundled. The tester must configure an API URL, key, and model reachable from that Mac. Bilibili private/collection tests additionally need that tester's own Cookie or Chrome Profile; never distribute the developer's credentials.
- Optional ASR models are separate large assets. Pandoc is installed on demand for EPUB tasks.
- Internal DMGs are currently unsigned and unnotarized. Give the tester the SHA-256 checksum through a separate trusted channel. After copying the app to `/Applications`, try Control-click → Open first. If Gatekeeper still blocks a package whose checksum they have verified, they may run `xattr -dr com.apple.quarantine "/Applications/Local Note Studio.app"` for this internal build only. Public distribution must use Developer ID signing and notarization instead.

Recommended handoff steps:

1. Send the matching DMG plus its SHA-256 checksum and this checklist. Do not send `worker/env.local`, cookies, indexes, or personal output data.
2. Open the DMG, drag Local Note Studio to Applications, then launch it.
3. In “配置”, choose “应用托管环境”, enter the tester's API/model and output root, then click “安装/修复”.
4. In “校验”, run “检查依赖” and confirm the managed runtime is complete.
5. Start with a small public webpage or local document before testing account-bound Bilibili or long ASR workflows.

For an internal upgrade, quit Local Note Studio and replace the existing `/Applications/Local Note Studio.app` with the new copy from the DMG. Trashing or replacing only the `.app` preserves Application Support runtime/state and WebView settings. A complete reset is a separate operation and should not be used for normal upgrades.

An Apple Silicon DMG cannot validate Intel compatibility. Produce and test a separate `x86_64` or universal package before claiming both architectures are supported.

## Current internal test build (2026-06-24)

- Version: `0.1.1`
- Architecture: Apple Silicon / `arm64` (`aarch64` artifact suffix)
- Artifact: `Local Note Studio_0.1.1_aarch64.dmg`
- Size: `3,431,921` bytes (about 3.3 MiB)
- SHA-256: `b1fa87a886e4ef2982ce5710f830951d7e36598afd14bad68d9b1252f04710cc`
- Build type: optimized release
- Signature: ad-hoc/linker-signed only; no Developer ID and no notarization
- Verification: `hdiutil verify` passed; the mounted app contains the arm64 executable, worker entry point, locked requirements, scripts, and stock-code reference resource.

This record identifies the current internal artifact only. Rebuilds may produce a different checksum; update this section before handing off a newer DMG.

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
- Chrome Profile access is only used after the user explicitly selects browser-session capture or Bilibili Cookie refresh.
- Network access is used for user-requested sources, configured LLM/OCR APIs, and checksum-verified runtime/tool downloads.

## Clean-Mac acceptance matrix

Use a macOS 12+ account without Homebrew or conda:

1. Install from the DMG and initialize the managed runtime.
2. Verify Python, pinned packages, `yt-dlp`, `ffmpeg`, and `ffprobe`; verify Pandoc installs on first EPUB export.
3. Configure an OpenAI-compatible API and optional Bilibili Cookie/Profile.
4. Run one webpage, document/PDF, paper, local media, Bilibili, and EPUB task.
5. Cancel and resume an OCR task; retry a failed organize step from history.
6. Check output actions, batch result lists, Manifest status, runtime disk usage, repair, and uninstall.
7. Upgrade from the previous application version and confirm Application Support state remains intact.

Signing, notarization, and the clean-machine matrix are release gates, not automated development-test substitutes.
