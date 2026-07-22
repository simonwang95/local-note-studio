from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "worker"
sys.path.insert(0, str(WORKER_DIR))

import automation_profiles as profiles
import local_notes_agent as agent
import local_notes_mcp as mcp
import local_notes_retrieval as retrieval


def frontmatter(**values: object) -> str:
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif value is None:
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


class NoteRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.notes = self.root / "notes"
        self.notes.mkdir()
        self.state = self.root / "state"
        self.indexes = self.state / "indexes"
        self.indexes.mkdir(parents=True)
        self.profile_path = self.root / "profiles.json"
        self.profile_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "profiles": [
                        {
                            "id": "fixture",
                            "enabled": True,
                            "up_mid": "123456",
                            "content_types": ["opus", "video"],
                            "output_dir": str(self.notes),
                            "allowed_output_roots": [str(self.root)],
                            "allowed_input_roots": [str(self.root / "inbox")],
                            "allowed_domains": ["bilibili.com"],
                            "stock_terms": True,
                            "overwrite_outputs": False,
                            "limit": 20,
                            "max_limit": 50,
                            "runtime_backend": "managed",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "LOCAL_NOTE_STUDIO_PROFILES_FILE": str(self.profile_path),
                "LOCAL_NOTE_STUDIO_STATE_DIR": str(self.state),
                "INDEX_DIR": str(self.indexes),
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.temporary.cleanup)
        self._write_fixtures()
        self.profile = profiles.require_profile("fixture")
        self.rebuild = retrieval.rebuild_profile_index(self.profile)
        self.payload = json.loads(pathlib.Path(self.rebuild["index_path"]).read_text(encoding="utf-8"))

    def _write(self, name: str, text: str) -> pathlib.Path:
        path = self.notes / name
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path

    def _write_fixtures(self):
        dynamic = self._write(
            "BILI-OPUS-七轨布林_1215000000000000001.md",
            frontmatter(
                title="七轨布林",
                type="source-note",
                source_type="bilibili-opus",
                source_url="https://www.bilibili.com/opus/1215000000000000001?token=must-not-index",
                author="青枫",
                author_mid="1420210197",
                published="2026-07-20T09:00:00+08:00",
                status="organized",
                model="draft-model",
                organize_model="qwen-organizer",
                source_hash="dynamic-hash",
                tags=["方法论", "证券/600000"],
            )
            + """

# 七轨布林

## 来源追溯

- 动态 ID：1215000000000000001

## Qwen 整理

### 核心观点

七轨布林是一套筛选框架，整理内容不能冒充原话。

## 原文抽取

UP 主原文明确写到七轨布林与 600000。

## 图片分析

图片 OCR 显示七轨布林示意图。
""",
        )
        self._write(
            "duplicate-draft.md",
            frontmatter(
                title="七轨布林草稿",
                source_type="bilibili-opus",
                source_url="https://www.bilibili.com/opus/1215000000000000001",
                status="draft",
                source_hash="dynamic-hash",
            )
            + "\n\n# 草稿\n\n## 原文抽取\n\n重复草稿。",
        )
        self._write(
            "BILI-video.md",
            frontmatter(
                title="公司代码观察",
                type="video-note",
                source_type="bilibili-video",
                source_url="https://www.bilibili.com/video/BVURL9999999",
                bvid="BVFRONT12345",
                author="青枫",
                published="2026-07-19T10:30:00+08:00",
                model="qwen-video",
                status="organized",
                source_hash="video-hash",
            )
            + """

# 公司代码观察

## Qwen 整理

### 核心观点

600000 的结构化整理。

## 原始字幕

字幕原话提到了 600000 与风险纪律。
""",
        )
        self._write(
            "BILI-BVFILE12345.md",
            frontmatter(title="文件名视频", source_type="video", status="organized", source_hash="filename-video-hash")
            + "\n\n# 文件名视频\n\n## 完整转写\n\n文件名回退。",
        )
        self._write(
            "BILI-OPUS-filename_998877665544332211.md",
            frontmatter(title="文件名动态", source_type="bilibili-opus", status="organized", source_hash="filename-opus-hash")
            + "\n\n# 文件名动态\n\n## 原文抽取\n\n动态 ID 文件名回退。",
        )
        self.legacy = self._write(
            "legacy-note.md",
            "# 旧笔记\n\n## 普通章节\n\n没有 frontmatter，也不猜发布时间、模型或来源 ID。",
        )
        self._write(
            "future.md",
            frontmatter(
                title="未来观点",
                source_type="bilibili-opus",
                source_url="https://www.bilibili.com/opus/999999999999999999",
                author="青枫",
                published="2026-07-22T09:00:00+08:00",
                status="organized",
                model="qwen-future",
                source_hash="future-hash",
            )
            + "\n\n# 未来观点\n\n## Qwen 整理\n\n七轨布林未来证据不得泄漏。\n\n## 原文抽取\n\n未来原文。",
        )
        self._write(
            "methodology.md",
            frontmatter(
                title="长期方法论",
                source_type="webpage",
                source_url="https://example.com/methodology",
                author="青枫",
                published="2025-01-01",
                status="organized",
                model="qwen-old",
                source_hash="method-hash",
            )
            + "\n\n# 长期方法论\n\n## 原文抽取\n\n七轨布林方法论与纪律框架长期使用。",
        )
        self._write(
            "PPTX-sample.md",
            frontmatter(
                title="PPT 样例",
                source_type="pptx",
                source_path="source/sample.pptx",
                status="organized",
                model="qwen-ppt",
                source_hash="ppt-hash",
            )
            + "\n\n# PPT 样例\n\n## 原文抽取\n\n### Slide 1\n\n演示材料。",
        )
        manifest_note = self._write(
            "manifest-enriched.md",
            frontmatter(title="Manifest 元数据", source_type="webpage", status="organized")
            + "\n\n# Manifest 元数据\n\n## 原文抽取\n\n来自既有 Manifest。",
        )
        (self.indexes / "source-manifest.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "organized_output_path": str(manifest_note),
                            "source_url": "https://example.com/manifest",
                            "author": "Manifest 作者",
                            "published": "2026-07-18",
                            "organize_model": "manifest-model",
                            "source_hash": "manifest-hash",
                            "organized_status": "organized",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def _search(self, **arguments):
        return retrieval.run_read_action("search", arguments, caller="mcp")

    def test_versioned_index_fields_fallbacks_and_deduplication(self):
        self.assertEqual(retrieval.classify_heading("Qwen 整理"), "llm_organized")
        self.assertEqual(retrieval.classify_heading("校对正文"), "transcript")
        self.assertEqual(retrieval.classify_heading("OCR 分析"), "llm_visual_analysis")
        self.assertEqual(self.payload["schema_version"], "2.0")
        self.assertEqual(self.payload["source_note_count"], 10)
        self.assertEqual(self.payload["deduplicated_count"], 1)
        required = {
            "note_id", "profile_id", "note_path", "source_path", "source_url", "title", "author",
            "author_mid", "published", "date_source", "type", "source_type", "content_type", "bvid",
            "avid", "dynamic_id", "opus_id", "model", "organize_model", "organized_status", "status",
            "source_hash", "tags", "headings", "sections", "modified_at", "size", "missing_metadata",
            "metadata_quality",
        }
        self.assertTrue(all(required <= set(item) for item in self.payload["notes"]))
        dynamic = next(item for item in self.payload["notes"] if item["title"] == "七轨布林")
        self.assertEqual(dynamic["dynamic_id"], "1215000000000000001")
        self.assertEqual(dynamic["organize_model"], "qwen-organizer")
        self.assertNotIn("token=", dynamic["source_url"])
        video = next(item for item in self.payload["notes"] if item["title"] == "公司代码观察")
        self.assertEqual(video["bvid"], "BVFRONT12345")
        self.assertEqual(next(item for item in self.payload["notes"] if item["title"] == "文件名视频")["bvid"], "BVFILE12345")
        self.assertEqual(next(item for item in self.payload["notes"] if item["title"] == "文件名动态")["dynamic_id"], "998877665544332211")
        legacy = next(item for item in self.payload["notes"] if item["title"] == "旧笔记")
        self.assertIsNone(legacy["published"])
        self.assertIsNone(legacy["model"])
        self.assertIsNone(legacy["bvid"])
        self.assertIn("published", legacy["missing_metadata"])
        ppt = next(item for item in self.payload["notes"] if item["title"] == "PPT 样例")
        self.assertEqual(ppt["content_type"], "pptx")
        self.assertIsNone(ppt["bvid"])
        enriched = next(item for item in self.payload["notes"] if item["title"] == "Manifest 元数据")
        self.assertEqual(enriched["author"], "Manifest 作者")
        self.assertEqual(enriched["organize_model"], "manifest-model")

    def test_search_filters_exact_ids_provenance_and_stable_sort(self):
        title = self._search(query="七轨布林", limit=20)
        self.assertEqual(title["status"], "completed")
        self.assertEqual(title["results"][0]["title"], "七轨布林")
        self.assertGreater(title["results"][0]["score"], title["results"][1]["score"])
        provenances = {snippet["provenance"] for snippet in title["results"][0]["snippets"]}
        self.assertIn("llm_organized", provenances)
        self.assertIn("source_text", provenances)
        self.assertTrue(all(snippet["provenance"] for item in title["results"] for snippet in item["snippets"]))
        by_author = self._search(query="600000", author="青枫", date_range={"start": "2026-07-19", "end": "2026-07-20"}, limit=20)
        self.assertEqual({item["title"] for item in by_author["results"]}, {"七轨布林", "公司代码观察"})
        self.assertEqual(self._search(query="BVFRONT12345")["results"][0]["title"], "公司代码观察")
        self.assertEqual(self._search(query="1215000000000000001")["results"][0]["title"], "七轨布林")
        first = [item["note_path"] for item in self._search(query="600000")["results"]]
        second = [item["note_path"] for item in self._search(query="600000")["results"]]
        self.assertEqual(first, second)

    def test_recent_filters_and_get_pagination(self):
        recent = retrieval.run_read_action(
            "list-recent", {"author": "青枫", "content_type": "bilibili-opus", "days": 365, "limit": 20}
        )
        self.assertEqual(recent["status"], "completed")
        self.assertTrue(all(item["author"] == "青枫" for item in recent["results"]))
        self.assertTrue(all(item["content_type"] == "bilibili-opus" for item in recent["results"]))
        self.assertNotIn("旧笔记", {item["title"] for item in recent["results"]})
        dynamic = next(item for item in self.payload["notes"] if item["title"] == "七轨布林")
        result = retrieval.run_read_action("get", {"path_or_id": dynamic["note_id"], "section": "source_text", "max_chars": 30})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["note"]["selected_section"], "source_text")
        self.assertTrue(result["note"]["truncated"])
        self.assertIsNotNone(result["note"]["next_offset"])
        self.assertGreaterEqual(result["note"]["line_end"], result["note"]["line_start"])

    def test_viewpoints_exclude_future_and_keep_methodology_separate(self):
        result = retrieval.run_read_action(
            "get-viewpoints", {"symbol_or_topic": "七轨布林", "as_of_date": "2026-07-20", "limit": 20}
        )
        self.assertEqual(result["status"], "completed")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("未来观点", serialized)
        self.assertGreater(result["details"]["future_evidence_excluded"], 0)
        methodology = result["groups"]["methodology_candidates"]
        self.assertTrue(methodology)
        self.assertTrue(any("does not imply invalidity" in item["classification_basis"] or "age does not imply invalidity" in item["classification_basis"] for item in methodology))
        self.assertIn("deterministic evidence grouping only", result["details"]["service_behavior"])

    def test_invalid_inputs_paths_stale_corrupt_and_empty_results(self):
        self.assertEqual(self._search(query="完全不存在的主题")["status"], "completed")
        self.assertEqual(self._search(query="完全不存在的主题")["results"], [])
        self.assertEqual(self._search(query="")["error"]["error_code"], "INVALID_QUERY")
        self.assertEqual(self._search(query="x", limit=51)["error"]["error_code"], "INPUT_LIMIT_EXCEEDED")
        self.assertEqual(self._search(query="x", date_range={"start": "bad"})["error"]["error_code"], "INVALID_DATE_RANGE")
        self.assertEqual(retrieval.run_read_action("get", {"path_or_id": "../secret.md"})["error"]["error_code"], "NOTE_PATH_NOT_ALLOWED")
        self.assertEqual(retrieval.run_read_action("get", {"path_or_id": "not-indexed.md"})["error"]["error_code"], "NOTE_NOT_FOUND")
        outside = self.root / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        self.assertEqual(retrieval.run_read_action("get", {"path_or_id": str(outside)})["error"]["error_code"], "NOTE_PATH_NOT_ALLOWED")
        index_path = pathlib.Path(self.rebuild["index_path"])
        index_before = index_path.read_bytes()
        self.legacy.write_text(self.legacy.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        stale = self._search(query="旧笔记")
        self.assertEqual(stale["error"]["error_code"], "NOTE_INDEX_STALE")
        self.assertEqual(index_path.read_bytes(), index_before)
        retrieval.rebuild_profile_index(self.profile)
        index_path.write_text("{invalid", encoding="utf-8")
        self.assertEqual(self._search(query="旧笔记")["error"]["error_code"], "NOTE_INDEX_CORRUPT")

    def test_queries_do_not_write_notes_manifest_history_lock_or_index(self):
        tracked = [*self.notes.glob("*.md"), self.indexes / "source-manifest.json", pathlib.Path(self.rebuild["index_path"]), pathlib.Path(self.rebuild["asset_index_path"])]

        def signature(path: pathlib.Path):
            return path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()

        before = {path: signature(path) for path in tracked}
        with mock.patch.object(retrieval, "rebuild_profile_index", side_effect=AssertionError("query must not rebuild")):
            self._search(query="七轨布林")
            retrieval.run_read_action("list-recent", {"days": 365})
            dynamic = next(item for item in self.payload["notes"] if item["title"] == "七轨布林")
            retrieval.run_read_action("get", {"path_or_id": dynamic["note_id"]})
            retrieval.run_read_action("get-viewpoints", {"symbol_or_topic": "七轨布林", "as_of_date": "2026-07-20"})
            with mock.patch.object(mcp, "run_agent_action", side_effect=AssertionError("read tool must not invoke Worker")):
                called = mcp.call_tool("local_notes_search", {"query": "七轨布林", "limit": 1})
                self.assertFalse(called["isError"])
        self.assertEqual(before, {path: signature(path) for path in tracked})
        self.assertFalse((self.state / "automation-history.sqlite3").exists())
        self.assertFalse((self.state / "global-task.lock").exists())
        self.assertFalse((self.state / "global-task.json").exists())

    def test_mcp_has_eleven_tools_strict_schemas_and_clean_stdout(self):
        tools = mcp.tool_definitions()
        self.assertEqual(len(tools), 11)
        new_names = {"local_notes_search", "local_notes_get", "local_notes_list_recent", "local_notes_get_viewpoints"}
        for tool in tools:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            if tool["name"] in new_names:
                self.assertEqual(
                    tool["annotations"],
                    {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
                )
        messages = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "local_notes_search", "arguments": {"query": "七轨布林", "limit": 1}}}),
            ]
        )
        completed = subprocess.run(
            [sys.executable, str(WORKER_DIR / "local_notes_mcp.py")],
            input=messages + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},
            check=True,
            timeout=10,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(len(responses[0]["result"]["tools"]), 11)
        self.assertEqual(responses[1]["result"]["structuredContent"]["status"], "completed")

    def test_explicit_cli_rebuild_and_post_ingest_refresh_are_separate_from_queries(self):
        completed = subprocess.run(
            [sys.executable, str(WORKER_DIR / "local_notes_agent.py"), "rebuild-index", "--profile", "fixture"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},
            check=True,
            timeout=10,
        )
        cli = json.loads(completed.stdout)
        self.assertEqual(cli["task"], "rebuild-index")
        self.assertEqual(cli["status"], "completed")
        worker_result = {
            "schema_version": "1.0",
            "run_id": "fixture",
            "caller": "agent",
            "task": "bilibili-up-sync",
            "status": "completed",
            "started_at": "2026-07-22T00:00:00+00:00",
            "finished_at": "2026-07-22T00:00:01+00:00",
            "source_ref": "",
            "output_dir": str(self.notes),
            "counts": {"discovered": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0},
            "outputs": [],
            "deliveries": [],
            "manifest_path": "",
            "warnings": [],
            "details": {},
            "error": None,
            "retryable": False,
        }
        with mock.patch.object(agent, "invoke_worker", return_value=worker_result.copy()), mock.patch.object(
            agent, "rebuild_profile_index", return_value={"note_count": 9}
        ) as refresh:
            result = agent.run_agent_action("sync-up", profile_id="fixture")
        refresh.assert_called_once()
        self.assertEqual(result["details"]["note_index"]["note_count"], 9)
        with mock.patch.object(agent, "invoke_worker", return_value=worker_result.copy()), mock.patch.object(
            agent, "rebuild_profile_index", side_effect=OSError("private path must not leak")
        ):
            result = agent.run_agent_action("sync-up", profile_id="fixture")
        self.assertEqual(result["status"], "completed")
        self.assertIn("NOTE_INDEX_REFRESH_FAILED", result["warnings"][-1])
        self.assertNotIn("private path", result["warnings"][-1])

    def test_all_enabled_profiles_warn_on_missing_index_and_deduplicate_shared_source(self):
        second_notes = self.root / "notes-two"
        second_notes.mkdir()
        original = self.notes / "BILI-OPUS-七轨布林_1215000000000000001.md"
        (second_notes / original.name).write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
        payload = json.loads(self.profile_path.read_text(encoding="utf-8"))
        second = dict(payload["profiles"][0])
        second.update(id="fixture2", output_dir=str(second_notes))
        payload["profiles"].append(second)
        self.profile_path.write_text(json.dumps(payload), encoding="utf-8")
        partial = self._search(query="七轨布林")
        self.assertEqual(partial["status"], "completed")
        self.assertTrue(any("NOTE_INDEX_UNAVAILABLE" in warning for warning in partial["warnings"]))
        retrieval.rebuild_profile_index(profiles.require_profile("fixture2"))
        complete = self._search(query="七轨布林")
        self.assertEqual(complete["details"]["total_matches"], partial["details"]["total_matches"])
        note_id = complete["results"][0]["note_id"]
        self.assertEqual(retrieval.run_read_action("get", {"path_or_id": note_id})["status"], "completed")


if __name__ == "__main__":
    unittest.main()
