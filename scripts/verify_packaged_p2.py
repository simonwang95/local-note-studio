#!/usr/bin/env python3
"""Verify a sealed macOS bundle and run isolated T-118 acceptance.

Use --app on a built bundle or an app on a read-only mounted DMG. Only
temporary fixture data is written; the installed app, real caches, environment
configuration and models are never used. --self-test verifies the acceptance
fixture against a temporary copy of the source resource whitelist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import select
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_MARKERS = (
    "cachePreviewClean", "cacheClean", "cacheSavePolicy", "cacheAutoEnabled",
    "cacheIncludeOriginal", "cacheIncludePaths", "cacheExport",
    "缓存管理与任务诊断", "定位诊断与恢复材料",
)
CATEGORIES = {
    "source_evidence", "asr_diagnostics", "success_checkpoints",
    "failed_recovery", "disposable_cache",
}
PRIVATE_BODY = "PACKAGED_ACCEPTANCE_PRIVATE_ORIGINAL"
PRIVATE_KEY = "sk-packaged-acceptance-fixture-secret"
PRIVATE_COOKIE = "packaged-acceptance-cookie-secret"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(path: Path) -> dict[str, str]:
    return {str(item.relative_to(path)): sha256(item)
            for item in sorted(path.rglob("*")) if item.is_file()}


def checked(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, f"{Path(args[0]).name} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def configured_resources(config: dict) -> dict[str, Path]:
    resources = {}
    for pattern in config["bundle"]["resources"]:
        require(pattern.startswith("../"), f"Unsupported resource mapping: {pattern}")
        matched = sorted(ROOT.glob(pattern[3:]))
        require(bool(matched), f"Resource pattern has no source files: {pattern}")
        for source in matched:
            require(source.is_file() and not source.is_symlink(), f"Unsafe source resource: {source}")
            resources[str(source.relative_to(ROOT))] = source
    return resources


def verify_frontend(executable_bytes: bytes) -> dict[str, str]:
    """Tauri normally stores Brotli assets in Mach-O, not readable JS strings.

    Find whole generated compressed assets in the actual executable, then
    decompress and compare with the current Vite output. This checks what is
    shipped without launching or modifying the app.
    """
    try:
        import brotli
    except ImportError as exc:
        raise RuntimeError("Packaged frontend verification requires the available Python brotli module") from exc
    embedded = []
    build_root = ROOT / "src-tauri/target/release/build"
    for suffix in ("js", "css"):
        for path in build_root.glob(f"local-note-studio-*/out/tauri-codegen-assets/*.{suffix}"):
            compressed = path.read_bytes()
            if compressed in executable_bytes:
                embedded.append(brotli.decompress(compressed))
    assets = sorted((ROOT / "dist/assets").glob("*"))
    require(bool(assets), "Current Vite assets are absent; build the frontend before acceptance")
    verified = {}
    contents = b""
    for path in assets:
        require(path.is_file(), f"Unexpected frontend asset directory: {path.name}")
        payload = path.read_bytes()
        require(payload in embedded or payload in executable_bytes, f"Current frontend asset is absent from the executable: {path.name}")
        verified[path.name] = hashlib.sha256(payload).hexdigest()
        contents += payload
    missing = [marker for marker in FRONTEND_MARKERS if marker.encode("utf-8") not in contents]
    require(not missing, "Packaged frontend markers are missing: " + ", ".join(missing))
    return verified


def verify_bundle(app: Path, config: dict) -> tuple[Path, dict]:
    require(app.is_absolute() and app.is_dir() and app.suffix == ".app", "--app must name an absolute .app directory")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    version = config["version"]
    for key in ("CFBundleVersion", "CFBundleShortVersionString"):
        require(info.get(key) == version, f"{key} does not match source version {version}")
    executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
    architecture = checked("/usr/bin/lipo", "-archs", str(executable))
    require(architecture == "arm64", f"Expected arm64 app; found {architecture}")
    checked("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    forbidden_names = {"env.local", ".env", "__pycache__", "cookies.txt", "cookies.json",
                       "credentials.json", "credentials", ".aws", ".ssh", "id_rsa", "id_ed25519"}
    for item in app.rglob("*"):
        require(item.name.lower() not in forbidden_names and item.suffix.lower() not in {".pyc", ".pyo", ".pem", ".key"},
                f"Local-only or credential file in bundle: {item.relative_to(app)}")
    resource_root = app / "Contents/Resources/_up_"
    expected = configured_resources(config)
    actual = tree_hashes(resource_root)
    require(set(actual) == set(expected), "Bundle resource whitelist differs from configured source resources")
    for relative, source in expected.items():
        require(actual[relative] == sha256(source), f"Bundled resource differs from source: {relative}")
    frontend_hashes = verify_frontend(executable.read_bytes())
    return resource_root / "worker", {
        "app": str(app), "version": version, "architecture": architecture,
        "codesign_verified": True, "source_resource_hashes_verified": len(expected),
        "local_only_files_absent": True, "frontend_markers_verified": list(FRONTEND_MARKERS),
        "embedded_frontend_sha256": frontend_hashes,
    }


FIXTURE_CODE = r'''
import json
import os
from pathlib import Path
import sys
import time
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]) / "scripts")]
from automation_core import WORKER_VERSION
from transcript_quality import save_transcript_diagnostic, load_transcript_diagnostic
import task_diagnostics as diagnostics
import convert_sources_to_md as convert

root = Path(sys.argv[2])
private_body, private_key, private_cookie = sys.argv[3:6]
paths = {}
def context(run, source):
    os.environ["LOCAL_NOTE_STUDIO_RUN_ID"] = run
    os.environ["LOCAL_NOTE_STUDIO_SOURCE_REF"] = source
    diagnostics.start_run(effective_config={"model": "fixture-model", "api_key": private_key})

def artifact(name, kind, run, source, errors=None):
    context(run, source)
    payload = {"text": private_body + " /Users/fixture/notes.md\nCookie: session=" + private_cookie,
               "api_key": private_key, "segments": [{"start": 0, "end": 2, "text": private_body}]}
    if errors:
        payload["errors"] = errors
    path = Path(save_transcript_diagnostic(kind, name, payload))
    loaded = load_transcript_diagnostic(kind, name)
    assert loaded["cache_metadata"]["run_id"] == run
    assert loaded["cache_metadata"]["source_ref"] == source
    assert path.stat().st_mode & 0o777 == 0o600
    with diagnostics.stage_span("proofread" if "proofread" in kind else "asr"):
        diagnostics.record_cache_hit("proofread" if "proofread" in kind else "asr", kind)
    if name == "asr":
        with diagnostics.stage_span("asr"):
            call = diagnostics.begin_model_call("asr", "fixture-asr", provider="local_asr")
            diagnostics.finish_model_call(call, "asr", "completed")
        with diagnostics.stage_span("proofread"):
            call = diagnostics.begin_model_call("proofread", "fixture-model")
            diagnostics.finish_model_call(call, "proofread", "completed", usage={"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11})
            diagnostics.record_retry("proofread", "quality")
    summary = diagnostics.finalize_run("failed" if errors else "completed")
    if name == "asr":
        assert summary["metrics"]["model_calls"] == 2
        assert summary["metrics"]["asr_calls"] == 1
        assert summary["metrics"]["llm_calls"] == 1
        assert summary["metrics"]["total_tokens"] == 11
        assert summary["metrics"]["reasoning_tokens"] is None
    old = time.time() - 90 * 86400
    os.utime(path, (old, old))
    paths[name] = str(path)

artifact("source", "source", "source-run", "https://www.bilibili.com/video/BVPackagedSource")
artifact("asr", "asr", "asr-run", str(root / "source.mp4"))
artifact("proofread", "proofread-checkpoints", "proofread-run", "https://www.bilibili.com/video/BVPackagedCheckpoint")
artifact("failed", "proofread-rejected", "failed-run", "https://www.bilibili.com/video/BVPackagedFailed", ["quality gate failed"])
artifact("changed", "source", "changed-run", "https://www.bilibili.com/video/BVPackagedChanged")

context("image-run", "https://www.bilibili.com/opus/1234")
cfg = {"DEFAULT_LLM_MODEL": "fixture-model", "OPUS_IMAGE_ANALYSIS_CACHE_DIR": str(root / "state/opus-image-analysis-cache")}
convert.save_opus_image_analysis_cache(cfg, "a" * 64, "vision", "context", {"visible_text": private_body})
image = convert.opus_image_cache_path(cfg, "a" * 64, "vision", "context")
assert convert.load_opus_image_analysis_cache(cfg, "a" * 64, "vision", "context")["visible_text"] == private_body
diagnostics.finalize_run("completed")
old = time.time() - 90 * 86400
os.utime(image, (old, old))
paths["disposable"] = str(image)
convert.save_opus_image_analysis_cache(cfg, "b" * 64, "vision", "context", {"visible_text": "recent cache retained by policy"})
paths["recent_disposable"] = str(convert.opus_image_cache_path(cfg, "b" * 64, "vision", "context"))

context("unknown-run", "file:fixture")
unknown = diagnostics.finalize_run("completed")
assert unknown["metrics"]["model_calls"] is None
assert unknown["metrics"]["total_tokens"] is None
legacy = root / "state/transcripts/source/legacy.json"
legacy.write_text(json.dumps({"transcript": private_body}))
paths["legacy"] = str(legacy)
recovery = root / "state/recovery/failed-run/draft.md"
recovery.parent.mkdir(parents=True)
recovery.write_text(private_body)
paths["recovery"] = str(recovery)
staging = root / "notes/.local-note-studio-staging/transaction-run/draft.md"
staging.parent.mkdir(parents=True)
staging.write_text(private_body)
journal = root / "notes/.local-note-studio-transactions/transaction-run.json"
journal.parent.mkdir(parents=True)
journal.write_text(json.dumps({"state": "rolled_back", "run_id": "transaction-run", "stage_dir": str(staging.parent)}))
paths["staging"] = str(staging)
paths["journal"] = str(journal)
print(json.dumps({"worker_version": WORKER_VERSION, "paths": paths}))
'''


def isolated_env(root: Path) -> dict[str, str]:
    # Keep system option variables untouched. Explicit app/state/cache paths
    # isolate all Worker state; no API configuration is inherited.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1",
        "LOCAL_NOTE_STUDIO_APP_DATA_DIR": str(root),
        "LOCAL_NOTE_STUDIO_STATE_DIR": str(root / "state"),
        "LOCAL_NOTE_STUDIO_OUTPUT_DIR": str(root / "notes"),
        "LOCAL_NOTE_STUDIO_TASK": "local-video", "LOCAL_NOTE_STUDIO_INCOGNITO": "false",
        "TRANSCRIPT_CACHE_DIR": str(root / "state/transcripts"),
        "TMPDIR": str(root / "tmp"),
    })
    return env


def run_fixture(worker: Path, root: Path, env: dict) -> dict:
    result = subprocess.run([sys.executable, "-B", "-c", FIXTURE_CODE, str(worker), str(root),
                             PRIVATE_BODY, PRIVATE_KEY, PRIVATE_COOKIE],
                            env=env, cwd=root, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, "Fixture generation failed: " + result.stderr.strip())
    return json.loads(result.stdout)


class WorkerClient:
    def __init__(self, worker: Path, root: Path, env: dict):
        self.worker, self.root, self.env = worker, root, env
        self.calls = 0

    def request(self, action: str, options: dict | None = None, *, error: str = "", dry_run: bool = False) -> dict:
        self.calls += 1
        request = {"task": "cache-manage", "caller": "cli", "cache_action": action,
                   "cache_options": options or {}, "output_dir": str(self.root / "notes"),
                   "runtime_backend": "managed", "dry_run": dry_run}
        process = subprocess.run([sys.executable, "-B", str(self.worker / "local_note_studio_worker.py"), "--request-stdin"],
                                 input=json.dumps(request), cwd=self.root, env=self.env,
                                 capture_output=True, text=True, timeout=30)
        records = [json.loads(line[len("TASK_RESULT_JSON:"):]) for line in process.stdout.splitlines()
                   if line.startswith("TASK_RESULT_JSON:")]
        require(len(records) == 1, f"Worker {action} did not emit exactly one result")
        result = records[0]
        if error:
            require(process.returncode == 1 and (result.get("error") or {}).get("error_code") == error,
                    f"Worker {action} did not reject request with {error}")
            return result
        require(process.returncode == 0 and result["status"] == "completed",
                f"Worker {action} failed: {result.get('error')}")
        return result["details"]["cache"]


def acceptance(worker: Path, version: str) -> dict:
    require(not (worker / "env.local").exists(), "Worker env.local must be absent before acceptance")
    with tempfile.TemporaryDirectory(prefix="lns-p2-acceptance-") as temporary:
        root = Path(temporary).resolve()
        for directory in (root / "state", root / "notes/assets", root / "models", root / "tmp"):
            directory.mkdir(parents=True)
        source, note, asset, model = root / "source.mp4", root / "notes/formal.md", root / "notes/assets/formal.png", root / "models/sentinel.bin"
        source.write_bytes(b"original media sentinel")
        note.write_text("# Formal note\n\n![asset](assets/formal.png)\n", encoding="utf-8")
        asset.write_bytes(b"formal image asset sentinel")
        model.write_bytes(b"managed model sentinel")
        sentinels = {path: sha256(path) for path in (source, note, asset, model)}
        env = isolated_env(root)
        fixture = run_fixture(worker, root, env)
        require(fixture["worker_version"] == version, "Packaged Worker version differs from the release")
        paths = {key: Path(value) for key, value in fixture["paths"].items()}
        recovery_hashes = {path: sha256(path) for key, path in paths.items() if key in {"failed", "legacy", "recovery", "staging", "journal"}}
        recent_digest = sha256(paths["recent_disposable"])
        client = WorkerClient(worker, root, env)
        inventory = client.request("inventory")
        require(not (root / "state/automation-history.sqlite3").exists(), "Maintenance wrote a processing history")
        require({item["category"] for item in inventory["items"]} == CATEGORIES, "Inventory is missing cache categories")
        by_path = {item["path"]: item for item in inventory["items"]}
        for key in ("failed", "legacy", "recovery", "staging"):
            require(by_path[str(paths[key])]["protected"], f"{key} fixture is not protected")
        diagnostics = {item["run_id"]: item for item in inventory["diagnostics"]}
        require(diagnostics["asr-run"]["model_calls"] == 2 and diagnostics["asr-run"]["total_tokens"] == 11,
                "Model call/token diagnostics differ from recorded events")
        require(diagnostics["asr-run"]["cache_hits"] == 1 and diagnostics["asr-run"]["retries"] == [{"stage": "proofread", "reason": "quality", "count": 1}],
                "Cache-hit/retry diagnostics differ from recorded events")
        require(diagnostics["unknown-run"]["total_tokens"] is None and diagnostics["unknown-run"]["model_calls"] is None,
                "Missing diagnostic values were estimated")
        require(inventory["summary"]["diagnostics_bytes"] > 0, "Diagnostic audit storage is unreported")

        policy = client.request("policy")
        require(policy["auto_enabled"] is False, "Automatic cleanup is enabled by default")
        require(client.request("auto")["deleted_bytes"] == 0 and paths["disposable"].exists(), "Disabled automatic cleanup removed data")
        exported = client.request("export")["content"]
        for private in (PRIVATE_BODY, PRIVATE_KEY, PRIVATE_COOKIE, str(root), "/Users/fixture/notes.md"):
            require(private not in exported, "Default export leaked original text, paths or credentials")
        require(json.loads(exported)["privacy"] == {"include_original": False, "include_paths": False}, "Default export opt-ins are incorrect")
        originals = client.request("export", {"include_original": True})["content"]
        require(PRIVATE_BODY in originals and "/Users/fixture/notes.md" not in originals and str(root) not in originals,
                "Original-only export revealed paths or omitted supported original text")
        expanded = client.request("export", {"include_original": True, "include_paths": True})["content"]
        require(PRIVATE_BODY in expanded and str(paths["asr"]) in expanded and "/Users/fixture/notes.md" in expanded,
                "Explicit original/path export omitted requested material")
        for content in (originals, expanded):
            require(PRIVATE_KEY not in content and PRIVATE_COOKIE not in content, "Opt-in export leaked credentials")

        preview = client.request("preview", {"categories": ["source_evidence", "asr_diagnostics", "success_checkpoints"], "older_than_days": 30})
        require(preview["candidate_count"] == 4, "Preview did not select the four eligible old artifacts")
        references = [{"id": "queue:future", "status": "waiting", "request": {"task": "local-video", "source": str(source), "api_key": PRIVATE_KEY, "cookies": PRIVATE_COOKIE}}]
        require(client.request("references", {"entries": references})["saved_count"] == 1, "Queue reference was not saved")
        reference_text = (root / "state/cache-maintenance/references.json").read_text()
        require(PRIVATE_KEY not in reference_text and PRIVATE_COOKIE not in reference_text, "Reference registry leaked credentials")
        replaced = json.loads(paths["changed"].read_text())
        replaced["text"] = "externally replaced cache content"
        paths["changed"].write_text(json.dumps(replaced))
        old = time.time() - 90 * 86400
        os.utime(paths["changed"], (old, old))
        result = client.request("clean", {"preview_id": preview["preview_id"]})
        require(result["deleted_count"] == 2 and len(result["skipped"]) == 2, "Cleanup did not recheck queue references and fingerprints")
        selected_bytes = sum(item["size_bytes"] for item in preview["candidates"] if item["path"] in {str(paths["source"]), str(paths["proofread"])})
        require(result["deleted_bytes"] == selected_bytes, "Cleanup byte count differs from deleted preview files")
        require(not paths["source"].exists() and not paths["proofread"].exists(), "Selected eligible files remain")
        require(paths["asr"].exists() and paths["changed"].exists(), "Protected/replaced cache was removed")

        # A distinct process owns the real global lock; reads must continue and
        # cleanup must fail before touching the preview or any fixture material.
        locked_preview = client.request("preview", {"categories": ["disposable_cache"], "older_than_days": 30})
        lock_code = "import sys; sys.path.insert(0, sys.argv[1]); from automation_core import GlobalTaskLock; lock = GlobalTaskLock('local-video', 'fixture', 'lock-holder'); lock.acquire(); print('ready', flush=True); sys.stdin.read(); lock.release()"
        holder = subprocess.Popen([sys.executable, "-B", "-c", lock_code, str(worker)], env=env, cwd=root,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            require(bool(select.select([holder.stdout], [], [], 10)[0]), "External lock holder did not become ready")
            require(holder.stdout.readline().strip() == "ready", "External lock holder failed")
            client.request("inventory")
            client.request("clean", {"preview_id": locked_preview["preview_id"]}, error="TASK_LOCKED")
            require(paths["disposable"].exists(), "Lock conflict deleted fixture data")
        finally:
            try:
                holder.communicate(input="", timeout=10)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.communicate(timeout=10)
        require(holder.returncode == 0, "External lock holder did not release cleanly")

        before_policy = client.request("policy")
        require(client.request("policy", {"auto_enabled": True}, dry_run=True)["dry_run"], "Dry run failed")
        require(client.request("policy") == before_policy, "Dry run persisted a policy change")
        client.request("policy", {"auto_enabled": True, "retention_days": 30, "categories": ["disposable_cache"]})
        automatic = client.request("auto")
        require(automatic["deleted_count"] == 1 and not paths["disposable"].exists(), "Enabled retention policy did not remove its sole eligible old cache")
        require(paths["recent_disposable"].exists() and sha256(paths["recent_disposable"]) == recent_digest,
                "Retention policy removed or changed recent disposable cache")
        require(paths["asr"].exists(), "Automatic cleanup ignored queue protection/category selection")
        require(all(path.exists() and sha256(path) == digest for path, digest in sentinels.items()), "Source, formal note, asset or model changed")
        require(all(path.exists() and sha256(path) == digest for path, digest in recovery_hashes.items()), "Recovery/legacy/journal material changed")
        final_inventory = client.request("inventory")
        require(final_inventory["summary"]["diagnostics_bytes"] == inventory["summary"]["diagnostics_bytes"], "Cleanup modified retained diagnostic audit events")
        return {
            "worker_version": fixture["worker_version"], "worker_stdin_requests": client.calls,
            "categories_verified": sorted(CATEGORIES), "manual_deleted_count": result["deleted_count"],
            "manual_deleted_bytes": result["deleted_bytes"], "protected_or_changed_skipped": len(result["skipped"]),
            "automatic_deleted_count": automatic["deleted_count"], "external_process_lock_verified": True,
            "recent_cache_retention_verified": True,
            "queue_references_verified": True, "export_privacy_verified": True,
            "diagnostic_usage_and_unknowns_verified": True, "recovery_and_audit_retained": True,
            "source_note_assets_models_unchanged": True, "runtime_python": sys.executable,
            "real_model_calls": 0, "runtime_downloads": 0,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--app", type=Path, help="Absolute path to a signed release .app")
    mode.add_argument("--self-test", action="store_true", help="Verify fixtures against temporary copied source resources")
    parser.add_argument("--report", type=Path, help="Save the content-free JSON acceptance report")
    args = parser.parse_args()
    config = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())
    try:
        if args.self_test:
            with tempfile.TemporaryDirectory(prefix="lns-p2-source-") as temporary:
                resource_root = Path(temporary).resolve()
                for relative, source in configured_resources(config).items():
                    target = resource_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                report = {"mode": "source-fixture-self-test", **acceptance(resource_root / "worker", config["version"])}
        else:
            require(args.app.is_absolute(), "--app must be an absolute path")
            if args.report:
                require(args.app.resolve() not in args.report.resolve().parents, "Acceptance report must be outside the signed app")
            before = tree_hashes(args.app)
            worker, report = verify_bundle(args.app, config)
            report.update({"mode": "packaged-p2-acceptance", **acceptance(worker, config["version"])})
            require(tree_hashes(args.app) == before, "Acceptance modified the signed app")
            checked("/usr/bin/codesign", "--verify", "--deep", "--strict", str(args.app))
            report["signed_bundle_unchanged"] = True
        report["passed"] = True
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(content, encoding="utf-8")
        print(content, end="")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
