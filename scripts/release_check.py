#!/usr/bin/env python3
"""Fail-fast checks for a local macOS release candidate."""

from __future__ import annotations

import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    tauri = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    bundle = tauri.get("bundle", {})
    targets = set(bundle.get("targets", []))
    if not {"app", "dmg"}.issubset(targets):
        errors.append("Tauri bundle targets must include app and dmg")
    resources = bundle.get("resources", [])
    if "../worker/local_note_studio_worker.py" not in resources or "../worker/scripts/*.py" not in resources:
        errors.append("worker resources are not bundled")
    for forbidden in ["env.local", "indexes", "__pycache__"]:
        if any(forbidden in resource for resource in resources):
            errors.append(f"mutable or local-only worker data is bundled: {forbidden}")
    for required in [
        ROOT / "src-tauri/icons/icon.png",
        ROOT / "worker/requirements-managed.lock",
        ROOT / "docs/release-macos.md",
    ]:
        if not required.exists():
            errors.append(f"missing release artifact: {required.relative_to(ROOT)}")
    source = (ROOT / "src-tauri/src/main.rs").read_text(encoding="utf-8")
    for marker in ["PYTHON_STANDALONE_TAG", "install_managed_media_tools", "install_managed_pandoc"]:
        if marker not in source:
            errors.append(f"managed-runtime marker is missing: {marker}")
    if errors:
        print("release checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("release configuration is internally consistent")
    print("manual gates remain: Developer ID signing, notarization, and clean-Mac end-to-end acceptance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
