from __future__ import annotations

import importlib.util
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker/scripts"))
from transcript_quality import (
    PHRASE_REPEAT_RE, proofread_errors, repetition_errors, save_transcript_diagnostic,
    short_repetition_candidates,
)
from test_video_contract import valid_video_note
from video_contract import raw_transcript, validate_video_note


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
    def short_repeat_fixture(self):
        before = "这段课程解释人类偏好与模型训练之间的明确边界，所以你不停地去"
        after = "了下去就是人类自身的极限，因此模型容易讨好人类而非解决问题。"
        closing = "接下来需要比较新的学习框架与现有训练方法之间的差别。"
        segments = [
            {"start": 0, "end": 20, "text": before},
            {"start": 20, "end": 22, "text": "优化" * 4},
            {"start": 22, "end": 24, "text": "优化" + after},
            {"start": 24, "end": 60, "text": closing},
        ]
        text = "".join(segment["text"] for segment in segments)
        initial = {"language": "zh", "text": text, "segments": segments}
        words = [{"word": before, "start": 0, "end": 18.2}]
        for index, character in enumerate("优化" * 5):
            words.append({"word": character, "start": 18.2 + index * 0.2,
                          "end": 18.2 + (index + 1) * 0.2, "probability": 0.55})
        words.append({"word": after + closing, "start": 20.2, "end": 40})
        independent = {"language": "zh", "text": text,
                       "segments": [{"start": 0, "end": 40, "text": text, "words": words}]}
        return initial, independent

    def test_generic_repeat_gate_stays_strict_and_proofread_requires_matching_source_context(self):
        source, _ = self.short_repeat_fixture()
        text = source["text"]
        self.assertIn("短语循环重复", repetition_errors(text))
        self.assertIn("短语循环重复", proofread_errors(text))
        self.assertEqual(proofread_errors(text, text), [])
        response = batch.LLMResponse(f"[[LNS_SECTION:proofread]]{text}[[/LNS_SECTION:proofread]]", "stop")
        self.assertEqual(batch._parse_combined_summary(response, ["proofread"], text)["proofread"], text)
        self.assertIn("短语循环重复", proofread_errors(text + text, text))
        other_context = "居民家庭讨论消费开支与房屋贷款之间的关系，所以需要做好" + "优化" * 5 + "接着银行会审核资产负债及经营现金流，不涉及模型训练。"
        self.assertIn("短语循环重复", proofread_errors(other_context, text))

    def test_source_supported_repeat_limits_use_the_shortest_period(self):
        source, _ = self.short_repeat_fixture()
        for phrase, count, accepted in (("优化", 6, True), ("优化", 7, False),
                                        ("优化", 10, False), ("优化模型参数", 5, False)):
            text = source["text"].replace("优化" * 5, phrase * count)
            with self.subTest(phrase=phrase, count=count):
                self.assertEqual("短语循环重复" not in proofread_errors(text, text), accepted)
        self.assertEqual(short_repetition_candidates("优化" * 10), [])
        self.assertIn("短语循环重复", proofread_errors(source["text"].replace("优化" * 5, "优化" * 6), source["text"]))

    def test_proofread_cannot_move_a_repeat_to_a_distant_place_with_identical_context(self):
        before = "我们回到刚才所说的模型训练方法需要围绕成本和训练效率做判断各种办法都要比较能够改进的方向所以接下来继续"
        after = "。随后讨论具体指标和实验记录以便检查结论能否用于当前场景不同团队还会根据真实反馈安排后续工作并总结经验。"
        separator = "".join(f"第{i}项测试比较设备成本与交付时间，记录需求变化和执行结果。" for i in range(20))
        source = before + "优化" * 5 + after + separator + before + "提升" + after
        corrected = before + "提升" + after + separator + before + "优化" * 5 + after
        self.assertEqual(proofread_errors(source, source), [])
        self.assertIn("短语循环重复", proofread_errors(corrected, source))

    def test_confirmed_asr_emphasis_retains_original_words_and_records_independent_evidence(self):
        initial, independent = self.short_repeat_fixture()
        call = mock.Mock(side_effect=[initial, copy.deepcopy(initial), independent])
        raw, final, attempts, errors = asr.transcribe_with_repair(
            Audio(60), call, {"condition_on_previous_text": False, "initial_prompt": "课程术语"}, lambda a, b: True)
        self.assertEqual(errors, [])
        self.assertEqual(raw, initial)
        self.assertEqual(final["text"], initial["text"])
        self.assertEqual(final["segments"], initial["segments"])
        self.assertEqual(call.call_count, 3)
        self.assertEqual(len(call.call_args.args[0]), 40 * 16000)
        self.assertTrue(call.call_args.kwargs["word_timestamps"])
        self.assertNotIn("initial_prompt", call.call_args.kwargs)
        self.assertEqual(call.call_args.kwargs["temperature"], 0.0)
        self.assertEqual(attempts[-1]["kind"], "short_repeat_confirmation")
        evidence = final["confirmed_short_repetitions"][0]
        self.assertEqual((evidence["phrase"], evidence["count"]), ("优化", 5))
        self.assertEqual(len(evidence["word_ranges"]), 5)
        self.assertIn("position_key", evidence)

    def test_word_confirmation_rejects_zero_overlap_missing_and_changed_repeats(self):
        initial, independent = self.short_repeat_fixture()
        for kind in ("zero", "overlap", "missing", "changed", "too_long"):
            invalid = copy.deepcopy(independent)
            words = invalid["segments"][0]["words"]
            if kind == "zero":
                words[1]["end"] = words[1]["start"]
            elif kind == "overlap":
                words[2]["start"] = words[1]["start"]
            elif kind == "missing":
                invalid["segments"][0].pop("words")
            elif kind == "changed":
                words[1]["word"] = "其"
                invalid["text"] = invalid["text"].replace("优化" * 5, "其化" + "优化" * 4)
            else:
                for index, word in enumerate(words[1:11]):
                    word.update(start=18.2 + index * 0.8, end=18.2 + (index + 1) * 0.8)
            with self.subTest(kind=kind):
                self.assertEqual(asr._confirm_short_repetitions(initial["segments"], invalid, 2, 42), [])

    def test_word_confirmation_requires_valid_source_segment_timestamps(self):
        initial, independent = self.short_repeat_fixture()
        for kind in ("zero", "reversed", "nan", "negative", "overlap"):
            source = copy.deepcopy(initial["segments"])
            if kind == "zero":
                source[1]["end"] = source[1]["start"]
            elif kind == "reversed":
                source[1]["end"] = source[1]["start"] - 1
            elif kind == "nan":
                source[1]["start"] = float("nan")
            elif kind == "negative":
                source[1]["start"] = -1
            else:
                source[2]["start"] = source[1]["end"] - 1
            with self.subTest(kind=kind):
                self.assertEqual(asr._confirm_short_repetitions(source, independent, 2, 42), [])

    def test_confirmed_position_does_not_approve_another_repeat_in_the_same_segment(self):
        initial, _ = self.short_repeat_fixture()
        text = initial["text"] + initial["text"]
        segments = [{"start": 0, "end": 60, "text": text}]
        joined, spans = asr._segment_text(segments)
        first = next(PHRASE_REPEAT_RE.finditer(joined))
        evidence = [{"phrase": "优化", "count": 5, "start": 0, "end": 60,
                     "position_key": asr._span_key(*first.span(), spans)}]
        self.assertIn("短语循环重复", asr._asr_repetition_errors(segments, evidence))

    def test_confirmation_and_repairs_share_the_call_budget_and_still_block_other_faults(self):
        base, independent = self.short_repeat_fixture()
        initial = copy.deepcopy(base)
        initial["segments"].extend([
            {"start": 60, "end": 120, "text": "中间课程讨论不同算法的正常训练过程。"},
            {"start": 120, "end": 180, "text": "。"},
        ])
        initial["text"] = "".join(segment["text"] for segment in initial["segments"])
        call = mock.Mock(side_effect=[initial, base, independent])
        self.assertEqual(asr.ASR_REPAIR_CALL_BUDGET, 8)
        with mock.patch.object(asr, "ASR_REPAIR_CALL_BUDGET", 2):
            _, _, attempts, errors = asr.transcribe_with_repair(Audio(180), call, {}, lambda a, b: True)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(any("预算" in error for error in errors))
        self.assertTrue(any("120.0" in error for error in errors))

    def test_confirmed_short_repeat_does_not_hide_global_paragraph_repetition(self):
        base, independent = self.short_repeat_fixture()
        paragraph = "".join(f"第{i}家企业分析设备采购与订单交付，核验执行过程中的预算。" for i in range(15))
        initial = copy.deepcopy(base)
        initial["segments"].extend([
            {"start": 60, "end": 120, "text": paragraph},
            {"start": 120, "end": 180, "text": paragraph},
        ])
        initial["text"] += "\n\n" + paragraph + "\n\n" + paragraph
        call = mock.Mock(side_effect=[initial, base, independent])
        _, final, _, errors = asr.transcribe_with_repair(Audio(180), call, {}, lambda a, b: True)
        self.assertIn("confirmed_short_repetitions", final)
        self.assertIn("相邻大段内容重复", errors)

    def test_source_supported_repeat_can_hide_subtitles_and_pass_final_contract(self):
        initial, _ = self.short_repeat_fixture()
        source = initial["text"]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"TRANSCRIPT_CACHE_DIR": tmp}):
            note = Path(tmp) / "note.md"
            note.write_text(valid_video_note(source))
            self.assertTrue(batch._can_remove_original_subtitles(note.read_text()))
            with mock.patch.object(batch, "SUMMARY_API_KEY", "fixture"), mock.patch.object(batch, "KEEP_ORIGINAL_SUBTITLES", False), mock.patch.object(batch, "_call_llm") as call:
                outcome = batch.generate_summary(str(note))
            self.assertEqual(outcome.status, "completed")
            call.assert_not_called()
            self.assertEqual(raw_transcript(note.read_text()), "")
            self.assertTrue(validate_video_note(note.read_text(), raw_transcript_override=source).complete)

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
        with mock.patch.object(batch, "_call_llm", side_effect=[bad, good]) as call, mock.patch.object(batch, "SUMMARY_CHUNK_COOLDOWN_DELAY", 0), mock.patch.object(batch, "SUMMARY_PROOFREAD_COOLDOWN_DELAY", 0), mock.patch.object(batch, "save_transcript_diagnostic"):
            self.assertEqual(batch._run_chunked_proofread("测试", "标题", "产业研究需要关注订单"), "产业研究需要关注订单。")
        self.assertEqual(call.call_count, 2)
        self.assertIn("转录文本分段：\n产业研究需要关注订单", call.call_args.args[1])
        self.assertFalse(call.call_args.kwargs["enable_thinking"])

    def test_readonly_context_carries_question_across_boundary_without_duplication(self):
        responses = [batch.LLMResponse("[[LNS_SECTION:proofread]]我说五成仓，[[/LNS_SECTION:proofread]]", "stop"),
                     batch.LLMResponse("[[LNS_SECTION:proofread]]你们就一定要照做吗？[[/LNS_SECTION:proofread]]", "stop")]
        with mock.patch.object(batch, "_chunk_text_with_overlap", return_value=["我说五成仓", "你们就一定要照做"]), mock.patch.object(batch, "_call_llm", side_effect=responses) as call, mock.patch.object(batch, "SUMMARY_CHUNK_COOLDOWN_DELAY", 0), mock.patch.object(batch, "SUMMARY_PROOFREAD_COOLDOWN_DELAY", 0), mock.patch.object(batch, "save_transcript_diagnostic"):
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
