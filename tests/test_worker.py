from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "worker" / "local_note_studio_worker.py"
sys.path.insert(0, str(ROOT / "worker" / "scripts"))


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


worker = load_module("local_note_studio_worker_test", WORKER_PATH)
runner = load_module("run_bilibili_transcript_test", ROOT / "worker" / "scripts" / "run_bilibili_transcript.py")


class RequestAndCommandContractTests(unittest.TestCase):
    def base(self, task: str, source: str = "source"):
        return worker.TaskRequest(task=task, source=source, output_dir="/tmp/local-note-output", python_bin="python3")

    def test_request_mapping_preserves_batch_options(self):
        req = worker.TaskRequest.from_mapping({
            "task": "bilibili-favorite", "favorite_limit": "0", "collection_type": "series",
            "collection_id": "42", "collection_mid": "7", "retry_failed": True,
            "keep_original_subtitles": False, "overwrite_outputs": True,
        })
        self.assertEqual((req.favorite_limit, req.collection_type, req.collection_id, req.collection_mid), (0, "series", "42", "7"))
        self.assertTrue(req.retry_failed)
        self.assertFalse(req.keep_original_subtitles)

    def test_major_task_command_contracts(self):
        cases = {
            "bilibili-url": "run_bilibili_transcript.py",
            "local-video": "run_bilibili_transcript.py",
            "web-url": "convert_sources_to_md.py",
            "source-file": "convert_sources_to_md.py",
            "paper-quickread": "quick_read_pdf.py",
            "bilibili-up-opus": "convert_sources_to_md.py",
            "epub-export": "export_epub.py",
        }
        for task, script in cases.items():
            with self.subTest(task=task):
                req = self.base(task)
                command = worker.command_for(req)
                self.assertTrue(any(part.endswith(script) for part in command))
                self.assertIn("/tmp/local-note-output", command if task not in {"bilibili-url", "local-video"} else worker.build_env(req).values())

    def test_collection_command_has_selection_retry_limit_and_overwrite(self):
        req = self.base("bilibili-favorite", "")
        req.collection_type = "series"
        req.collection_id = "88"
        req.collection_mid = "99"
        req.favorite_limit = 0
        req.retry_failed = True
        req.overwrite_outputs = True
        command = worker.command_for(req)
        rendered = worker.render_command(command)
        for token in ("--collection-type series", "--collection-id 88", "--collection-mid 99", "--limit 0", "--retry-failed", "--overwrite"):
            self.assertIn(token, rendered)

    def test_single_output_filename_and_overwrite_flags(self):
        req = self.base("source-file", "/tmp/source.pdf")
        req.output_filename = "stable-name"
        req.overwrite_outputs = True
        command = worker.command_for(req)
        self.assertEqual(command[-3:], ["--overwrite", "--output-filename", "stable-name"])


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self) -> pathlib.Path:
        shutil.copytree(ROOT / "tests" / "fixtures", self.root, dirs_exist_ok=True)
        return self.root / "organized-note.md"

    def test_organized_fixture_passes_source_original_and_image_checks(self):
        path = self.fixture()
        req = worker.TaskRequest(task="web-url", output_dir=str(self.root))
        self.assertEqual(worker.validate_markdown_output(path, req), [])

    def test_missing_image_and_temp_draft_are_rejected(self):
        path = self.fixture()
        text = path.read_text(encoding="utf-8").replace("assets/cover.png", "assets/missing.png")
        path.write_text("draft_path: /tmp/local-note-studio-drafts-x/a.md\n" + text, encoding="utf-8")
        errors = worker.validate_markdown_output(path, worker.TaskRequest(task="web-url"))
        self.assertTrue(any("临时草稿" in item for item in errors))
        self.assertTrue(any("图片相对路径" in item for item in errors))

    def test_video_raw_subtitle_strictly_follows_option(self):
        path = self.root / "video.md"
        path.write_text("---\nsource_url: https://bilibili.com/video/BV1test\n---\n\n# 视频\n\n## 原始字幕\n\n字幕\n", encoding="utf-8")
        keep = worker.TaskRequest(task="bilibili-url", keep_original_subtitles=True)
        remove = worker.TaskRequest(task="bilibili-url", keep_original_subtitles=False)
        self.assertEqual(worker.validate_markdown_output(path, keep), [])
        self.assertTrue(any("仍含原始字幕" in item for item in worker.validate_markdown_output(path, remove)))

    def test_paper_requires_full_translation(self):
        path = self.root / "paper.md"
        path.write_text("---\nsource_path: /tmp/paper.pdf\n---\n# Paper\n", encoding="utf-8")
        errors = worker.validate_markdown_output(path, worker.TaskRequest(task="paper-quickread"))
        self.assertTrue(any("全文翻译" in item for item in errors))


class BatchAndDiagnosticsTests(unittest.TestCase):
    def test_collection_batch_applies_cooldown_only_between_qwen_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            cfg = {
                "BILIBILI_OUTPUT_DIR": temp,
                "BILIBILI_STATE_DIR": str(root / "state"),
                "COOLDOWN_DELAY": "2.5",
                "CONDA_ENV": "",
            }
            videos = [
                {"avid": "1", "bvid": "BV1111111111", "title": "One"},
                {"avid": "2", "bvid": "BV2222222222", "title": "Two"},
            ]
            paths = [str(root / "one.md"), str(root / "two.md")]
            for path in paths:
                pathlib.Path(path).write_text("# fixture\n", encoding="utf-8")
            with (
                mock.patch.object(runner, "project_env", return_value={}),
                mock.patch.object(runner, "python_command", return_value=["python"]),
                mock.patch.object(runner, "bash_command", return_value=["bash"]),
                mock.patch.object(runner, "stream_command", side_effect=[(0, "scan"), (0, "t1"), (0, "q1"), (0, "t2"), (0, "q2")]),
                mock.patch.object(runner, "parse_scanner_output", return_value=videos),
                mock.patch.object(runner, "extract_markdown_paths", side_effect=[[paths[0]], [paths[1]]]),
                mock.patch.object(runner, "postprocess_video_notes"),
                mock.patch.object(runner, "append_processed"),
                mock.patch.object(runner, "wait_for_collection_llm_cooldown") as wait,
            ):
                code = runner.run_collection_batch(ROOT / "worker", cfg, 0, False, "favorite", "9", "", False)
            self.assertEqual(code, 0)
            wait.assert_called_once_with(2.5, 2, 2)

    def test_scanner_output_contract(self):
        text = "\n".join(["COLLECTION_TOTAL:1", "  - AVID:1", "    BVID:BV1234567890", "    TITLE:Fixture", "    DURATION:1分2秒", "    UPPER:Tester"])
        videos = runner.parse_scanner_output(text)
        self.assertEqual(videos[0]["bvid"], "BV1234567890")
        self.assertEqual(videos[0]["title"], "Fixture")

    def test_failure_list_round_trip_and_collection_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = {"BILIBILI_OUTPUT_DIR": temp}
            collection = {"type": "favorite", "id": "1", "mid": "2"}
            failures = [{"bvid": "BV1234567890", "stage": "qwen", "path": "/tmp/a.md"}]
            runner.save_batch_failures(cfg, collection, failures)
            self.assertEqual(runner.load_batch_failures(cfg, collection), failures)
            with self.assertRaises(RuntimeError):
                runner.load_batch_failures(cfg, {"type": "favorite", "id": "other", "mid": "2"})

    def test_restricted_content_categories_are_distinct(self):
        self.assertIn("未登录", worker.bilibili_error_category(-101, ""))
        self.assertIn("权限", worker.bilibili_error_category(-403, ""))
        self.assertIn("412", worker.bilibili_error_category(http_status=412))
        self.assertIn("不存在", worker.bilibili_error_category(-404, ""))

    def test_authorized_charging_video_and_opus_payload_records(self):
        fixtures = ROOT / "tests" / "fixtures"
        video = json.loads((fixtures / "bilibili-video-authorized.json").read_text(encoding="utf-8"))
        opus = json.loads((fixtures / "bilibili-opus-authorized.json").read_text(encoding="utf-8"))
        worker.validate_bilibili_target_payload("video", video)
        worker.validate_bilibili_target_payload("opus", opus)
        blocked = json.loads(json.dumps(opus))
        blocked["data"]["item"]["modules"]["module_dynamic"]["major"]["type"] = "MAJOR_TYPE_BLOCKED"
        with self.assertRaisesRegex(RuntimeError, "充电权限"):
            worker.validate_bilibili_target_payload("opus", blocked)


if __name__ == "__main__":
    unittest.main()
