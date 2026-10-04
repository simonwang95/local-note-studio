"""T-118 Worker adapter checks; maintenance must use the existing write lock."""

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "worker"))
import local_note_studio_worker as worker
from automation_core import GlobalTaskLock, HistoryStore, redact_text, sanitize_mapping, stable_source_ref


class CacheWorkerTests(unittest.TestCase):
    def test_cache_mapping_requires_object_options_and_valid_action(self):
        for value in ([], "invalid", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker.TaskRequest.from_mapping({"task": "cache-manage", "cache_options": value})
        with self.assertRaises(ValueError):
            worker.TaskRequest.from_mapping({"task": "cache-manage", "cache_action": "delete-everything"})

    def test_cache_dispatch_bypasses_business_commands(self):
        request = worker.TaskRequest.from_mapping({
            "task": "cache-manage", "cache_action": "preview",
            "cache_options": {"categories": ["disposable_cache"], "older_than_days": 30},
        })
        result = worker.TaskResult("preview-run", "gui", request.task, "completed", worker.utc_now())
        with mock.patch.object(worker, "build_env", return_value={}), \
             mock.patch.object(worker, "handle_cache_request", return_value={"preview_id": "opaque", "total_bytes": 42}) as handler, \
             mock.patch.object(worker, "command_for") as business:
            worker.execute_request(request, result)
        handler.assert_called_once_with("preview", request.cache_options, {})
        business.assert_not_called()
        self.assertEqual(result.as_dict()["details"]["cache"]["preview_id"], "opaque")

    def test_readonly_cache_inventory_never_uses_processing_lock(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temporary}), \
             mock.patch.object(worker, "handle_cache_request", return_value={"items": []}), \
             mock.patch.object(worker, "build_env", return_value={}), \
             mock.patch.object(worker, "GlobalTaskLock") as lock, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.main(["--task", "cache-manage", "--cache-action", "inventory"]), 0)
            lock.assert_not_called()
            self.assertFalse((pathlib.Path(temporary) / "automation-history.sqlite3").exists())

    def test_clean_is_rejected_when_processing_lock_is_held(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temporary}), \
             mock.patch.object(worker, "handle_cache_request") as handler, \
             mock.patch.object(worker, "build_env", return_value={}):
            held = GlobalTaskLock("local-video", "fixture", "running-video")
            held.acquire()
            try:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = worker.main(["--task", "cache-manage", "--cache-action", "clean", "--cache-options", '{"preview_id":"opaque"}'])
                self.assertEqual(exit_code, 1)
                self.assertIn("TASK_LOCKED", output.getvalue())
                handler.assert_not_called()
            finally:
                held.release()

    def test_cache_dry_run_never_writes_policy_or_references(self):
        for action in ("policy", "references", "clean", "auto"):
            with self.subTest(action=action), \
                 mock.patch.object(worker, "build_env", return_value={}), \
                 mock.patch.object(worker, "handle_cache_request") as handler:
                request = worker.TaskRequest(task="cache-manage", cache_action=action, cache_options={"auto_enabled": True}, dry_run=True)
                result = worker.TaskResult("dry", "gui", request.task, "completed", worker.utc_now())
                worker.execute_request(request, result)
                handler.assert_not_called()

    def test_nonsecret_usage_counts_survive_result_sanitization(self):
        clean = sanitize_mapping({"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19,
                                  "api_key": "secret", "access_token": "secret", "total_tokens_text": "secret"})
        self.assertEqual(clean, {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19})
        self.assertEqual(sanitize_mapping({"total_tokens": None}), {"total_tokens": None})

    def test_cookie_headers_and_netscape_values_are_always_redacted(self):
        for text in ("Cookie: session=private-cookie-value; theme=dark", "Set-Cookie: session=private-cookie-value; HttpOnly",
                     "bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tprivate-cookie-value"):
            self.assertNotIn("private-cookie-value", redact_text(text))

    def test_history_preserves_source_identity_when_filename_is_redacted(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temporary}):
            store = HistoryStore()
            source = str(pathlib.Path(temporary) / "sk-abcdefghijklmnop.mp4")
            store.start("waiting-fixture", "cli", "local-video", {"task": "local-video", "source": source}, worker.utc_now())
            with store._connect() as connection:
                saved = json.loads(connection.execute("SELECT request_json FROM runs WHERE run_id=?", ("waiting-fixture",)).fetchone()[0])
            self.assertNotIn("sk-abcdefghijklmnop", saved["source"])
            self.assertEqual(saved["source_ref"], stable_source_ref(source))

    def test_processing_failure_diagnostics_are_not_marked_completed(self):
        request = worker.TaskRequest(task="local-video", run_id="failed-run")
        result = worker.TaskResult(request.run_id, "gui", request.task, "completed", worker.utc_now())
        env = {"LOCAL_NOTE_STUDIO_RUN_ID": request.run_id}
        with mock.patch.object(worker, "build_env", return_value=env), \
             mock.patch.object(worker, "_execute_request", side_effect=RuntimeError("ASR 转录失败")), \
             mock.patch.object(worker, "finalize_run", return_value={"status": "failed"}) as finalize:
            with self.assertRaises(RuntimeError):
                worker.execute_request(request, result)
        finalize.assert_called_once_with("failed", env=env)
        self.assertEqual(result.details["diagnostics"]["status"], "failed")

    def test_diagnostic_storage_failure_does_not_replace_business_error(self):
        request = worker.TaskRequest(task="local-video")
        result = worker.TaskResult("failed-run", "gui", request.task, "completed", worker.utc_now())
        with mock.patch.object(worker, "build_env", return_value={}), \
             mock.patch.object(worker, "_execute_request", side_effect=RuntimeError("business failure")), \
             mock.patch.object(worker, "finalize_run", side_effect=OSError("storage failure")):
            with self.assertRaisesRegex(RuntimeError, "business failure"):
                worker.execute_request(request, result)
        self.assertIn("task diagnostics could not be persisted", result.warnings)

    def test_incognito_processing_never_runs_automatic_cache_maintenance(self):
        request = worker.TaskRequest(task="local-video", incognito_mode=True)
        result = worker.TaskResult("incognito", "gui", request.task, "completed", worker.utc_now())
        with mock.patch.object(worker, "build_env", return_value={"LOCAL_NOTE_STUDIO_LOCK_FD": "fixture"}), \
             mock.patch.object(worker, "_execute_request"), \
             mock.patch.object(worker, "finalize_run", return_value=None), \
             mock.patch.object(worker, "handle_cache_request") as cache_handler:
            worker.execute_request(request, result)
        cache_handler.assert_not_called()

    def test_cache_cli_matches_json_contract(self):
        options = {"categories": ["asr_diagnostics"], "older_than_days": 7}
        request = worker.request_from_args(worker.parse_args([
            "--task", "cache-manage", "--cache-action", "preview", "--cache-options", json.dumps(options),
        ]))
        self.assertEqual(request.cache_action, "preview")
        self.assertEqual(request.cache_options, options)


if __name__ == "__main__":
    unittest.main()
