from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "worker"
sys.path.insert(0, str(WORKER_DIR))

import automation_core as core
import automation_profiles as profiles
import local_notes_agent as agent
import local_notes_mcp as mcp


def load_worker():
    spec = importlib.util.spec_from_file_location("local_note_studio_worker_automation_test", WORKER_DIR / "local_note_studio_worker.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


worker = load_worker()


def profile_mapping(root: pathlib.Path, **overrides):
    data = {
        "id": "fixture",
        "enabled": True,
        "up_mid": "123456",
        "content_types": ["opus", "video"],
        "output_dir": str(root / "notes"),
        "allowed_output_roots": [str(root)],
        "allowed_input_roots": [str(root / "inbox")],
        "allowed_domains": ["bilibili.com", "example.com"],
        "stock_terms": True,
        "overwrite_outputs": False,
        "cooldown_delay": 60,
        "limit": 5,
        "max_limit": 20,
        "subtitle_strategy": "yt-dlp",
        "lock_timeout_seconds": 0,
        "execution_timeout_seconds": 300,
        "runtime_backend": "managed",
    }
    data.update(overrides)
    return data


class ProfileSafetyTests(unittest.TestCase):
    def test_profile_validation_and_explicit_safe_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            profile = profiles.AutomationProfile.from_mapping(profile_mapping(root))
            request = agent.build_request("sync-up", profile, limit=2, caller="mcp")
            self.assertEqual(request["favorite_limit"], 2)
            self.assertEqual(request["cooldown_delay"], 60)
            self.assertEqual(request["caller"], "mcp")
            self.assertNotIn("api_key", request)
            self.assertNotIn("cookies", request)

    def test_opus_image_analysis_profile_modes_validate_and_propagate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            for mode in ("off", "ocr", "vision"):
                with self.subTest(mode=mode):
                    profile = profiles.AutomationProfile.from_mapping(profile_mapping(root, opus_image_analysis=mode))
                    request = agent.build_request("sync-up", profile, caller="mcp")
                    self.assertEqual(profile.opus_image_analysis, mode)
                    self.assertEqual(request["opus_image_analysis"], mode)
                    self.assertEqual(profile.public_dict()["opus_image_analysis"], mode)

            legacy = profiles.AutomationProfile.from_mapping(profile_mapping(root))
            self.assertEqual(legacy.opus_image_analysis, "off")
            with self.assertRaises(core.AutomationError):
                profiles.AutomationProfile.from_mapping(profile_mapping(root, opus_image_analysis="auto"))

    def test_output_and_input_allowlists_reject_escape_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            (allowed / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(core.AutomationError) as context:
                profiles.validate_allowed_path(str(allowed / ".." / "outside"), (allowed,), "source")
            self.assertEqual(context.exception.error_code, "PATH_NOT_ALLOWED")
            with self.assertRaises(core.AutomationError):
                profiles.validate_allowed_path(str(allowed / "escape" / "secret.txt"), (allowed,), "source")

    def test_domain_and_secret_query_are_rejected(self):
        with self.assertRaises(core.AutomationError):
            profiles.validate_allowed_url("https://evil.example/path", ("example.com",))
        with self.assertRaises(core.AutomationError):
            profiles.validate_allowed_url("https://example.com/path?access_token=secret", ("example.com",))

    def test_invalid_profile_prevents_partial_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            payload = {"schema_version": "1.0", "profiles": [profile_mapping(root), profile_mapping(root, id="bad", unknown=True)]}
            path = root / "profiles.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(core.AutomationError):
                profiles.load_profiles(path)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(core.AutomationError):
                profiles.AutomationProfile.from_mapping(profile_mapping(pathlib.Path(temp), limit="5"))


class LockAndHistoryTests(unittest.TestCase):
    def test_cross_process_lock_conflict_and_sigkill_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            code = textwrap.dedent(
                f"""
                import os, sys, time
                sys.path.insert(0, {str(WORKER_DIR)!r})
                os.environ['LOCAL_NOTE_STUDIO_STATE_DIR'] = {temp!r}
                from automation_core import GlobalTaskLock
                lock = GlobalTaskLock('fixture', 'test', 'holder')
                lock.acquire()
                print('READY', flush=True)
                time.sleep(30)
                """
            )
            holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "READY")
                with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temp}):
                    status = core.read_lock_status()
                    self.assertEqual(status["caller"], "test")
                    for caller in ("gui", "cli", "agent", "mcp"):
                        with self.assertRaises(core.LockBusyError):
                            core.GlobalTaskLock("second", caller, f"waiter-{caller}").acquire()
                holder.kill()
                holder.wait(timeout=5)
                holder.stdout.close()
                with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temp}):
                    self.assertIsNone(core.read_lock_status())
                    with core.GlobalTaskLock("recovered", "test", "next"):
                        self.assertIsNotNone(core.read_lock_status())
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.wait(timeout=5)
                if holder.stdout and not holder.stdout.closed:
                    holder.stdout.close()

    def test_history_redacts_secret_values(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temp}):
            store = core.HistoryStore()
            result = core.TaskResult("run", "agent", "fixture", "completed", core.utc_now())
            store.start(
                "run",
                "agent",
                "fixture",
                {"api_key": "top-secret", "cookies": "SESSDATA=cookie-secret", "source": "https://example.com/"},
                result.started_at,
            )
            store.finish(result)
            serialized = json.dumps(store.list(), ensure_ascii=False)
            self.assertNotIn("top-secret", serialized)
            self.assertNotIn("cookie-secret", serialized)
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("cookies", serialized)
            self.assertEqual(store.list()[0]["worker_version"], "0.1.19")

    def test_redaction_covers_provider_error_key_format(self):
        message = "Incorrect API key provided: sk-live-secret123456 url=https://example.com/?signature=signed-value"
        redacted = core.redact_text(message)
        self.assertNotIn("sk-live-secret123456", redacted)
        self.assertNotIn("signed-value", redacted)
        self.assertIn("<redacted>", redacted)

    def test_lock_wait_timeout_can_wait_for_owner_release(self):
        with tempfile.TemporaryDirectory() as temp:
            code = textwrap.dedent(
                f"""
                import os, sys, time
                sys.path.insert(0, {str(WORKER_DIR)!r})
                os.environ['LOCAL_NOTE_STUDIO_STATE_DIR'] = {temp!r}
                from automation_core import GlobalTaskLock
                with GlobalTaskLock('fixture', 'gui', 'short-holder'):
                    print('READY', flush=True)
                    time.sleep(0.2)
                """
            )
            holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "READY")
                with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temp}):
                    with core.GlobalTaskLock("waiting", "agent", "waiter", timeout_seconds=2):
                        self.assertIsNotNone(core.read_lock_status())
                holder.wait(timeout=5)
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.wait(timeout=5)
                if holder.stdout:
                    holder.stdout.close()

    def test_history_recovers_abnormally_terminated_running_row(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temp}):
            store = core.HistoryStore()
            store.start("abrupt", "mcp", "fixture", {"source": "https://example.com/"}, core.utc_now())
            with store._connect() as connection:
                connection.execute("UPDATE runs SET owner_pid=99999999 WHERE run_id='abrupt'")
            recovered = core.HistoryStore().list(run_id="abrupt")
            self.assertEqual(recovered[0]["status"], "interrupted")
            self.assertEqual(recovered[0]["error_code"], "TASK_INTERRUPTED")
            self.assertTrue(recovered[0]["retryable"])

    def test_running_history_exposes_incremental_counts(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": temp}):
            store = core.HistoryStore()
            result = core.TaskResult("progress", "agent", "fixture", "completed", core.utc_now())
            store.start("progress", "agent", "fixture", {}, result.started_at)
            store.mark_running("progress")
            result.counts["discovered"] = 3
            result.counts["created"] = 1
            store.progress(result)
            running = store.list(run_id="progress")[0]
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["counts"]["discovered"], 3)
            self.assertEqual(running["counts"]["created"], 1)


class UpVideoTests(unittest.TestCase):
    def test_video_pagination_and_bvid_deduplication(self):
        req = worker.TaskRequest(task="bilibili-up-video", source="https://space.bilibili.com/123/upload/video")
        pages = [
            {"code": 0, "data": {"page": {"count": 3, "ps": 2}, "list": {"vlist": [{"bvid": "BV111", "title": "one"}, {"bvid": "BV222", "title": "two"}]}}},
            {"code": 0, "data": {"page": {"count": 3, "ps": 2}, "list": {"vlist": [{"bvid": "BV222", "title": "two"}, {"bvid": "BV333", "title": "three"}]}}},
        ]
        with mock.patch.object(worker, "bilibili_json", side_effect=pages) as fetch:
            result = worker.discover_bilibili_up_videos(req, page_size=2)
        self.assertEqual([item["bvid"] for item in result], ["BV111", "BV222", "BV333"])
        self.assertEqual(fetch.call_count, 2)

    def test_failed_video_is_not_completed_and_retry_can_complete_it(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state")}
        ):
            output_dir = pathlib.Path(temp) / "notes"
            output_dir.mkdir()
            req = worker.TaskRequest(
                task="bilibili-up-video",
                source="123",
                output_dir=str(output_dir),
                favorite_limit=1,
                cooldown_delay=0,
            )
            item = {"bvid": "BVFAIL", "title": "fixture", "source_url": "https://www.bilibili.com/video/BVFAIL/", "author_mid": "123", "published": "", "source_hash": "hash"}
            result = core.TaskResult("first", "agent", req.task, "completed", core.utc_now())
            with mock.patch.object(worker, "discover_bilibili_up_videos", return_value=[item]), mock.patch.object(
                worker, "run_command", side_effect=RuntimeError("ASR failed")
            ), mock.patch.object(worker, "existing_video_output", return_value=None):
                with self.assertRaises(core.AutomationError):
                    worker.run_bilibili_up_videos(req, {}, result)
            manifest = worker.load_up_sync_manifest("123")
            self.assertEqual(manifest["items"][0]["status"], "failed")
            self.assertEqual(manifest["items"][0]["error_code"], "ASR_FAILED")

            note = output_dir / "fixture.md"
            note.write_text(
                "---\nsource_url: https://www.bilibili.com/video/BVFAIL/\nsource_type: bilibili-video\n---\n\n## 原始字幕\nfixture\n",
                encoding="utf-8",
            )
            retry = worker.replace(req, retry_failed=True)
            retry_result = core.TaskResult("retry", "agent", retry.task, "completed", core.utc_now())
            with mock.patch.object(worker, "discover_bilibili_up_videos", return_value=[item]), mock.patch.object(
                worker, "run_command", return_value=""
            ), mock.patch.object(worker, "changed_outputs", return_value=[note]), mock.patch.object(
                worker, "validate_task_outputs", return_value=None
            ):
                worker.run_bilibili_up_videos(retry, {}, retry_result)
            manifest = worker.load_up_sync_manifest("123")
            self.assertEqual(manifest["items"][0]["status"], "completed")
            self.assertEqual(manifest["items"][0]["output_path"], str(note))

    def test_retry_includes_stored_failure_missing_from_current_discovery(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state")}
        ):
            output_dir = pathlib.Path(temp) / "notes"
            output_dir.mkdir()
            manifest_path = worker.up_sync_manifest_path("789")
            worker.save_up_sync_manifest(
                manifest_path,
                {
                    "schema_version": "1.0",
                    "up_mid": "789",
                    "items": [
                        {
                            "bvid": "BVHIDDEN",
                            "title": "stored failure",
                            "source_url": "https://www.bilibili.com/video/BVHIDDEN/",
                            "author_mid": "789",
                            "published": "",
                            "source_hash": "stored",
                            "status": "failed",
                            "organized_status": "failed",
                        }
                    ],
                },
            )
            note = output_dir / "recovered.md"
            note.write_text("---\nsource_url: https://www.bilibili.com/video/BVHIDDEN/\n---\n\n## 原始字幕\nok\n", encoding="utf-8")
            req = worker.TaskRequest(
                task="bilibili-up-video",
                source="789",
                output_dir=str(output_dir),
                favorite_limit=0,
                cooldown_delay=0,
                retry_failed=True,
            )
            result = core.TaskResult("retry-hidden", "agent", req.task, "completed", core.utc_now())
            with mock.patch.object(worker, "discover_bilibili_up_videos", return_value=[]), mock.patch.object(
                worker, "run_command", return_value=""
            ) as run_command, mock.patch.object(worker, "changed_outputs", return_value=[note]), mock.patch.object(
                worker, "validate_task_outputs", return_value=None
            ), mock.patch.object(worker, "existing_video_output", return_value=None):
                worker.run_bilibili_up_videos(req, {}, result)
            run_command.assert_called_once()
            self.assertEqual(worker.load_up_sync_manifest("789")["items"][0]["status"], "completed")

    def test_partial_failure_continues_other_items_and_next_normal_run_skips_both_states(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state")}
        ):
            output_dir = pathlib.Path(temp) / "notes"
            output_dir.mkdir()
            req = worker.TaskRequest(task="bilibili-up-video", source="456", output_dir=str(output_dir), favorite_limit=0, cooldown_delay=0)
            items = [
                {"bvid": "BVGOOD", "title": "good", "source_url": "https://www.bilibili.com/video/BVGOOD/", "author_mid": "456", "published": "", "source_hash": "one"},
                {"bvid": "BVBAD", "title": "bad", "source_url": "https://www.bilibili.com/video/BVBAD/", "author_mid": "456", "published": "", "source_hash": "two"},
            ]
            note = output_dir / "good.md"
            note.write_text("---\nsource_url: https://www.bilibili.com/video/BVGOOD/\n---\n\n## 原始字幕\nok\n", encoding="utf-8")
            result = core.TaskResult("partial", "agent", req.task, "completed", core.utc_now())
            calls = iter(["", RuntimeError("download failed")])

            def run_item(*_args, **_kwargs):
                outcome = next(calls)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

            changed = iter([[note], []])
            with mock.patch.object(worker, "discover_bilibili_up_videos", return_value=items), mock.patch.object(
                worker, "run_command", side_effect=run_item
            ), mock.patch.object(worker, "changed_outputs", side_effect=lambda *_args: next(changed)), mock.patch.object(
                worker, "validate_task_outputs", return_value=None
            ), mock.patch.object(worker, "existing_video_output", return_value=None):
                worker.run_bilibili_up_videos(req, {}, result)
            self.assertEqual(result.status, "partial_failed")
            self.assertEqual(result.counts["failed"], 1)
            manifest = worker.load_up_sync_manifest("456")
            self.assertEqual([item["status"] for item in manifest["items"]], ["completed", "failed"])

            next_result = core.TaskResult("incremental", "agent", req.task, "completed", core.utc_now())
            with mock.patch.object(worker, "discover_bilibili_up_videos", return_value=items), mock.patch.object(
                worker, "existing_video_output", side_effect=[note, None]
            ), mock.patch.object(worker, "run_command") as run_command:
                worker.run_bilibili_up_videos(req, {}, next_result)
            run_command.assert_not_called()
            self.assertEqual(next_result.counts["skipped"], 2)


class ContractAndMcpTests(unittest.TestCase):
    def run_worker_request(self, request):
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state")}
            completed = subprocess.run(
                [sys.executable, str(WORKER_DIR / "local_note_studio_worker.py"), "--request-stdin"],
                input=json.dumps(request),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
        line = next(line for line in reversed(completed.stdout.splitlines()) if line.startswith("TASK_RESULT_JSON:"))
        return completed, json.loads(line.split(":", 1)[1])

    def test_success_failure_and_no_changes_use_versioned_contract(self):
        success, success_result = self.run_worker_request(
            {"task": "bilibili-up-sync", "source": "123", "output_dir": "/tmp/fixture", "dry_run": True}
        )
        self.assertEqual(success.returncode, 0)
        self.assertEqual(success_result["schema_version"], "1.0")
        self.assertIsNone(success_result["error"])
        failure, failure_result = self.run_worker_request({"task": "not-supported", "source": "x", "output_dir": "/tmp/fixture"})
        self.assertNotEqual(failure.returncode, 0)
        self.assertEqual(failure_result["status"], "failed")
        self.assertEqual(failure_result["error"]["error_code"], "UNSUPPORTED_REQUEST")

    def test_mcp_stdout_is_json_rpc_only_and_lists_expected_tools(self):
        messages = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "local_notes_list_profiles", "arguments": {}}}),
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.run(
                [sys.executable, str(WORKER_DIR / "local_notes_mcp.py")],
                input=messages + "\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state")},
                check=True,
            )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(responses), 3)
        names = {item["name"] for item in responses[1]["result"]["tools"]}
        self.assertIn("local_notes_sync_up", names)
        self.assertIn("local_notes_get_status", names)
        self.assertNotIn("local_notes_cancel_run", names)

    def test_status_reports_manifest_counts_without_source_details(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {
                "LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state"),
                "INDEX_DIR": str(pathlib.Path(temp) / "state" / "indexes"),
            },
            clear=False,
        ):
            index = pathlib.Path(temp) / "state" / "indexes"
            index.mkdir(parents=True)
            (index / "source-manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "source_url": "https://example.com/private-source",
                                "output_path": "",
                                "organized_status": "organized",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            payload = agent.status_payload(caller="mcp")
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertEqual(payload["caller"], "mcp")
            self.assertEqual(payload["manifest_state"]["index_manifests"][0]["counts"]["completed"], 1)
            self.assertNotIn("private-source", serialized)

    def test_status_prefers_real_organized_output_path_over_deleted_staging_draft(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ,
            {
                "LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state"),
                "INDEX_DIR": str(pathlib.Path(temp) / "state" / "indexes"),
            },
            clear=False,
        ):
            root = pathlib.Path(temp)
            index = root / "state" / "indexes"
            index.mkdir(parents=True)
            organized = root / "formal" / "organized.md"
            organized.parent.mkdir()
            organized.write_text("# 正式笔记\n", encoding="utf-8")
            deleted_staging = root / "staging" / "deleted-draft.md"
            (index / "source-manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "source_url": "https://example.com/private-source",
                                "status": "converted",
                                "output_path": str(deleted_staging),
                                "organized_status": "organized",
                                "organized_output_path": str(organized),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            payload = agent.status_payload(caller="mcp")
            counts = payload["manifest_state"]["index_manifests"][0]["counts"]
            self.assertEqual(counts["completed"], 1)
            self.assertEqual(counts["missing_output"], 0)

    def test_profile_listing_failure_uses_structured_contract(self):
        with mock.patch.object(agent, "load_profiles", side_effect=core.AutomationError("invalid profiles", "PROFILE_INVALID")):
            result = agent.run_agent_action("profiles")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["error_code"], "PROFILE_INVALID")

    def test_mcp_enforces_status_limit_and_env_check_returns_redacted_details(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            mcp.validate_tool_arguments("local_notes_get_status", {"limit": 0})
        with self.assertRaisesRegex(ValueError, "at most 100"):
            mcp.validate_tool_arguments("local_notes_get_status", {"limit": 101})

        req = worker.TaskRequest(task="env-check")
        result = core.TaskResult("env", "mcp", req.task, "completed", core.utc_now())
        with mock.patch.object(worker, "build_env", return_value={}), mock.patch.object(
            worker, "check_environment", return_value="API_KEY=super-secret\n"
        ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            worker.execute_request(req, result)
        self.assertNotIn("super-secret", result.details["environment_report"])
        self.assertNotIn("super-secret", stdout.getvalue())

    def test_opus_image_analysis_summary_enters_safe_result_contract(self):
        result = core.TaskResult("vision", "mcp", "bilibili-opus", "completed", core.utc_now())
        output = "OPUS_IMAGE_ANALYSIS_SUMMARY_JSON:" + json.dumps(
            {
                "mode": "vision",
                "image_count": 2,
                "analyzed": 1,
                "cache_hits": 1,
                "model_calls": 0,
                "failed": 1,
                "limited": 0,
                "finish_reason": "length",
                "completion_tokens": 4096,
                "max_tokens": 4096,
                "timeout_seconds": 300,
                "retry_count": 1,
                "warnings": ["图片 2 分析失败，API_KEY=super-secret"],
                "last_model_call_epoch": 0,
                "cooldown_delay": 60,
            },
            ensure_ascii=False,
        )
        remaining = worker.record_opus_image_analysis_summaries(output, result, 60)
        serialized = json.dumps(result.as_dict(), ensure_ascii=False)
        self.assertEqual(remaining, 0)
        self.assertEqual(result.details["opus_image_analysis"]["cache_hits"], 1)
        self.assertEqual(result.details["opus_image_analysis"]["finish_reason"], "length")
        self.assertEqual(result.details["opus_image_analysis"]["completion_tokens"], 4096)
        self.assertEqual(result.details["opus_image_analysis"]["max_tokens"], 4096)
        self.assertEqual(result.details["opus_image_analysis"]["timeout_seconds"], 300)
        self.assertEqual(result.details["opus_image_analysis"]["retry_count"], 1)
        self.assertIn("<redacted>", serialized)
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("last_model_call_epoch", serialized)

    def test_contract_omits_credentials_and_preserves_numeric_token_diagnostics(self):
        clean = core.sanitize_mapping(
            {
                "api_key": "super-secret",
                "cookies": "SESSDATA=cookie-secret",
                "browser_profile": "/Users/example/Chrome/Profile 1",
                "image_base64": "data:image/png;base64,secret-pixels",
                "completion_tokens": 2056,
                "max_tokens": 4096,
            }
        )
        self.assertEqual(clean, {"completion_tokens": 2056, "max_tokens": 4096})

    def test_agent_worker_command_never_contains_request_or_secret(self):
        contract = {
            "schema_version": "1.0",
            "run_id": "run",
            "caller": "agent",
            "task": "env-check",
            "status": "completed",
            "started_at": core.utc_now(),
            "finished_at": core.utc_now(),
            "source_ref": "",
            "output_dir": "",
            "counts": {"discovered": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0},
            "outputs": [],
            "manifest_path": "",
            "warnings": [],
            "error": None,
            "retryable": False,
        }

        class FakeProcess:
            pid = 999999
            returncode = 0

            def communicate(self, input=None, timeout=None):
                self.input = input
                return "TASK_RESULT_JSON:" + json.dumps(contract) + "\n", ""

            def poll(self):
                return 0

        fake = FakeProcess()
        with mock.patch.object(agent.subprocess, "Popen", return_value=fake) as popen:
            result = agent.invoke_worker({"task": "env-check", "api_key": "super-secret", "execution_timeout_seconds": 1})
        command = popen.call_args.args[0]
        self.assertEqual(command[-1], "--request-stdin")
        self.assertNotIn("super-secret", " ".join(command))
        self.assertEqual(result["status"], "completed")
        rust_bridge = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertIn('.arg("--request-stdin")', rust_bridge)
        self.assertNotIn('.arg("--request-json")', rust_bridge)

    def test_worker_redacts_secret_bearing_child_logs(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            output = worker.run_process(
                [sys.executable, "-c", "print('API_KEY=super-secret SESSDATA=cookie-secret')"],
                os.environ.copy(),
            )
        self.assertNotIn("super-secret", output + stdout.getvalue())
        self.assertNotIn("cookie-secret", output + stdout.getvalue())
        self.assertIn("<redacted>", output)

    def test_no_changes_contract_and_legacy_request_json_remain_compatible(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state")}):
            request = json.dumps({"task": "bilibili-url", "source": "https://www.bilibili.com/video/BVNONE/", "output_dir": str(pathlib.Path(temp) / "notes")})
            with mock.patch.object(worker, "run_command", return_value=""), mock.patch.object(
                worker, "command_for", return_value=["fixture"]
            ), mock.patch.object(worker, "validate_task_outputs", return_value=None), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(worker.main(["--request-json", request]), 0)
            result_line = next(line for line in stdout.getvalue().splitlines() if line.startswith("TASK_RESULT_JSON:"))
            self.assertEqual(json.loads(result_line.split(":", 1)[1])["status"], "no_changes")

    def test_manifest_update_dry_run_never_writes(self):
        req = worker.TaskRequest(task="manifest-update", dry_run=True, manifest_path="/tmp/manifest.json", manifest_action="delete")
        result = core.TaskResult("manifest-preview", "cli", req.task, "completed", core.utc_now())
        with mock.patch.object(worker, "build_env", return_value={}), mock.patch.object(
            worker, "update_manifest_record", side_effect=AssertionError("unexpected Manifest write")
        ):
            worker.execute_request(req, result)
        self.assertIn("manifest_update_preview", result.details)
        self.assertIn("not modified", result.warnings[0])

    def test_cancel_and_timeout_failures_emit_structured_results(self):
        for exception, expected_status, expected_code in (
            (KeyboardInterrupt("cancelled"), "cancelled", "TASK_CANCELLED"),
            (TimeoutError("too slow"), "timeout", "TASK_TIMEOUT"),
        ):
            with self.subTest(expected_status), tempfile.TemporaryDirectory() as temp, mock.patch.dict(
                os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp) / "state")}
            ), mock.patch.object(worker, "execute_request", side_effect=exception), mock.patch(
                "sys.stdout", new_callable=io.StringIO
            ) as stdout:
                request = json.dumps({"task": "bilibili-url", "source": "https://www.bilibili.com/video/BVTEST/", "output_dir": temp})
                self.assertEqual(worker.main(["--request-json", request]), 1)
                result_line = next(line for line in stdout.getvalue().splitlines() if line.startswith("TASK_RESULT_JSON:"))
                result = json.loads(result_line.split(":", 1)[1])
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["error"]["error_code"], expected_code)

    def test_agent_cli_dry_run_uses_enabled_temp_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            profile_file = root / "profiles.json"
            profile_file.write_text(json.dumps({"schema_version": "1.0", "profiles": [profile_mapping(root)]}), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(WORKER_DIR / "local_notes_agent.py"), "sync-up", "--profile", "fixture", "--dry-run"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={
                    **os.environ,
                    "LOCAL_NOTE_STUDIO_PROFILES_FILE": str(profile_file),
                    "LOCAL_NOTE_STUDIO_STATE_DIR": str(root / "state"),
                },
                check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "completed")
            self.assertIn("dry run", " ".join(result["warnings"]))
            self.assertFalse((root / "state" / "automation-history.sqlite3").exists())

    def test_mcp_shutdown_terminates_active_worker_process_group(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        agent._ACTIVE_PROCESS = process
        agent.terminate_active_worker()
        self.assertIsNotNone(process.poll())
        self.assertIsNone(agent._ACTIVE_PROCESS)

        harness_code = textwrap.dedent(
            f"""
            import signal, subprocess, sys
            sys.path.insert(0, {str(WORKER_DIR)!r})
            import local_notes_agent as agent
            import local_notes_mcp as mcp
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
            agent._ACTIVE_PROCESS = child
            signal.signal(signal.SIGTERM, mcp._shutdown)
            print(child.pid, flush=True)
            signal.pause()
            """
        )
        harness = subprocess.Popen([sys.executable, "-c", harness_code], stdout=subprocess.PIPE, text=True)
        child_pid = int(harness.stdout.readline().strip())
        harness.terminate()
        harness.wait(timeout=10)
        harness.stdout.close()
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)

    def test_openhanako_example_fields_match_current_stdio_connector(self):
        example = json.loads((ROOT / "docs" / "openhanako-mcp.example.json").read_text(encoding="utf-8"))
        server = example["mcpServers"]["local-note-studio"]
        self.assertEqual(server["transport"], "stdio")
        self.assertTrue(server["command"].endswith("scripts/local-notes-mcp"))
        self.assertGreaterEqual(server["timeout"], 3600)
        self.assertNotIn("API_KEY", json.dumps(server))


if __name__ == "__main__":
    unittest.main()
