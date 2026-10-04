"""Real disposable-cache payloads and incognito must honor T-118 maintenance."""
from __future__ import annotations

import json
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
sys.path.insert(0, str(ROOT / "worker" / "scripts"))
import cache_maintenance as maintenance
import convert_sources_to_md as convert
import task_diagnostics as diagnostics


class ConversionCacheTests(unittest.TestCase):
    def test_conversion_actual_requests_keep_provider_usage_and_missing_values(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {
            "LOCAL_NOTE_STUDIO_STATE_DIR": temporary, "LOCAL_NOTE_STUDIO_RUN_ID": "conversion-run",
            "LOCAL_NOTE_STUDIO_INCOGNITO": "false",
        }):
            cfg = {**convert.DEFAULTS, "DEFAULT_LLM_MODEL": "fixture-model", "DEFAULT_LLM_API_BASE": "http://fixture/v1"}
            data = {"choices": [{"message": {"content": "accepted"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}}
            with mock.patch.object(convert.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
                convert._chat_completion(cfg, [])
            summary = diagnostics.summarize_run()["metrics"]
            self.assertEqual(summary["llm_calls"], 1)
            self.assertEqual(summary["total_tokens"], 17)
            self.assertIsNone(summary["reasoning_tokens"])
            del data["usage"]
            with mock.patch.object(convert.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
                convert._chat_completion(cfg, [])
            summary = diagnostics.summarize_run()["metrics"]
            self.assertEqual(summary["llm_calls"], 2)
            self.assertIsNone(summary["total_tokens"])

    def test_real_image_cache_can_be_previewed_but_queue_reference_protects_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            env = {"LOCAL_NOTE_STUDIO_STATE_DIR": str(root / "state"),
                   "LOCAL_NOTE_STUDIO_APP_DATA_DIR": str(root),
                   "LOCAL_NOTE_STUDIO_RUN_ID": "image-run",
                   "LOCAL_NOTE_STUDIO_TASK": "bilibili-opus",
                   "LOCAL_NOTE_STUDIO_SOURCE_REF": "opus/1234",
                   "LOCAL_NOTE_STUDIO_INCOGNITO": "false"}
            cfg = {"DEFAULT_LLM_MODEL": "fixture-model", "OPUS_IMAGE_ANALYSIS_CACHE_DIR": str(root / "state" / "opus-image-analysis-cache")}
            with mock.patch.dict(os.environ, env):
                convert.save_opus_image_analysis_cache(cfg, "a" * 64, "vision", "context", {"visible_text": "private text"})
                path = convert.opus_image_cache_path(cfg, "a" * 64, "vision", "context")
                self.assertEqual(convert.load_opus_image_analysis_cache(cfg, "a" * 64, "vision", "context")["visible_text"], "private text")
                self.assertEqual(json.loads(path.read_text())["cache_metadata"]["run_id"], "image-run")
                preview = maintenance.handle_cache_request("preview", {"categories": ["disposable_cache"], "older_than_days": 0}, env)
                self.assertEqual(preview["candidate_count"], 1)
                maintenance.handle_cache_request("references", {"entries": [{"id": "queue:future", "status": "waiting", "request": {"task": "bilibili-opus", "source": "https://www.bilibili.com/opus/1234"}}]}, env)
                result = maintenance.handle_cache_request("clean", {"preview_id": preview["preview_id"]}, env)
                self.assertEqual(result["deleted_bytes"], 0)
                self.assertTrue(path.exists())

    def test_incognito_does_not_read_or_create_body_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg = {"DEFAULT_LLM_MODEL": "fixture-model", "OPUS_IMAGE_ANALYSIS_CACHE_DIR": temporary,
                   "LOCAL_NOTE_STUDIO_INCOGNITO": "true"}
            with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_INCOGNITO": "false"}):
                convert.save_opus_image_analysis_cache(cfg, "a" * 64, "ocr", "context", {"visible_text": "private text"})
                self.assertEqual(list(pathlib.Path(temporary).iterdir()), [])
                self.assertIsNone(convert.load_opus_image_analysis_cache(cfg, "a" * 64, "ocr", "context"))


if __name__ == "__main__":
    unittest.main()
