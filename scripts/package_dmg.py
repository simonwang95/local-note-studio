#!/usr/bin/env python3
"""Package an already-built app with an Applications drop target and verify it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> bytes:
    return subprocess.check_output(args, stderr=subprocess.STDOUT)


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def main() -> None:
    cfg = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())
    name, version = cfg["productName"], cfg["version"]
    bundle = ROOT / "src-tauri/target/release/bundle"
    app = bundle / "macos" / f"{name}.app"
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if any(info.get(key) != version for key in ("CFBundleVersion", "CFBundleShortVersionString")):
        raise RuntimeError("Build the current app version before packaging")
    archs = run("/usr/bin/lipo", "-archs", str(app / "Contents/MacOS" / info["CFBundleExecutable"])).decode().strip()
    arch = {"arm64": "aarch64", "x86_64": "x86_64"}.get(archs)
    if arch is None:
        raise RuntimeError(f"Unsupported artifact architecture: {archs}")
    output = bundle / "dmg" / f"{name}_{version}_{arch}.dmg"
    mounted = plistlib.loads(run("/usr/bin/hdiutil", "info", "-plist"))
    if any(Path(image.get("image-path", "")).resolve() == output.resolve() for image in mounted.get("images", [])):
        raise RuntimeError(f"Eject the existing image before replacing it: {output}")
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    for path in app.rglob("*"):
        if path.name in {"env.local", "__pycache__"} or path.suffix == ".pyc":
            raise RuntimeError(f"Local-only content in app: {path}")
    expected = tree_hashes(app)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lns-dmg-", dir=output.parent) as temporary:
        work = Path(temporary)
        staging = work / "staging"
        staging.mkdir()
        run("/usr/bin/ditto", str(app), str(staging / app.name))
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        candidate = work / "candidate.dmg"
        print("Creating APFS DMG with app and Applications shortcut...", flush=True)
        run("/usr/bin/hdiutil", "create", "-volname", name, "-srcfolder", str(staging),
            "-fs", "APFS", "-format", "UDZO", str(candidate))
        run("/usr/bin/hdiutil", "verify", str(candidate))
        mount = work / "mounted"
        mount.mkdir()
        run("/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(candidate))
        try:
            shortcut = mount / "Applications"
            if not shortcut.is_symlink() or os.readlink(shortcut) != "/Applications" or not shortcut.is_dir():
                raise RuntimeError("DMG is missing a usable Applications shortcut")
            mounted_app = mount / app.name
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(mounted_app))
            if tree_hashes(mounted_app) != expected:
                raise RuntimeError("Mounted app differs from the signed build")
        finally:
            run("/usr/bin/hdiutil", "detach", str(mount))
        os.replace(candidate, output)
    print(json.dumps({"artifact": str(output), "bytes": output.stat().st_size,
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                      "applications_shortcut": "/Applications", "version": version}, indent=2))


if __name__ == "__main__":
    main()
