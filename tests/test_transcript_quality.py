from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker/scripts"))
from transcript_quality import proofread_errors, save_transcript_diagnostic


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


asr = load("quality_whisper", ROOT / "worker/scripts/bilibili/whisper_transcribe.py")
batch = load("quality_batch", ROOT / "worker/scripts/bilibili/batch_transcribe.py")


class Audio:
    def __init__(self, seconds):
        self.samples = int(seconds * 16000)

    def __len__(self):
        return self.samples

    def __getitem__(self, interval):
        return Audio((interval.stop - interval.start) / 16000)


class TranscriptQualityTests(unittest.TestCase):
    def test_actual_failure_shapes_are_rejected_even_with_complete_markers(self):
        for body in ["这里介绍产业和企业未来的盈利情况" * 40,
                     "应用。这边的。。。。。。。。。。下一段。",
                     "股票交易需要灵活。我们!!!!!!!!!!!!!!!!!!!!继续。",
                     "抄底抄底抄底抄底抄底抄底，然后看结构。"]:
            with self.subTest(body=body[:25]):
                response = batch.LLMResponse(f"[[LNS_SECTION:proofread]]{body}[[/LNS_SECTION:proofread]]", "stop")
                self.assertNotIn("proofread", batch._parse_combined_summary(response, ["proofread"]))

    def test_closed_but_truncated_response_is_rejected(self):
        response = batch.LLMResponse("[[LNS_SECTION:proofread]]看起来有句号。[[/LNS_SECTION:proofread]]", "length")
        self.assertEqual(batch._parse_combined_summary(response, ["proofread"]), {})

    def test_missing_middle_is_detected_despite_similar_overall_length(self):
        first = "供应链分析关注设备交付以及订单兑现。" * 15
        middle = "算法团队围绕训练效率展开讨论，比较不同方案成本。" * 15
        last = "消费需求取决于居民就业、工资与储蓄变化。" * 15
        self.assertTrue(any("覆盖不足" in e for e in proofread_errors(first + last + last, first + middle + last)))

    def test_punctuation_and_small_homophone_corrections_preserve_coverage(self):
        source = "".join(f"第{i}家企业需要研究钢杆和现金流因此先看资产再看债务" for i in range(20))
        corrected = "\n\n".join(f"第{i}家企业需要研究杠杆和现金流，因此先看资产，再看债务。" for i in range(20))
        self.assertEqual(proofread_errors(corrected, source), [])

    def test_quality_retry_uses_original_chunk_then_accepts_repaired_text(self):
        bad = batch.LLMResponse("[[LNS_SECTION:proofread]]" + "产业企业研究逻辑" * 30 + "[[/LNS_SECTION:proofread]]", "stop")
        good = batch.LLMResponse("[[LNS_SECTION:proofread]]产业研究需要关注订单。[[/LNS_SECTION:proofread]]", "stop")
        with mock.patch.object(batch, "_call_llm", side_effect=[bad, good]) as call, mock.patch.object(batch, "SUMMARY_CHUNK_COOLDOWN_DELAY", 0):
            self.assertEqual(batch._run_chunked_proofread("测试", "标题", "产业研究需要关注订单"), "产业研究需要关注订单。")
        self.assertEqual(call.call_count, 2)
        self.assertIn("转录文本分段：\n产业研究需要关注订单", call.call_args.args[1])
        self.assertFalse(call.call_args.kwargs["enable_thinking"])

    def test_readonly_context_carries_question_across_boundary_without_duplication(self):
        responses = [batch.LLMResponse("[[LNS_SECTION:proofread]]我说五成仓，[[/LNS_SECTION:proofread]]", "stop"),
                     batch.LLMResponse("[[LNS_SECTION:proofread]]你们就一定要照做吗？[[/LNS_SECTION:proofread]]", "stop")]
        with mock.patch.object(batch, "_chunk_text_with_overlap", return_value=["我说五成仓", "你们就一定要照做"]), mock.patch.object(batch, "_call_llm", side_effect=responses) as call, mock.patch.object(batch, "SUMMARY_CHUNK_COOLDOWN_DELAY", 0):
            text = batch._run_chunked_proofread("测试", "反问", "我说五成仓你们就一定要照做")
        self.assertIn("只读后文：你们就一定要照做", call.call_args_list[0].args[1])
        self.assertIn("只读前文：我说五成仓", call.call_args_list[1].args[1])
        self.assertEqual(text.count("我说五成仓"), 1)
        self.assertTrue(text.endswith("照做吗？"))

    def test_repeated_quality_failure_preserves_raw_and_blocks_summaries(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TRANSCRIPT_CACHE_DIR": tmp}):
            note = Path(tmp) / "note.md"
            original = ("# 课程\n\n## 校对正文\n\n" + batch.PLACEHOLDERS["proofread"] +
                        "\n\n## 速读摘要\n\n" + batch.PLACEHOLDERS["quick_summary"] +
                        "\n\n<details>\n<summary>📄 原始字幕</summary>\n\n唯一原文。\n\n</details>\n")
            note.write_text(original)
            with mock.patch.object(batch, "SUMMARY_API_KEY", "fixture"), mock.patch.object(batch, "_run_chunked_proofread", side_effect=RuntimeError("质量失败")), mock.patch.object(batch, "_run_summary_sections") as derived:
                self.assertFalse(batch.generate_summary(str(note)))
            derived.assert_not_called()
            self.assertEqual(note.read_text(), original)
            cached = json.loads(next((Path(tmp) / "source").glob("*.json")).read_text())
            self.assertEqual(cached["transcript"], "唯一原文。")

    def test_private_cache_is_durable_and_incognito_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TRANSCRIPT_CACHE_DIR": tmp, "LOCAL_NOTE_STUDIO_INCOGNITO": "false"}):
            path = Path(save_transcript_diagnostic("asr", "source", {"text": "原文", "segments": [{"start": 0, "end": 2}]}))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["segments"][0]["end"], 2)
            with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_INCOGNITO": "true"}):
                self.assertEqual(save_transcript_diagnostic("asr", "other", {"text": "秘密"}), "")
            self.assertEqual(len(list(path.parent.glob("*.json"))), 1)

    def test_bad_prompt_examples_are_gone(self):
        prompt = batch._build_domain_prompt("finance,computer,legal,engineering")
        for bad in ["杠杆→钢杆", "对冲→对充", "期货→期权", "Git→给特", "API→A P I"]:
            self.assertNotIn(bad, prompt)

    def test_asr_retries_only_bad_window_and_keeps_original_evidence(self):
        initial = {"language": "zh", "text": "开场。。。。结尾", "segments": [
            {"start": 0, "end": 60, "text": "开场"},
            {"start": 60, "end": 120, "text": "。。。。"},
            {"start": 120, "end": 180, "text": "结尾"}]}
        retry = {"text": "恢复的内容", "segments": [{"start": 0, "end": 60, "text": "恢复的内容"}]}
        call = mock.Mock(side_effect=[initial, retry])
        raw, final, attempts, errors = asr.transcribe_with_repair(Audio(180), call, {"condition_on_previous_text": False}, lambda a, b: True)
        self.assertEqual(raw, initial)
        self.assertEqual(final["text"], "开场 恢复的内容 结尾")
        self.assertEqual(final["segments"][1]["start"], 60)
        self.assertEqual(len(call.call_args.args[0]), 60 * 16000)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(errors, [])

    def test_asr_persistent_failure_is_bounded_and_reported(self):
        broken = {"text": "。", "segments": [{"start": 0, "end": 60, "text": "。"}]}
        call = mock.Mock(return_value=broken)
        _raw, _final, attempts, errors = asr.transcribe_with_repair(Audio(60), call, {}, lambda a, b: True)
        self.assertEqual(call.call_count, 2)
        self.assertTrue(errors)
        self.assertEqual(len(attempts), 1)

    def test_silence_does_not_trigger_missing_speech_retry(self):
        result = {"text": "开场。", "segments": [{"start": 0, "end": 10, "text": "开场。"}]}
        self.assertEqual(asr.suspect_intervals(result, 70, lambda a, b: False), [])
        self.assertEqual(asr.suspect_intervals(result, 70, lambda a, b: True), [(10, 70)])


if __name__ == "__main__":
    unittest.main()
