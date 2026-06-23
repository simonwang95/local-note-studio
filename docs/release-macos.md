# macOS Release Checklist

The release build produces both `Local Note Studio.app` and a DMG. The app bundle contains the worker source but no mutable Python environment or model weights.

## Build gate

1. Run `npm run release:check`.
2. Run `npm run tauri:build` with the release signing environment configured.
3. Confirm the bundled worker exists inside the `.app` resources and the app launches without a source checkout.

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
