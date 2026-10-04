from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker/scripts"))
import task_diagnostics as diagnostics
from transcript_quality import load_transcript_diagnostic, save_transcript_diagnostic


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


batch = load("diagnostics_batch", ROOT / "worker/scripts/bilibili/batch_transcribe.py")
asr = load("diagnostics_asr", ROOT / "worker/scripts/bilibili/whisper_transcribe.py")
qwen_asr = load("diagnostics_qwen_asr", ROOT / "worker/scripts/bilibili/qwen3_transcribe.py")
organizer = load("diagnostics_organizer", ROOT / "worker/scripts/qwen_organize_notes.py")
quickread = load("diagnostics_quickread", ROOT / "worker/scripts/quick_read_pdf.py")


class Audio:
    def __init__(self, seconds):
        self.samples = int(seconds * 16000)

    def __len__(self):
        return self.samples

    def __getitem__(self, interval):
        return Audio((interval.stop - interval.start) / 16000)


def response(content, usage=None, finish_reason="stop"):
    data = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    if usage is not None:
        data["usage"] = usage
    result = mock.Mock(status_code=200)
    result.json.return_value = data
    return result


class TaskDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {
            "LOCAL_NOTE_STUDIO_STATE_DIR": str(self.root / "state"),
            "TRANSCRIPT_CACHE_DIR": str(self.root / "transcripts"),
            "LOCAL_NOTE_STUDIO_RUN_ID": "fixture-run",
            "LOCAL_NOTE_STUDIO_TASK": "local-video",
            "LOCAL_NOTE_STUDIO_SOURCE_REF": "file:fixture-source",
            "LOCAL_NOTE_STUDIO_OUTPUT_DIR": str(self.root / "notes"),
            "LOCAL_NOTE_STUDIO_INCOGNITO": "false",
        }
        self.patch_env = mock.patch.dict(os.environ, self.env)
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)

    def test_cache_metadata_preserves_payload_and_accumulates_run_references(self):
        original = {"schema_version": 2, "metadata": {"model": "fixture"},
                    "segments": [{"start": 0, "end": 3, "result": "正文。"}]}
        path = Path(save_transcript_diagnostic("proofread-checkpoints", "identity", original))
        saved = load_transcript_diagnostic("proofread-checkpoints", "identity")
        self.assertEqual(saved["metadata"], original["metadata"])
        self.assertEqual(saved["segments"], original["segments"])
        self.assertNotIn("cache_metadata", original)
        meta = saved["cache_metadata"]
        self.assertEqual(meta["category"], "success_checkpoints")
        self.assertEqual(meta["run_id"], "fixture-run")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_RUN_ID": "retry-run"}):
            save_transcript_diagnostic("proofread-checkpoints", "identity", original)
        retry = json.loads(path.read_text())["cache_metadata"]
        self.assertEqual(retry["created_at"], meta["created_at"])
        self.assertIn({"type": "run", "id": "fixture-run"}, retry["references"])
        self.assertIn({"type": "run", "id": "retry-run"}, retry["references"])
        # Old cache payloads without the envelope still load unchanged.
        path.write_text(json.dumps(original))
        self.assertEqual(load_transcript_diagnostic("proofread-checkpoints", "identity"), original)

    def test_worker_explicit_env_does_not_mutate_process_and_unknown_stays_null(self):
        before = dict(os.environ)
        explicit = {**self.env, "LOCAL_NOTE_STUDIO_RUN_ID": "explicit-run"}
        diagnostics.start_run(env=explicit, effective_config={"model": "fixture", "api_key": "secret", "max_tokens": 50})
        finished = diagnostics.finalize_run("completed", env=explicit)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(finished["run_id"], "explicit-run")
        self.assertIsNone(finished["metrics"]["model_calls"])
        self.assertIsNone(finished["metrics"]["prompt_tokens"])
        self.assertIsNone(finished["metrics"]["stages"])
        self.assertIsNone(finished["metrics"]["retries"])
        self.assertGreaterEqual(finished["metrics"]["duration_seconds"], 0)
        self.assertEqual(finished["effective_config"], {"model": "fixture", "max_tokens": 50})

    def test_cache_source_uses_per_note_source_and_staging_maps_to_final(self):
        stage, final = self.root / "stage", self.root / "notes"
        stage.mkdir()
        note = stage / "note.md"
        note.write_text("# 视频\n\n> **链接**：https://www.bilibili.com/video/BVfixture?api_key=secret\n\nsecret body")
        with mock.patch.dict(os.environ, {"VIDEO_TRANSACTION_STAGE_DIR": str(stage), "VIDEO_TRANSACTION_FINAL_DIR": str(final)}):
            path = Path(save_transcript_diagnostic("source", "source", {"note_path": str(note), "transcript": "raw evidence"}))
        meta = json.loads(path.read_text())["cache_metadata"]
        self.assertEqual(meta["source_ref"], "https://www.bilibili.com/video/BVfixture")
        self.assertEqual(meta["note_path"], str(final / "note.md"))
        self.assertNotIn("secret body", json.dumps(meta))
        note.write_text("---\nsource_path: /fixtures/item.mp4\n---\ncontent")
        path = Path(save_transcript_diagnostic("source", "local", {"note_path": str(note)}))
        self.assertEqual(json.loads(path.read_text())["cache_metadata"]["source_ref"], "/fixtures/item.mp4")

    def test_interrupted_stage_keeps_unknown_duration_and_failed_status(self):
        diagnostics.start_run()
        diagnostics.emit_event("stage_start", stage="proofread")
        with diagnostics.stage_span("proofread"):
            pass
        metrics = diagnostics.finalize_run("cancelled")["metrics"]
        self.assertEqual(metrics["stages"][0]["status"], "interrupted")
        self.assertIsNone(metrics["stages"][0]["duration_seconds"])

    def test_transport_attempts_count_and_missing_tokens_are_unknown(self):
        import requests
        diagnostics.start_run()
        usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        with mock.patch.object(batch, "SUMMARY_API_KEY", "secret-fixture"), \
                mock.patch.object(batch.requests, "post", side_effect=[requests.Timeout("secret must not persist"), response("完成", usage)]) as post, \
                mock.patch.object(batch, "LLM_RETRY_DELAY", 0):
            self.assertEqual(batch._call_llm("secret prompt", "secret source text", max_retries=1).content, "完成")
        self.assertEqual(post.call_count, 2)
        summary = diagnostics.finalize_run("completed")
        self.assertEqual(summary["metrics"]["model_calls"], 2)
        self.assertIsNone(summary["metrics"]["prompt_tokens"])
        self.assertFalse(summary["metrics"]["usage_complete"])
        self.assertEqual(summary["metrics"]["retries"], [{"stage": "summary", "reason": "timeout", "count": 1}])
        persisted = "".join(path.read_text() for path in (self.root / "state/diagnostics").rglob("*.json"))
        for secret in ("secret-fixture", "secret prompt", "secret source text", "secret must not persist"):
            self.assertNotIn(secret, persisted)

    def test_empty_output_retry_keeps_real_usage_from_both_requests(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                 "completion_tokens_details": {"reasoning_tokens": 2}}
        diagnostics.start_run()
        with mock.patch.object(batch, "SUMMARY_API_KEY", "fixture"), \
                mock.patch.object(batch.requests, "post", side_effect=[response("", usage, "length"), response("完成", usage)]), \
                mock.patch.object(batch, "LLM_RETRY_DELAY", 0):
            batch._call_llm("fixture", "fixture", max_tokens=10, max_retries=1)
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["model_calls"], 2)
        self.assertEqual(metrics["prompt_tokens"], 20)
        self.assertEqual(metrics["completion_tokens"], 8)
        self.assertEqual(metrics["total_tokens"], 28)
        self.assertEqual(metrics["reasoning_tokens"], 4)
        self.assertTrue(metrics["usage_complete"])
        self.assertEqual(metrics["retries"][0]["reason"], "output_budget")

    def test_invalid_response_still_records_usage_returned_by_provider(self):
        usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
        invalid = mock.Mock(status_code=200)
        invalid.json.return_value = {"usage": usage}
        diagnostics.start_run()
        with mock.patch.object(batch, "SUMMARY_API_KEY", "fixture"), \
                mock.patch.object(batch.requests, "post", side_effect=[invalid, response("完成", usage)]), \
                mock.patch.object(batch, "LLM_RETRY_DELAY", 0):
            batch._call_llm("fixture", "fixture", max_retries=1)
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["total_tokens"], 16)
        self.assertEqual(metrics["retries"][0]["reason"], "invalid_response")

    def test_checkpoint_reuse_counts_only_validated_hit_without_model_call(self):
        text = "产业研究需要关注订单。"
        content = f"[[LNS_SECTION:proofread]]{text}[[/LNS_SECTION:proofread]]"
        diagnostics.start_run()
        with mock.patch.object(batch, "SUMMARY_API_KEY", "fixture"), \
                mock.patch.object(batch.requests, "post", return_value=response(content)) as post, \
                mock.patch.object(batch, "SUMMARY_PROOFREAD_COOLDOWN_DELAY", 0):
            batch._run_chunked_proofread("fixture", "title", text, identity_path=str(self.root / "note.md"))
            with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_RUN_ID": "cached-run"}):
                diagnostics.start_run()
                self.assertEqual(batch._run_chunked_proofread("fixture", "title", text, identity_path=str(self.root / "note.md")), text)
                metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(post.call_count, 1)
        self.assertEqual(metrics["model_calls"], 0)
        self.assertEqual(metrics["cache_hits"], 1)
        self.assertIsNone(metrics["total_tokens"])
        self.assertEqual(metrics["stages"][0]["status"], "completed")
        self.assertGreaterEqual(metrics["stages"][0]["duration_seconds"], 0)

    def test_quality_retry_is_distinct_from_transport_retry(self):
        text = "产业研究需要关注订单。"
        good = f"[[LNS_SECTION:proofread]]{text}[[/LNS_SECTION:proofread]]"
        bad = "[[LNS_SECTION:proofread]]。[[/LNS_SECTION:proofread]]"
        diagnostics.start_run()
        with mock.patch.object(batch, "SUMMARY_API_KEY", "fixture"), \
                mock.patch.object(batch.requests, "post", side_effect=[response(bad), response(good)]), \
                mock.patch.object(batch, "SUMMARY_PROOFREAD_COOLDOWN_DELAY", 0), \
                mock.patch.object(batch, "SUMMARY_PROOFREAD_MAX_RETRIES", 1):
            self.assertEqual(batch._run_chunked_proofread("fixture", "title", text), text)
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["model_calls"], 2)
        self.assertEqual(metrics["retries"], [{"stage": "proofread", "reason": "quality", "count": 1}])

    def test_local_asr_repair_counts_actual_calls_without_invented_usage(self):
        initial = {"text": "。", "segments": [{"start": 0, "end": 60, "text": "。"}]}
        fixed = {"text": "恢复内容。", "segments": [{"start": 0, "end": 60, "text": "恢复内容。"}]}
        diagnostics.start_run()
        raw, result, _repairs, errors = asr.transcribe_with_repair(Audio(60), mock.Mock(side_effect=[initial, fixed]), {}, lambda a, b: True)
        self.assertEqual(raw, initial)
        self.assertEqual(result["text"], "恢复内容。")
        self.assertEqual(errors, [])
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["asr_calls"], 2)
        self.assertEqual(metrics["llm_calls"], 0)
        self.assertIsNone(metrics["prompt_tokens"])
        self.assertEqual(metrics["retries"], [{"stage": "asr", "reason": "quality", "count": 1}])

    def test_qwen_asr_records_call_and_original_evidence_without_usage(self):
        audio_path = self.root / "audio.wav"
        audio_path.write_bytes(b"fixture audio")
        model = mock.Mock()
        model.transcribe.return_value = [SimpleNamespace(text="Qwen原文。")]
        diagnostics.start_run()
        with mock.patch.dict(os.environ, {"ASR_SOURCE_REF": "/fixtures/original.mp4"}):
            self.assertEqual(qwen_asr.transcribe_with_diagnostics(model, str(audio_path), "/model/Qwen3", "cpu"), "Qwen原文。")
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["asr_calls"], 1)
        self.assertIsNone(metrics["total_tokens"])
        original = json.loads(next((self.root / "transcripts/asr").glob("*.json")).read_text())
        self.assertEqual(original["text"], "Qwen原文。")
        self.assertEqual(original["cache_metadata"]["source_ref"], "/fixtures/original.mp4")
        self.assertEqual(original["cache_metadata"]["category"], "asr_diagnostics")

    def test_organizer_records_provider_usage_and_transport_retry(self):
        data = {"choices": [{"message": {"content": "整理完成"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        cfg = {**organizer.DEFAULTS, "QWEN_ORGANIZE_COOLDOWN_DELAY": "0", "QWEN_ORGANIZE_RETRY_DELAY": "0"}
        diagnostics.start_run()
        with mock.patch.object(organizer.urllib.request, "urlopen", side_effect=[urllib.error.URLError("fixture"), io.BytesIO(json.dumps(data).encode())]):
            self.assertEqual(organizer.call_chat_completion(cfg, []), "整理完成")
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["model_calls"], 2)
        self.assertEqual(metrics["retries"], [{"stage": "organization", "reason": "connection", "count": 1}])
        self.assertIsNone(metrics["total_tokens"])
        with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_RUN_ID": "usage-run"}):
            with mock.patch.object(organizer.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
                organizer.call_chat_completion(cfg, [])
            metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["total_tokens"], 5)
        self.assertIsNone(metrics["reasoning_tokens"])

    def test_paper_requests_measure_real_calls_and_translation_stage(self):
        data = {"choices": [{"message": {"content": "全文翻译"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        cfg = {**quickread.DEFAULTS, "QWEN_QUICKREAD_RETRY_DELAY": "0"}
        diagnostics.start_run()
        with mock.patch.object(quickread.urllib.request, "urlopen", side_effect=[urllib.error.URLError("fixture"), io.BytesIO(json.dumps(data).encode())]) as request:
            self.assertEqual(quickread.translate_full_text("title", "source", 1, "paper body", cfg), "全文翻译")
        self.assertEqual(request.call_count, 2)
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["model_calls"], 2)
        self.assertIsNone(metrics["prompt_tokens"])
        self.assertEqual(metrics["retries"], [{"stage": "paper_translation", "reason": "connection", "count": 1}])
        self.assertEqual(metrics["stages"][0]["stage"], "paper_translation")
        self.assertGreaterEqual(metrics["stages"][0]["duration_seconds"], 0)

    def test_incognito_writes_neither_body_cache_nor_diagnostics(self):
        for value in ("true", "1", "yes", "on"):
            with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_INCOGNITO": value}):
                self.assertEqual(save_transcript_diagnostic("source", "private", {"transcript": "private body"}), "")
                self.assertIsNone(diagnostics.start_run())
                self.assertIsNone(diagnostics.begin_model_call("summary"))
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "transcripts").exists())

    def test_concurrent_subprocesses_do_not_lose_requests(self):
        diagnostics.start_run()
        script = ("import task_diagnostics as d\n"
                  "for n in range(6):\n"
                  "    call = d.begin_model_call('summary', 'fixture')\n"
                  "    d.finish_model_call(call, 'summary', 'completed', usage={'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3})\n")
        env = {**os.environ, "PYTHONPATH": str(ROOT / "worker/scripts")}
        def run(_):
            subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(run, range(3)))
        metrics = diagnostics.finalize_run("completed")["metrics"]
        self.assertEqual(metrics["model_calls"], 18)
        self.assertEqual(metrics["prompt_tokens"], 36)
        self.assertEqual(metrics["completion_tokens"], 18)
        self.assertEqual(metrics["total_tokens"], 54)


if __name__ == "__main__":
    unittest.main()
