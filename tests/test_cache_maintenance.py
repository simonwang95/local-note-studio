from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

import cache_maintenance as cache


class CacheMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {"LOCAL_NOTE_STUDIO_APP_DATA_DIR": str(self.root),
                    "LOCAL_NOTE_STUDIO_STATE_DIR": str(self.state)}

    def call(self, action, **payload):
        return cache.handle_cache_request(action, payload, self.env)

    def artifact(self, kind="source", name="a", source="BV1cache", status="completed", **extra):
        path = self.state / "transcripts" / kind / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "transcript": "PRIVATE ORIGINAL",
                   "cache_metadata": {"schema_version": 1, "task": "bilibili-transcript",
                                      "source_ref": source, "status": status,
                                      "run_id": name, "created_at": "2025-01-01T00:00:00Z"}, **extra}
        path.write_text(json.dumps(payload), encoding="utf-8")
        old = time.time() - 90 * 86400
        os.utime(path, (old, old))
        return path

    def preview(self, category="source_evidence"):
        return self.call("preview", categories=[category], older_than_days=30)

    def test_categories_preview_and_exact_cleanup_preserve_notes_and_source(self):
        source = self.root / "source.mp4"
        note = self.root / "note.md"
        source.write_bytes(b"source")
        note.write_text("formal note")
        selected = self.artifact()
        other = self.artifact("asr", "b")
        listing = self.call("inventory")
        self.assertEqual({row["category"] for row in listing["items"]},
                         {"source_evidence", "asr_diagnostics"})
        preview = self.preview()
        self.assertEqual(preview["candidate_count"], 1)
        self.assertEqual(preview["total_bytes"], selected.stat().st_size)
        result = self.call("clean", preview_id=preview["preview_id"])
        self.assertEqual(result["deleted_bytes"], preview["total_bytes"])
        self.assertFalse(selected.exists())
        self.assertTrue(other.exists())
        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(note.read_text(), "formal note")

    def test_new_queue_reference_after_preview_prevents_delete(self):
        path = self.artifact()
        preview = self.preview()
        self.call("references", entries=[{"id": "queued", "status": "waiting",
                 "request": {"task": "bilibili-transcript", "source": "https://www.bilibili.com/video/BV1cache",
                             "api_key": "sk-SECRETKEY", "cookies": "SESSDATA=secret"}}])
        result = self.call("clean", preview_id=preview["preview_id"])
        self.assertTrue(path.exists())
        self.assertEqual(len(result["skipped"]), 1)
        stored = (self.state / "cache-maintenance" / "references.json").read_text()
        self.assertNotIn("SECRETKEY", stored)
        self.assertNotIn("SESSDATA", stored)

    def test_changed_fingerprint_cannot_delete_replaced_cache(self):
        path = self.artifact()
        preview = self.preview()
        path.write_text('{"changed":true}')
        result = self.call("clean", preview_id=preview["preview_id"])
        self.assertTrue(path.exists())
        self.assertEqual(result["deleted_bytes"], 0)

    def test_legacy_unknown_and_failed_recovery_are_protected(self):
        self.artifact("proofread-checkpoints", "failed", status="failed")
        legacy = self.state / "transcripts" / "source" / "legacy.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('{"transcript":"legacy"}')
        recovery = self.state / "recovery" / "recovery-id" / "draft.md"
        recovery.parent.mkdir(parents=True)
        recovery.write_text("recovery material")
        result = self.call("inventory")
        self.assertTrue(all(row["protected"] for row in result["items"]))
        self.assertEqual(self.call("preview", categories=list(cache.CATEGORIES), older_than_days=0)["candidate_count"], 0)

    def test_symlink_root_and_leaf_never_follow_or_delete_user_files(self):
        source = self.root / "owned.json"
        source.write_text("user file")
        path = self.artifact()
        path.unlink()
        path.symlink_to(source)
        inventory = self.call("inventory")
        self.assertTrue(inventory["items"][0]["protected"])
        self.assertEqual(self.preview()["candidate_count"], 0)
        self.assertTrue(source.exists())
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "b.json").write_text("owned outside")
        (self.state / "opus-image-analysis-cache").symlink_to(outside, target_is_directory=True)
        self.assertFalse(any(row["path"].endswith("b.json") for row in self.call("inventory")["items"]))

    def test_preview_parent_replaced_by_symlink_is_safe(self):
        path = self.artifact()
        preview = self.preview()
        destination = self.root / "outside"
        path.parent.rename(destination)
        path.parent.symlink_to(destination, target_is_directory=True)
        result = self.call("clean", preview_id=preview["preview_id"])
        self.assertEqual(result["deleted_bytes"], 0)
        self.assertTrue((destination / path.name).exists())

    def test_sqlite_failed_run_protects_source_and_diagnostic_is_unknown(self):
        path = self.artifact()
        with sqlite3.connect(self.state / "automation-history.sqlite3") as db:
            db.execute("CREATE TABLE runs (run_id TEXT,status TEXT,task TEXT,request_json TEXT,result_json TEXT,started_at TEXT,finished_at TEXT)")
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", ("a", "failed", "bilibili-transcript",
                       json.dumps({"source": "https://www.bilibili.com/video/BV1cache"}), "{}", "2025-01-01", None))
        inventory = self.call("inventory")
        self.assertTrue(inventory["items"][0]["protected"])
        self.assertEqual(inventory["diagnostics"][0]["model_calls"], None)
        self.assertEqual(self.preview()["candidate_count"], 0)
        self.assertTrue(path.exists())

    def test_transaction_recovery_staging_is_locatable_and_protected(self):
        output = self.root / "notes"
        staging = output / ".local-note-studio-staging" / "run-id"
        staging.mkdir(parents=True)
        (staging / "draft.md").write_text("recoverable body")
        journal = output / ".local-note-studio-transactions"
        journal.mkdir()
        (journal / "run-id.json").write_text(json.dumps({"state": "rolled_back", "run_id": "run-id", "stage_dir": str(staging)}))
        self.call("references", entries=[{"id": "run-id", "status": "failed", "request": {"task": "local-video", "output_dir": str(output)}}])
        entry = self.call("inventory")["items"][0]
        self.assertEqual(entry["category"], "failed_recovery")
        self.assertTrue(entry["protected"])
        self.assertEqual(entry["path"], str(staging / "draft.md"))

    def test_default_export_omits_original_paths_and_secrets_even_from_nested_config(self):
        path = self.artifact(api_key="sk-SECRETKEY", cookie="SESSDATA=SECRET", arbitrary="user prose /secret/path")
        diag = self.state / "diagnostics" / "a.json"
        diag.parent.mkdir()
        diag.write_text(json.dumps({"run_id": "a", "task": "local-video", "source_ref": "/secret/path",
                   "effective_config": {"api_key": "sk-SECRETKEY", "model": "safe-model", "arbitrary": "private prose"},
                   "metrics": {"model_calls": 2, "total_tokens": 40, "cache_hits": 1,
                               "retries": [{"stage": "proofread", "reason": "quality", "count": 1}]}}))
        content = self.call("export")["content"]
        for private in ("PRIVATE ORIGINAL", "SECRETKEY", "SESSDATA", "private prose", "/secret/path", str(path)):
            self.assertNotIn(private, content)
        exported = json.loads(content)
        self.assertEqual(exported["diagnostics"][0]["total_tokens"], 40)
        expanded = self.call("export", include_original=True, include_paths=True)["content"]
        self.assertIn("PRIVATE ORIGINAL", expanded)
        self.assertIn(str(path), expanded)
        self.assertNotIn("SECRETKEY", expanded)

    def test_auto_disabled_and_validated_policy(self):
        path = self.artifact()
        self.assertFalse(self.call("policy")["auto_enabled"])
        self.call("auto")
        self.assertTrue(path.exists())
        self.call("policy", auto_enabled=True, retention_days=30, categories=["source_evidence"])
        self.call("auto")
        self.assertFalse(path.exists())
        with self.assertRaises(cache.CacheMaintenanceError):
            self.call("policy", retention_days=-1)

    def test_corrupt_references_fail_closed(self):
        self.artifact()
        refs = self.state / "cache-maintenance" / "references.json"
        refs.parent.mkdir()
        refs.write_text("broken")
        self.assertEqual(self.preview()["candidate_count"], 0)

    def test_preview_cannot_accept_paths_or_change_selection_afterwards(self):
        self.artifact()
        with self.assertRaises(cache.CacheMaintenanceError):
            self.call("preview", categories=["unknown"], older_than_days=0)
        preview = self.preview()
        with self.assertRaises(cache.CacheMaintenanceError):
            self.call("clean", preview_id=preview["preview_id"], categories=["asr_diagnostics"])

    def test_previous_run_metadata_and_prefixed_gui_id_protect_shared_cache(self):
        self.artifact(cache_metadata={"schema_version": 1, "status": "completed", "run_id": "new-run",
                     "references": [{"type": "run", "id": "old-run"}]})
        self.call("references", entries=[{"id": "history:desktop-id", "status": "failed", "request": {"task": "local-video", "run_id": "old-run"}}])
        inventory = self.call("inventory")
        self.assertTrue(inventory["items"][0]["protected"])
        self.assertEqual(inventory["items"][0]["references"][0]["run_id"], "old-run")

    def test_retained_source_identity_protects_source_after_new_source_ref(self):
        old_source = "BV1oldsource"
        self.artifact(cache_metadata={"schema_version": 1, "status": "completed", "source_ref": "BV1newsource",
                     "references": [{"type": "source", "id": cache.hashlib.sha256(old_source.encode()).hexdigest()}]})
        self.call("references", entries=[{"id": "queued", "status": "waiting", "request": {"task": "bilibili-transcript", "source": "https://www.bilibili.com/video/" + old_source}}])
        self.assertTrue(self.call("inventory")["items"][0]["protected"])

    def test_hardlinks_and_corrupt_sqlite_are_protected(self):
        path = self.artifact()
        linked = self.root / "user-owned.json"
        os.link(path, linked)
        self.assertEqual(self.preview()["candidate_count"], 0)
        linked.unlink()
        (self.state / "automation-history.sqlite3").write_text("bad sqlite")
        self.assertEqual(self.preview()["candidate_count"], 0)

    def test_expired_preview_is_rejected_and_reference_update_is_atomic(self):
        path = self.artifact()
        preview = self.preview()
        saved = self.state / "cache-maintenance" / "previews" / (preview["preview_id"] + ".json")
        snapshot = json.loads(saved.read_text())
        snapshot["expires_timestamp"] = 1
        saved.write_text(json.dumps(snapshot))
        with self.assertRaises(cache.CacheMaintenanceError) as expired:
            self.call("clean", preview_id=preview["preview_id"])
        self.assertEqual(expired.exception.error_code, "CACHE_PREVIEW_EXPIRED")
        self.assertTrue(path.exists())
        self.call("references", entries=[{"id": "q", "status": "waiting", "request": {"task": "local-video"}}])
        refs = self.state / "cache-maintenance" / "references.json"
        before = refs.read_bytes()
        with self.assertRaises(cache.CacheMaintenanceError):
            self.call("references", entries=[{"id": "q", "status": {}, "request": {}}])
        self.assertEqual(refs.read_bytes(), before)

    def test_partial_failure_and_timeout_references_protect_material(self):
        self.artifact()
        for status_value in ("partial_failed", "timeout"):
            self.call("references", entries=[{"id": "r", "status": status_value, "request": {"task": "local-video", "run_id": "a"}}])
            self.assertTrue(self.call("inventory")["items"][0]["protected"])

    def test_original_export_keeps_cookie_secret_and_path_options_independent(self):
        self.artifact(transcript="body /Users/private/Documents/note.md\nCookie: session=private-cookie-value; theme=dark\nbilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tnetscape-cookie-value")
        for paths in (False, True):
            exported = self.call("export", include_original=True, include_paths=paths)["content"]
            self.assertNotIn("private-cookie-value", exported)
            self.assertNotIn("netscape-cookie-value", exported)
            self.assertEqual("/Users/private/Documents/note.md" in exported, paths)

    def test_credential_shaped_source_filename_keeps_stable_reference_after_redaction(self):
        source = str(self.root / "sk-abcdefghijklmnop.mp4")
        self.artifact(source=source)
        self.call("references", entries=[{"id": "waiting", "status": "waiting", "request": {"task": "local-video", "source": source, "output_dir": str(self.root / "future-notes")}}])
        inventory = self.call("inventory")
        self.assertTrue(inventory["items"][0]["protected"])
        self.assertEqual(self.preview()["candidate_count"], 0)
        stored = (self.state / "cache-maintenance" / "references.json").read_text()
        self.assertNotIn("sk-abcdefghijklmnop", stored)
        self.assertIn(cache.stable_source_ref(source), stored)

    def test_redacted_sqlite_source_uses_result_stable_source_ref(self):
        source = str(self.root / "sk-abcdefghijklmnop.mp4")
        self.artifact(source=source)
        with sqlite3.connect(self.state / "automation-history.sqlite3") as db:
            db.execute("CREATE TABLE runs (run_id TEXT,status TEXT,task TEXT,request_json TEXT,result_json TEXT,started_at TEXT)")
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", ("different-run", "failed", "local-video", json.dumps({"source": cache.redact_text(source)}),
                       json.dumps({"source_ref": cache.stable_source_ref(source)}), "2025-01-01"))
        self.assertTrue(self.call("inventory")["items"][0]["protected"])

    def test_directory_queue_protects_child_source_with_new_output_root(self):
        source_root = self.root / "input"
        source_root.mkdir()
        source = source_root / "child.mp4"
        self.artifact(source=str(source))
        self.call("references", entries=[{"id": "waiting", "status": "waiting", "request": {"task": "local-video", "source": str(source_root), "output_dir": str(self.root / "new-notes")}}])
        self.assertTrue(self.call("inventory")["items"][0]["protected"])
        self.assertEqual(self.preview()["candidate_count"], 0)

    def test_unresolved_queued_collection_preserves_video_evidence(self):
        self.artifact()
        self.call("references", entries=[{"id": "waiting", "status": "waiting", "request": {"task": "bilibili-favorite", "source": "", "collection_id": "1234", "output_dir": str(self.root / "new-notes")}}])
        self.assertTrue(self.call("inventory")["items"][0]["protected"])
        self.assertEqual(self.preview()["candidate_count"], 0)

    def test_diagnostic_audit_size_includes_summaries_events_and_locks_but_is_retained(self):
        diagnostics = self.state / "diagnostics"
        events = diagnostics / "events" / "done-run"
        events.mkdir(parents=True)
        summary = diagnostics / "done-run.json"
        summary.write_text(json.dumps({"run_id": "done-run", "status": "completed", "metrics": {}}))
        event = events / "event.json"
        event.write_text(json.dumps({"type": "run_finish", "status": "completed"}))
        (diagnostics / ".done-run.lock").write_bytes(b"")
        inventory = self.call("inventory")
        self.assertEqual(inventory["summary"]["diagnostics_bytes"], summary.stat().st_size + event.stat().st_size)
        self.assertEqual(inventory["summary"]["diagnostics_count"], 3)
        preview = self.call("preview", categories=list(cache.CATEGORIES), older_than_days=0)
        self.assertEqual(preview["candidate_count"], 0)
        self.call("clean", preview_id=preview["preview_id"])
        self.assertTrue(summary.exists())
        self.assertTrue(event.exists())

    def test_history_interrupted_state_wins_over_stale_running_telemetry(self):
        self.artifact("proofread-checkpoints")
        diagnostics = self.state / "diagnostics"
        diagnostics.mkdir()
        (diagnostics / "a.json").write_text(json.dumps({"run_id": "a", "status": "running", "metrics": {}}))
        with sqlite3.connect(self.state / "automation-history.sqlite3") as db:
            db.execute("CREATE TABLE runs (run_id TEXT,status TEXT,task TEXT,request_json TEXT,result_json TEXT,started_at TEXT)")
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", ("a", "interrupted", "local-video", "{}", "{}", "2025-01-01"))
        inventory = self.call("inventory")
        self.assertEqual(inventory["items"][0]["status"], "interrupted")
        self.assertEqual(inventory["items"][0]["category"], "failed_recovery")
        self.assertTrue(inventory["items"][0]["protected"])
        self.assertEqual(inventory["diagnostics"][0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
