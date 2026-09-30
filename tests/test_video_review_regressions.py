"""Regression cases from the P0/P1 branch review (no network or real LLM)."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from test_worker import ROOT, runner as r, batch_transcriber as b, keyframes as k, worker, load_module
from test_video_contract import valid_video_note
from transcript_timing import attach_timing, load_note_timing, timing_path, timestamp_for_position
from transcript_quality import save_transcript_diagnostic, load_transcript_diagnostic


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        tmp = tempfile.TemporaryDirectory(prefix="lns-review-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.out = self.root / "notes"
        self.out.mkdir()
        self.cfg = {**r.DEFAULTS, "CONDA_ENV": "", "BILIBILI_OUTPUT_DIR": str(self.out),
                    "BILIBILI_STATE_DIR": str(self.root / "state"), "INDEX_DIR": str(self.root / "indexes"),
                    "EXTRACT_KEYFRAMES": "false", "OVERWRITE_OUTPUT": "true", "COOLDOWN_DELAY": "0"}
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "TRANSCRIPT_CACHE_DIR": str(self.root / "cache-transcript"), "CACHE_DIR": str(self.root / "cache"),
            "MODEL_CACHE_DIR": str(self.root / "models"), "ENABLE_OPENCC": "false",
            "LOCAL_NOTE_STUDIO_PYTHON_BIN": sys.executable, "LOCAL_NOTE_STUDIO_INCOGNITO": "false",
        }))
        self.log = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def journal(self):
        return json.loads(next((self.out / ".local-note-studio-transactions").glob("*.json")).read_text())

    def result(self):
        return json.loads([line.split(":", 1)[1] for line in self.log.getvalue().splitlines()
                           if line.startswith("VIDEO_TRANSACTION_RESULT_JSON:")][-1])

    def produce(self, cfg):
        staged = Path(cfg["BILIBILI_OUTPUT_DIR"]) / "video.md"
        staged.write_text(valid_video_note().replace("# 视频", "# 新笔记"))
        r.append_processed("123", cfg, str(staged))
        return 0

    def test_crash_after_replace_restores_old_note_and_leaves_indexes_and_cache_unchanged(self):
        target = self.out / "video.md"
        old = valid_video_note()
        target.write_text(old)
        save_transcript_diagnostic("source-by-note", str(target), {"transcript": "旧原文"})
        original = r.atomic_write_text
        def crash(path, content):
            original(path, content)
            if path == target:
                raise KeyboardInterrupt("crash after atomic rename")
        with mock.patch.object(r, "atomic_write_text", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                r.run_video_transaction(str(self.out), self.cfg, self.produce)
        r.recover_video_transactions(self.out)
        self.assertEqual(target.read_text(), old)
        self.assertEqual(self.journal()["state"], "rolled_back")
        self.assertTrue(self.journal()["entries"][0]["prepared"])
        self.assertFalse(self.journal()["entries"][0]["committed"])
        self.assertFalse((self.root / "state/processed_videos.txt").exists())
        self.assertEqual(load_transcript_diagnostic("source-by-note", str(target))["transcript"], "旧原文")

    def test_new_note_is_removed_if_crash_precedes_commit_acknowledgement(self):
        target = self.out / "video.md"
        original = r.atomic_write_text
        def crash(path, content):
            original(path, content)
            if path == target:
                raise KeyboardInterrupt("crash")
        with mock.patch.object(r, "atomic_write_text", side_effect=crash), self.assertRaises(KeyboardInterrupt):
            r.run_video_transaction(str(self.out), self.cfg, self.produce)
        self.assertFalse(target.exists())
        self.assertEqual(self.journal()["state"], "rolled_back")

    def test_recovery_preserves_user_edit_and_reports_conflict(self):
        target = self.out / "video.md"
        target.write_text(valid_video_note())
        original = r.atomic_write_text
        def edit_then_crash(path, content):
            original(path, content)
            if path == target:
                target.write_text("用户在提交时修改的内容")
                raise KeyboardInterrupt("crash")
        with mock.patch.object(r, "atomic_write_text", side_effect=edit_then_crash), self.assertRaises(KeyboardInterrupt):
            r.run_video_transaction(str(self.out), self.cfg, self.produce)
        self.assertEqual(target.read_text(), "用户在提交时修改的内容")
        self.assertEqual(self.journal()["state"], "recovery_conflict")

    def test_index_recovery_rolls_forward_idempotently(self):
        with mock.patch.object(r, "_merge_processed_ids", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                r.run_video_transaction(str(self.out), self.cfg, self.produce)
        self.assertEqual(self.journal()["state"], "notes_committed")
        self.assertTrue((self.out / "video.md").exists())
        r.recover_video_transactions(self.out)
        r.recover_video_transactions(self.out)
        self.assertEqual(self.journal()["state"], "complete")
        self.assertEqual((self.root / "state/processed_videos.txt").read_text().splitlines(), ["123"])

    def test_publish_failure_never_advances_processed_ids(self):
        with mock.patch.object(r, "_rewrite_and_commit_note", side_effect=OSError("publish failed")):
            with self.assertRaises(OSError):
                r.run_video_transaction(str(self.out), self.cfg, self.produce)
        self.assertFalse((self.root / "state/processed_videos.txt").exists())
        self.assertFalse((self.out / "video.md").exists())

    def test_batch_continues_after_real_child_failure_and_only_records_published_notes(self):
        videos = [{"avid": str(i), "bvid": f"BV{i}", "title": str(i)} for i in (111, 222, 333)]
        seen = []
        def transcribe(cfg, script, url):
            seen.append(url)
            if "BV222" in url:
                return [sys.executable, "-c", "import sys; print('ASR failed'); sys.exit(1)"]
            path = Path(cfg["BILIBILI_OUTPUT_DIR"]) / ("111.md" if "BV111" in url else "333.md")
            return [sys.executable, "-c", f"from pathlib import Path; p=Path({str(path)!r}); p.write_text({valid_video_note()!r}); print('GENERATED_MARKDOWN_PATH:'+str(p))"]
        with (
            mock.patch.object(r, "project_env", return_value=os.environ.copy()),
            mock.patch.object(r, "python_command", return_value=[sys.executable, "-c", "print('fixture')"]),
            mock.patch.object(r, "bash_command", side_effect=transcribe),
            mock.patch.object(r, "parse_scanner_output", return_value=videos),
            mock.patch.object(r, "postprocess_video_notes"),
        ):
            status = r.run_video_transaction(str(self.out), self.cfg, lambda cfg: r.run_collection_batch(
                ROOT / "worker", cfg, 0, False, "favorite", "9", "", False))
        self.assertEqual(status, 1)
        self.assertEqual(len(seen), 3)
        self.assertTrue((self.out / "111.md").exists())
        self.assertTrue((self.out / "333.md").exists())
        self.assertEqual((self.root / "state/processed_videos.txt").read_text().splitlines(), ["111", "333"])
        failures = json.loads((self.out / ".local-note-studio-batch-failures.json").read_text())
        self.assertIn("222", json.dumps(failures))
        self.assertEqual({key: self.result()[key] for key in ("total", "created", "failed")},
                         {"total": 3, "created": 2, "failed": 1})

    def local_media(self):
        media = self.root / "clip.mp3"
        media.write_bytes(b"fixture; SRT bypasses ASR")
        text = "订单交付和客户验收决定本季度回款进度，需要按合同节点持续跟踪。"
        media.with_suffix(".srt").write_text("1\n00:00:10,000 --> 00:00:14,000\n" + text + "\n")
        return media, text

    def test_existing_complete_note_bypasses_source_import_and_llm(self):
        media, _ = self.local_media()
        target = self.out / "video.md"
        target.write_text(valid_video_note())
        before = target.read_bytes()
        with mock.patch.object(r, "python_command") as summary:
            status = r.run_video_transaction(str(self.out), {**self.cfg, "OVERWRITE_OUTPUT": "false"},
                lambda cfg: r.run_local_file(ROOT / "worker", cfg, str(media), False, "video.md"))
        self.assertEqual(status, 0)
        summary.assert_not_called()
        self.assertNotIn("字幕导入完成", self.log.getvalue())
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(self.result()["skipped"], 1)

    def test_repair_of_existing_incomplete_note_runs_only_in_staging(self):
        original = self.out / "video.md"
        original.write_text("# 视频\n\n## 原始字幕\n\n这是完整的原始转写内容。\n")
        original_text = original.read_text()
        def repair(cfg):
            staged = r.stage_existing_note(original, cfg)
            staged.write_text(valid_video_note())
            self.assertEqual(original.read_text(), original_text)
            return 0
        self.assertEqual(r.run_video_transaction(str(self.out), {**self.cfg, "OVERWRITE_OUTPUT": "false"}, repair), 0)
        self.assertEqual(original.read_text(), valid_video_note())
        self.assertEqual(self.result()["updated"], 1)

    def test_srt_import_preserves_timing_after_staging_is_removed(self):
        media, text = self.local_media()
        status = r.run_video_transaction(str(self.out), {**self.cfg, "VIDEO_OUTPUT_MODE": "transcription-only"},
            lambda cfg: r.run_local_file(ROOT / "worker", cfg, str(media), False, "video.md"))
        self.assertEqual(status, 0)
        note = self.out / "video.md"
        segments = load_note_timing(note)
        self.assertEqual(segments, [{"start": 10., "end": 14., "text": text}])
        self.assertEqual(timestamp_for_position(text, 2, segments), (10., 14.))
        self.assertFalse(timing_path(note).exists())
        self.assertFalse(list((self.out / ".local-note-studio-staging").glob("*/output/*.md")))

    def test_keyframe_generation_uses_timing_even_without_visible_subtitles(self):
        media, text = self.local_media()
        video = media.with_suffix(".mp4")
        video.write_bytes(b"fixture")
        note = self.out / "video.md"
        note.write_text(f"# 视频\n\n## 结构化正文\n\n### 订单交付\n\n{text}\n\n## 校对正文\n\n{text}\n")
        attach_timing(note, media.with_suffix(".srt"))
        with (
            mock.patch.object(k, "_ffprobe_duration", return_value=120),
            mock.patch.object(k, "_scene_timestamps", return_value=[12.]),
            mock.patch.object(k, "_frame_fingerprint", return_value=b"usable"),
            mock.patch.object(k, "_usable_fingerprint", return_value=True),
            mock.patch.object(k, "_extract_frame", side_effect=lambda video, ts, path: path.write_bytes(b"jpeg")),
        ):
            result = k.add_keyframes_to_note(note, {"source_path": str(video)}, max_frames=1)
        self.assertEqual(result["status"], "generated")
        self.assertTrue((note.parent / result["assets"][0]).is_file())
        self.assertEqual(result["frames"][0]["transcript_range_seconds"], [10., 14.])
        self.assertEqual(result["frames"][0]["alignment"], "transcript-match")
        self.assertIn("00:10–00:14", note.read_text())

    def test_incognito_timing_is_ephemeral(self):
        media, _ = self.local_media()
        note = self.out / "video.md"
        with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_INCOGNITO": "true"}):
            attach_timing(note, media.with_suffix(".srt"))
            self.assertEqual(len(load_note_timing(note)), 1)
            self.assertFalse((self.root / "cache-transcript").exists())
            timing_path(note).unlink()
            self.assertEqual(load_note_timing(note), [])

    def proofread_context(self):
        text = "订单交付需要逐一核实。"
        self.stack.enter_context(mock.patch.object(b, "_chunk_text_ranges", return_value=[(0, 5, text[:5]), (5, len(text), text[5:])]))
        self.stack.enter_context(mock.patch.object(b, "SUMMARY_PROOFREAD_COOLDOWN_DELAY", 30))
        self.stack.enter_context(mock.patch.object(b, "SUMMARY_CHUNK_COOLDOWN_DELAY", 0))
        return text

    def test_call_budget_caps_quality_retries_across_segments(self):
        text = self.proofread_context()
        with (
            mock.patch.dict(os.environ, {"SUMMARY_PROOFREAD_CALL_BUDGET": "2"}),
            mock.patch.object(b, "SUMMARY_PROOFREAD_MAX_RETRIES", 2),
            mock.patch.object(b, "_parse_combined_summary", side_effect=[{"proofread": text[:5]}, {}, {}, {}]),
            mock.patch.object(b, "_call_llm", return_value=b.LLMResponse("fixture", "stop")) as calls,
            mock.patch.object(b.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "预算"):
                b._run_chunked_proofread("budget", "title", text, str(self.out / "video.md"))
        self.assertEqual(calls.call_count, 2)

    def test_call_budget_also_caps_http_retries(self):
        text = self.proofread_context()
        with (
            mock.patch.dict(os.environ, {"SUMMARY_PROOFREAD_CALL_BUDGET": "2"}),
            mock.patch.object(b, "SUMMARY_API_KEY", "fixture"),
            mock.patch.object(b, "LLM_MAX_RETRIES", 5),
            mock.patch.object(b, "SUMMARY_PROOFREAD_MAX_RETRIES", 5),
            mock.patch.object(b.requests, "post", side_effect=b.requests.ConnectionError("unavailable")) as calls,
            mock.patch.object(b.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "预算"):
                b._run_chunked_proofread("budget-http", "title", text, str(self.out / "video.md"))
        self.assertEqual(calls.call_count, 2)

    def test_independent_proofread_cooldown_and_cached_resume(self):
        text = self.proofread_context()
        note = str(self.out / "video.md")
        with (
            mock.patch.object(b, "_parse_combined_summary", side_effect=[{"proofread": text[:5]}, {"proofread": text[5:]}]),
            mock.patch.object(b, "_call_llm", return_value=b.LLMResponse("fixture", "stop")) as calls,
            mock.patch.object(b.time, "sleep") as sleep,
        ):
            b._run_chunked_proofread("cooldown", "title", text, note)
            self.assertEqual(calls.call_count, 2)
            sleep.assert_called_once_with(30)
            b._run_chunked_proofread("cooldown", "title", text, note)
            self.assertEqual(calls.call_count, 2)
            sleep.assert_called_once_with(30)

    def test_transaction_keeps_keyframe_provenance_with_published_images(self):
        def produce(cfg):
            stage = Path(cfg["BILIBILI_OUTPUT_DIR"])
            asset = stage / "assets/video-keyframes-fixture/frame.jpg"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"jpeg")
            asset.with_name("keyframes-manifest.json").write_text('{"frames": [{"alignment": "transcript-match"}]}')
            (stage / "video.md").write_text(valid_video_note() + "\n![frame](assets/video-keyframes-fixture/frame.jpg)\n")
            return 0
        self.assertEqual(r.run_video_transaction(str(self.out), self.cfg, produce), 0)
        self.assertTrue((self.out / "assets/video-keyframes-fixture/frame.jpg").exists())
        manifest = self.out / "assets/video-keyframes-fixture/keyframes-manifest.json"
        self.assertEqual(json.loads(manifest.read_text())["frames"][0]["alignment"], "transcript-match")

    def test_worker_counts_transaction_once_and_retains_partial_outputs(self):
        req = worker.TaskRequest.from_mapping({"task": "local-video", "source": str(self.root / "clip.mp3"),
                                               "output_dir": str(self.out), "keep_original_subtitles": True})
        result = worker.TaskResult(run_id="regression", caller="gui", task=req.task, status="completed",
                                   started_at=worker.utc_now(), source_ref="fixture", output_dir=req.output_dir)
        note = self.out / "video.md"
        def child_failure(*args):
            note.write_text("---\nsource_path: /fixture/clip.mp3\n---\n" + valid_video_note())
            raise RuntimeError('LOCAL_BATCH_RESULT_JSON:{"total":4,"changed":1,"skipped":1,"failed":2}\n'
                               'VIDEO_TRANSACTION_RESULT_JSON:{"total":4,"created":1,"updated":0,"skipped":1,"failed":2}')
        with mock.patch.object(worker, "run_process", side_effect=child_failure):
            with self.assertRaises(RuntimeError):
                worker.execute_request(req, result)
        self.assertEqual(result.counts["discovered"], 4)
        self.assertEqual(result.counts["skipped"], 1)
        self.assertEqual(result.counts["failed"], 2)
        self.assertEqual(result.counts["created"], 1)
        self.assertEqual(result.outputs, [str(note)])
        self.assertEqual(result.status, "partial_failed")

    def test_whisper_writes_current_timing_segments_for_downstream_use(self):
        whisper = load_module("whisper_review_test", ROOT / "worker/scripts/bilibili/whisper_transcribe.py")
        media, text = self.local_media()
        class Audio:
            def tobytes(self):
                return b"audio"
            def __len__(self):
                return 16000
        segments = [{"start": 10., "end": 14., "text": text}]
        result = {"segments": segments, "text": text}
        output, timing = self.root / "transcript.txt", self.root / "segments.json"
        mlx = types.SimpleNamespace(audio=types.SimpleNamespace(load_audio=lambda _: Audio()), transcribe=mock.Mock())
        with (
            mock.patch.dict(sys.modules, {"mlx_whisper": mlx, "numpy": types.SimpleNamespace(asarray=lambda x: x)}),
            mock.patch.object(sys, "argv", ["whisper", "--audio", str(media), "--output-file", str(output),
                                          "--segments-output", str(timing), "--model-path", str(self.root), "--progress-interval", "0"]),
            mock.patch.object(whisper, "transcribe_with_repair", return_value=(result, result, [], [])),
        ):
            whisper.main()
        self.assertEqual(json.loads(timing.read_text())["segments"], segments)
        self.assertIn(text, output.read_text())


if __name__ == "__main__":
    unittest.main()
