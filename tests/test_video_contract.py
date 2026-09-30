from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker" / "scripts"))
from video_contract import SECTION_NAMES, review_transcript_changes, validate_video_note


def valid_video_note(raw: str = "这是完整的原始转写内容。") -> str:
    sections = {
        "一句话概括": "本期讨论行业经营和风险。",
        "速读摘要": "摘要内容完整。",
        "思维导图": "- 总主题\n  - 子主题",
        "结构化正文": "经营情况需要结合订单和现金流。",
        "金句与重要原话": "需要持续观察经营变化。",
        "可复习清单": "- 哪些指标需要跟踪？",
        "术语与概念": "订单：企业在未来履行的销售约定。",
        "校对正文": raw,
    }
    return "# 视频\n\n" + "\n\n".join(f"## {name}\n\n{value}" for name, value in sections.items()) + f"\n\n## 原始字幕\n\n{raw}\n"


class VideoContractTests(unittest.TestCase):
    def test_all_required_sections_and_proofread_quality_are_required(self):
        note = valid_video_note()
        self.assertTrue(validate_video_note(note).complete)
        missing = note.replace("## 思维导图\n\n- 总主题\n  - 子主题\n\n", "")
        self.assertIn("思维导图", validate_video_note(missing).missing_sections)
        placeholder = note.replace("摘要内容完整。", "【AI待处理：摘要】")
        self.assertFalse(validate_video_note(placeholder).complete)

    def test_explicit_transcription_only_contract_requires_raw_transcript(self):
        raw_only = "# 视频\n\n## 原始字幕\n\n这是完整的转写。\n"
        self.assertTrue(validate_video_note(raw_only, transcription_only=True).complete)
        self.assertFalse(validate_video_note("# 视频\n", transcription_only=True).complete)
        self.assertFalse(validate_video_note(raw_only).complete)

    def test_private_source_cache_can_validate_notes_without_visible_raw_subtitles(self):
        note = valid_video_note().replace("## 原始字幕\n\n这是完整的原始转写内容。\n", "")
        result = validate_video_note(note, raw_transcript_override="这是完整的原始转写内容。")
        self.assertTrue(result.complete)

    def test_review_flags_meaning_changes_but_ignores_punctuation_and_grouping(self):
        source = "建议加仓50万股，苹果公司预计收入增长，资金约1,000元。"
        corrected = "不建议减仓500万股，英伟达公司预计收入下降，资金约1000元。"
        findings = review_transcript_changes(source, corrected)
        kinds = {item["kind"] for item in findings}
        self.assertIn("否定词", kinds)
        self.assertIn("方向词", kinds)
        self.assertIn("数字或单位", kinds)
        self.assertIn("公司/主体名称", kinds)

        format_only = review_transcript_changes("持有1,000股，苹果公司现金流稳定。", "持有1000股，苹果公司现金流稳定。")
        self.assertEqual(format_only, [])

    def test_magnitude_does_not_hide_a_changed_unit(self):
        for source, corrected in (("50万股", "50万元"), ("50亿股", "50亿美元"), ("10千克", "10千米")):
            with self.subTest(source=source):
                findings = review_transcript_changes("持有" + source + "。", "持有" + corrected + "。")
                self.assertIn("数字或单位", {item["kind"] for item in findings})
        self.assertEqual(review_transcript_changes("持有1万股。", "持有10,000股。"), [])
        self.assertEqual(review_transcript_changes("成本1,000元。", "成本1000块。"), [])

    def test_timestamped_source_positions_include_a_time_range(self):
        original = "1\n00:00:10 --> 00:00:12\n建议买入。\n\n2\n00:00:12 --> 00:00:14\n预计增长。"
        result = review_transcript_changes(original, original.replace("建议买入", "不建议卖出"))
        direction = next(item for item in result if item["kind"] == "方向词")
        self.assertEqual(direction["timestamp_range"], (10.0, 12.0))


if __name__ == "__main__":
    unittest.main()
