from __future__ import annotations

import importlib.util
import io
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
converter = load_module("convert_sources_to_md_test", ROOT / "worker" / "scripts" / "convert_sources_to_md.py")
quickread = load_module("quick_read_pdf_test", ROOT / "worker" / "scripts" / "quick_read_pdf.py")
organizer = load_module("qwen_organize_notes_test", ROOT / "worker" / "scripts" / "qwen_organize_notes.py")
keyframes = sys.modules["video_keyframes"]


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

    def test_explicit_conda_executable_is_used_by_worker_commands(self):
        req = worker.TaskRequest.from_mapping({
            "runtime_backend": "conda",
            "task": "source-file",
            "conda_env": "course-whisper",
            "conda_bin": "/Users/tester/miniforge3/bin/conda",
        })
        self.assertEqual(req.conda_bin, "/Users/tester/miniforge3/bin/conda")
        self.assertEqual(worker.conda_cmd(req), req.conda_bin)
        self.assertEqual(worker.python_eval_cmd(req, "print('ok')")[0], req.conda_bin)
        self.assertEqual(worker.build_env(req)["CONDA_EXE"], req.conda_bin)

    def test_managed_runtime_clears_legacy_conda_environment(self):
        req = worker.TaskRequest(task="bilibili-url", runtime_backend="managed")
        with mock.patch.object(worker, "load_env_file", return_value={"CONDA_ENV": "course-whisper", "CONDA_EXE": "/tmp/conda"}):
            env = worker.build_env(req)
        self.assertEqual(env["CONDA_ENV"], "")
        self.assertNotIn("CONDA_EXE", env)

    def test_managed_runtime_uses_app_data_for_cache_and_default_asr_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_data = pathlib.Path(temp_dir) / "app"
            model = app_data / "models" / worker.MANAGED_ASR_MODEL_NAME
            model.mkdir(parents=True)
            (model / "config.json").write_text("{}", encoding="utf-8")
            (model / "weights.safetensors").write_text("fixture", encoding="utf-8")
            req = worker.TaskRequest(task="bilibili-url", runtime_backend="managed")
            with mock.patch.dict("os.environ", {"LOCAL_NOTE_STUDIO_APP_DATA_DIR": str(app_data)}):
                env = worker.build_env(req)
        self.assertEqual(env["CACHE_DIR"], str(app_data / "cache" / "audio"))
        self.assertEqual(env["MODEL_CACHE_DIR"], str(app_data / "models"))
        self.assertEqual(env["ASR_ENGINE"], "whisper")
        self.assertEqual(env["ASR_LOCAL_MODEL"], str(model))

    def test_managed_runtime_does_not_leak_legacy_asr_model_from_env_file(self):
        req = worker.TaskRequest(task="bilibili-url", runtime_backend="managed")
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict("os.environ", {"LOCAL_NOTE_STUDIO_APP_DATA_DIR": temp_dir}):
                with mock.patch.object(worker, "load_env_file", return_value={"ASR_LOCAL_MODEL": "/old/model", "ASR_ENGINE": "qwen3"}):
                    env = worker.build_env(req)
        self.assertEqual(env["ASR_ENGINE"], "whisper")
        self.assertNotIn("ASR_LOCAL_MODEL", env)

    def test_forced_asr_requires_model_before_downloading_audio(self):
        req = worker.TaskRequest(
            task="bilibili-url",
            runtime_backend="managed",
            subtitle_strategy="asr",
            source="https://www.bilibili.com/video/BV1111111111",
            output_dir="/tmp/local-note-output",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict("os.environ", {"LOCAL_NOTE_STUDIO_APP_DATA_DIR": temp_dir}):
                with self.assertRaisesRegex(ValueError, "ASR 模型目录"):
                    worker.command_for(req)

    def test_bilibili_runner_preserves_conda_only_when_explicit(self):
        with mock.patch.object(runner, "load_env_file", return_value={"CONDA_ENV": "course-whisper"}):
            with mock.patch.dict("os.environ", {"CONDA_ENV": ""}, clear=True):
                managed_cfg = runner.config()
        self.assertEqual(managed_cfg["CONDA_ENV"], "")
        self.assertEqual(runner.bash_command(managed_cfg, pathlib.Path("script.sh"), "url")[0], "bash")
        self.assertEqual(runner.project_env(managed_cfg)["CONDA_ENV"], "")

        conda_cfg = dict(managed_cfg)
        conda_cfg["CONDA_ENV"] = "course-whisper"
        self.assertEqual(runner.bash_command(conda_cfg, pathlib.Path("script.sh"), "url")[:5], ["conda", "run", "--no-capture-output", "-n", "course-whisper"])

    def test_cookie_refresh_rejects_broad_profile_and_uses_app_data_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_data = pathlib.Path(temp_dir) / "app-data"
            chrome_root = pathlib.Path(temp_dir) / "Chrome"
            profile = chrome_root / "Default"
            (profile / "Network").mkdir(parents=True)
            (profile / "Network" / "Cookies").touch()

            self.assertEqual(worker.validate_chromium_profile_path(str(profile)), profile.resolve())
            with self.assertRaisesRegex(ValueError, "不是具体的 Chrome Profile"):
                worker.validate_chromium_profile_path(str(chrome_root))

            with mock.patch.dict("os.environ", {"LOCAL_NOTE_STUDIO_APP_DATA_DIR": str(app_data)}):
                req = worker.TaskRequest(task="refresh-bilibili-cookies", runtime_backend="managed", browser_profile=str(profile))
                command = worker.command_for(req)
                expected_output = app_data / "auth" / "bili_cookies.txt"
                self.assertEqual(command[0], sys.executable)
                self.assertEqual(command[command.index("--output") + 1], str(expected_output))
                self.assertEqual(worker.cookie_output_path("./bili_cookies.txt"), expected_output)

                expected_output.parent.mkdir(parents=True)
                expected_output.touch()
                env = worker.build_env(req)
                self.assertEqual(env["BILIBILI_COOKIES_FILE"], str(expected_output))

    def test_managed_environment_check_points_to_install_repair_for_managed_components(self):
        req = worker.TaskRequest(task="env-check", runtime_backend="managed", api_base="http://127.0.0.1:1234/v1", api_key="x", model="qwen")

        def fake_probe(command, env, timeout=15):
            joined = " ".join(command)
            if "pandoc" in joined or "mlx_whisper" in joined:
                return False, "missing"
            if "ffmpeg" in joined or "ffprobe" in joined or "yt-dlp" in joined:
                return True, "version"
            if "sys.version_info" in joined or "pypdf" in joined or "lxml" in joined or "requests" in joined:
                return True, "import ok"
            return True, "ok"

        with mock.patch.object(worker, "probe", side_effect=fake_probe):
            result = worker.check_environment(req, {})

        self.assertIn("[MISSING] Managed command `pandoc`", result)
        self.assertIn("[MISSING] Managed Python package `mlx_whisper`", result)
        self.assertIn("点击“安装/修复”补齐托管环境组件", result)
        self.assertNotIn("brew install pandoc", result)
        self.assertNotIn("python3 -m pip install", result)

    def test_bilibili_access_uses_default_app_cookie_after_refresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_path = pathlib.Path(temp_dir) / "bili_cookies.txt"
            cookie_path.write_text(
                "# Netscape HTTP Cookie File\n"
                ".bilibili.com\tTRUE\t/\tFALSE\t2147483647\tSESSDATA\tfixture\n"
                ".bilibili.com\tTRUE\t/\tFALSE\t2147483647\tDedeUserID\t42\n",
                encoding="utf-8",
            )
            response = mock.Mock()
            response.__enter__ = mock.Mock(return_value=response)
            response.__exit__ = mock.Mock(return_value=None)
            response.read.return_value = b'{"code":0,"data":{"isLogin":true,"mid":42,"uname":"tester"}}'
            opener = mock.Mock()
            opener.open.return_value = response
            req = worker.TaskRequest(task="bilibili-access-check", runtime_backend="managed")

            with mock.patch.object(worker, "default_bilibili_cookie_path", return_value=cookie_path):
                with mock.patch.object(worker.urllib.request, "build_opener", return_value=opener):
                    login = worker.bilibili_login_data(req)

            self.assertEqual(login["mid"], 42)
            request = opener.open.call_args.args[0]
            self.assertIn("nav", request.full_url)

    def test_empty_output_snapshot_never_scans_the_current_directory(self):
        with mock.patch.object(pathlib.Path, "rglob", side_effect=AssertionError("unexpected directory scan")):
            self.assertEqual(worker.output_snapshot(""), {})
            self.assertEqual(worker.output_snapshot("   "), {})

    def test_cookie_refresh_bypasses_output_snapshot_pipeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = pathlib.Path(temp_dir) / "Default"
            (profile / "Network").mkdir(parents=True)
            (profile / "Network" / "Cookies").touch()
            request = json.dumps({"task": "refresh-bilibili-cookies", "browser_profile": str(profile)})
            with mock.patch.object(worker, "run_command", return_value="refreshed\n"):
                with mock.patch.object(worker, "output_snapshot", side_effect=AssertionError("unexpected output scan")):
                    with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                        self.assertEqual(worker.main(["--request-json", request]), 0)
                        self.assertEqual(stdout.getvalue(), "refreshed\n")

    def test_p1_request_mapping_and_task_overrides(self):
        req = worker.TaskRequest.from_mapping({
            "task": "web-url", "web_capture_mode": "browser", "browser_executable": "/Applications/Chrome",
            "timeout_seconds": "600", "retry_count": "3", "cooldown_delay": "12", "chunk_chars": "24000",
            "ocr_resume": False,
        })
        env = worker.build_env(req)
        self.assertEqual(req.web_capture_mode, "browser")
        self.assertEqual(env["WEB_CAPTURE_MODE"], "browser")
        self.assertEqual(env["QWEN_ORGANIZE_TIMEOUT_SECONDS"], "600")
        self.assertEqual(env["QWEN_ORGANIZE_MAX_RETRIES"], "3")
        self.assertEqual(env["COOLDOWN_DELAY"], "12")
        self.assertEqual(env["QWEN_ORGANIZE_COOLDOWN_DELAY"], "12")
        self.assertEqual(env["QWEN_PDF_POLISH_COOLDOWN_DELAY"], "12")
        self.assertEqual(env["QWEN_QUICKREAD_COOLDOWN_DELAY"], "12")
        self.assertEqual(env["SUMMARY_CHUNK_COOLDOWN_DELAY"], "12")
        with mock.patch.dict("os.environ", env):
            self.assertEqual(organizer.config()["QWEN_ORGANIZE_COOLDOWN_DELAY"], "12")
        self.assertEqual(env["QWEN_ORGANIZE_MAX_CHARS"], "24000")
        self.assertEqual(env["OCR_RESUME"], "false")

    def test_explicit_zero_disables_all_model_cooldowns(self):
        req = worker.TaskRequest.from_mapping({"task": "bilibili-up-opus", "cooldown_delay": "0"})
        env = worker.build_env(req)
        self.assertEqual(req.cooldown_delay, 0)
        for key in (
            "COOLDOWN_DELAY",
            "QWEN_ORGANIZE_COOLDOWN_DELAY",
            "QWEN_PDF_POLISH_COOLDOWN_DELAY",
            "QWEN_QUICKREAD_COOLDOWN_DELAY",
            "SUMMARY_CHUNK_COOLDOWN_DELAY",
        ):
            self.assertEqual(env[key], "0")
        self.assertEqual(worker.TaskRequest.from_mapping({"task": "bilibili-up-opus"}).cooldown_delay, -1)

    def test_incognito_request_disables_all_manifest_state_flags(self):
        req = worker.TaskRequest.from_mapping({"task": "source-file", "incognito_mode": True})
        env = worker.build_env(req)
        self.assertTrue(req.incognito_mode)
        self.assertEqual(env["LOCAL_NOTE_STUDIO_INCOGNITO"], "true")
        self.assertEqual(env["VIDEO_MANIFEST_ENABLED"], "false")
        self.assertEqual(env["BILIBILI_INCREMENTAL_STATE_ENABLED"], "false")
        self.assertEqual(env["KEYFRAME_MANIFEST_ENABLED"], "false")

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

    def test_manifest_status_classifies_processed_failed_and_rebuild(self):
        existing = self.root / "ready.md"
        existing.write_text("# ready\n", encoding="utf-8")
        manifest = {
            "items": [
                {"source": "ok", "output_path": str(existing), "status": "converted"},
                {"source": "bad", "error": "boom", "status": "failed"},
                {"source": "missing", "output_path": str(self.root / "missing.md"), "status": "converted"},
            ]
        }
        (self.root / "source-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        text = worker.manifest_status(
            worker.TaskRequest(task="manifest-status", source=str(self.root)),
            {"INDEX_DIR": str(self.root)},
        )
        data = json.loads(text.removeprefix("MANIFEST_STATUS_JSON:"))
        self.assertEqual(data["totals"], {"processed": 1, "skipped": 0, "failed": 1, "rebuild": 1})
        self.assertEqual(data["manifests"][0]["items"][1]["record_index"], 1)
        self.assertEqual(data["manifests"][0]["items"][1]["record_kind"], "manifest-json")

    def test_manifest_record_status_can_be_overridden_restored_and_deleted(self):
        manifest_path = self.root / "source-manifest.json"
        manifest_path.write_text(
            json.dumps({"items": [{"source": "bad", "error": "boom", "status": "failed"}, {"source": "old"}]}),
            encoding="utf-8",
        )
        base = dict(task="manifest-update", source=str(self.root), manifest_path=str(manifest_path), manifest_kind="manifest-json", manifest_index=0)
        worker.update_manifest_record(
            worker.TaskRequest(**base, manifest_action="set-status", manifest_status="processed"),
            {"INDEX_DIR": str(self.root)},
        )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["items"][0]["manual_status"], "processed")
        self.assertEqual(worker._manifest_item_status(payload["items"][0]), "processed")

        worker.update_manifest_record(
            worker.TaskRequest(**base, manifest_action="set-status", manifest_status="auto"),
            {"INDEX_DIR": str(self.root)},
        )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertNotIn("manual_status", payload["items"][0])
        self.assertEqual(worker._manifest_item_status(payload["items"][0]), "failed")

        worker.update_manifest_record(
            worker.TaskRequest(**base, manifest_action="delete"),
            {"INDEX_DIR": str(self.root)},
        )
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["items"], [{"source": "old"}])

    def test_manifest_records_support_atomic_batch_status_and_delete(self):
        manifest_path = self.root / "video-manifest.json"
        manifest_path.write_text(
            json.dumps({"items": [{"source": "one"}, {"source": "two"}, {"source": "three"}]}),
            encoding="utf-8",
        )
        base = dict(
            task="manifest-update",
            source=str(self.root),
            manifest_path=str(manifest_path),
            manifest_kind="manifest-json",
            manifest_indexes=(0, 2),
        )
        result = worker.update_manifest_record(
            worker.TaskRequest(**base, manifest_action="set-status", manifest_status="failed"),
            {"INDEX_DIR": str(self.root)},
        )
        self.assertEqual(json.loads(result.removeprefix("MANIFEST_UPDATE_JSON:"))["count"], 2)
        items = json.loads(manifest_path.read_text(encoding="utf-8"))["items"]
        self.assertEqual([item.get("manual_status") for item in items], ["failed", None, "failed"])

        worker.update_manifest_record(
            worker.TaskRequest(**base, manifest_action="delete"),
            {"INDEX_DIR": str(self.root)},
        )
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["items"], [{"source": "two"}])

    def test_request_mapping_accepts_manifest_batch_indexes(self):
        request = worker.TaskRequest.from_mapping({"task": "manifest-update", "manifest_indexes": ["3", 1, "bad", -1, 3]})
        self.assertEqual(request.manifest_indexes, (3, 1, 3))

    def test_processed_state_record_can_be_deleted_but_not_relabelled(self):
        state_path = self.root / "processed_videos.txt"
        state_path.write_text("BV1\nBV2\n", encoding="utf-8")
        base = dict(
            task="manifest-update",
            source=str(self.root),
            manifest_path=str(state_path),
            manifest_kind="processed-text",
            manifest_index=0,
        )
        worker.update_manifest_record(worker.TaskRequest(**base, manifest_action="delete"), {"INDEX_DIR": str(self.root)})
        self.assertEqual(state_path.read_text(encoding="utf-8"), "BV2\n")
        with self.assertRaisesRegex(ValueError, "只支持删除"):
            worker.update_manifest_record(
                worker.TaskRequest(**base, manifest_action="set-status", manifest_status="failed"),
                {"INDEX_DIR": str(self.root)},
            )

    def test_manifest_update_rejects_file_outside_allowed_roots(self):
        outside = pathlib.Path(tempfile.mkdtemp()) / "source-manifest.json"
        self.addCleanup(lambda: shutil.rmtree(outside.parent, ignore_errors=True))
        outside.write_text(json.dumps({"items": [{}]}), encoding="utf-8")
        req = worker.TaskRequest(
            task="manifest-update",
            source=str(self.root),
            manifest_path=str(outside),
            manifest_kind="manifest-json",
            manifest_index=0,
            manifest_action="delete",
        )
        with self.assertRaisesRegex(ValueError, "拒绝修改"):
            worker.update_manifest_record(req, {"INDEX_DIR": str(self.root)})

    def test_manifest_prefers_organized_note_over_deleted_staging_draft(self):
        organized = self.root / "organized.md"
        organized.write_text("# organized\n", encoding="utf-8")
        item = {
            "status": "converted",
            "output_path": "/tmp/local-note-studio-drafts-old/draft.md",
            "organized_status": "organized",
            "organized_output_path": str(organized),
            "error": "",
            "organize_error": "",
        }
        status, output, reason = worker._manifest_item_detail(item)
        self.assertEqual((status, output, reason), ("processed", str(organized), ""))
        organized.unlink()
        status, _, reason = worker._manifest_item_detail(item)
        self.assertEqual(status, "rebuild")
        self.assertIn("正式笔记", reason)

    def test_ocr_checkpoint_round_trip(self):
        source = self.root / "scan.pdf"
        source.write_bytes(b"fixture")
        checkpoint_dir = self.root / "ocr-state"
        with mock.patch.dict("os.environ", {"OCR_CHECKPOINT_DIR": str(checkpoint_dir), "OCR_RESUME": "true"}):
            converter.save_ocr_checkpoint(source, "Fixture", ["page one", None])
            self.assertEqual(converter.load_ocr_checkpoint(source, "Fixture", 2), ["page one", None])

    def test_incognito_source_conversion_does_not_read_or_write_manifest(self):
        source = self.root / "fixture.csv"
        source.write_text("name,value\nalpha,1\n", encoding="utf-8")
        output = self.root / "output"
        index = self.root / "index"
        index.mkdir()
        manifest = index / "source-manifest.json"
        sentinel = "not-json-and-must-stay-untouched\n"
        manifest.write_text(sentinel, encoding="utf-8")
        with (
            mock.patch.dict("os.environ", {"LOCAL_NOTE_STUDIO_INCOGNITO": "true", "INDEX_DIR": str(index)}),
            mock.patch.object(sys, "argv", ["convert_sources_to_md.py", "--source", str(source), "--output-dir", str(output)]),
        ):
            self.assertEqual(converter.main(), 0)
        self.assertEqual(manifest.read_text(encoding="utf-8"), sentinel)
        self.assertTrue(list(output.glob("*.md")))

    def test_incognito_quickread_does_not_read_or_write_manifest(self):
        source = self.root / "paper.pdf"
        source.write_bytes(b"fixture")
        output = self.root / "quickread"
        index = self.root / "quickread-index"
        index.mkdir()
        manifest = index / "quickread-manifest.json"
        sentinel = "not-json-and-must-stay-untouched\n"
        manifest.write_text(sentinel, encoding="utf-8")
        cfg = {
            **quickread.DEFAULTS,
            "INDEX_DIR": str(index),
            "LOCAL_NOTE_STUDIO_INCOGNITO": "true",
        }
        with mock.patch.object(quickread, "extract_pdf", return_value=("Fixture Paper", 1, "body")):
            path = quickread.write_quickread(source, output, cfg, overwrite=True, prompt_only=True)
        self.assertTrue(path.exists())
        self.assertEqual(manifest.read_text(encoding="utf-8"), sentinel)

    def test_semantic_keyframe_selection_and_visual_filter(self):
        markdown = "# 标题\n\n## 核心结论\n\n核心结论是增长来自效率提升，因此需要关注风险和关键指标。\n\n## 方法\n\n首先比较方案，然后验证数据，最后给出建议。"
        points = keyframes._structured_semantic_points(markdown, 120.0, 2)
        self.assertTrue(points)
        self.assertTrue(all(0 < point["timestamp"] < 120 for point in points))
        self.assertFalse(keyframes._usable_fingerprint(bytes([0] * 256), []))
        varied = bytes([index % 256 for index in range(256)])
        self.assertTrue(keyframes._usable_fingerprint(varied, []))
        self.assertFalse(keyframes._usable_fingerprint(varied, [varied]))


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

    def test_incognito_collection_does_not_persist_processed_or_failure_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            cfg = {
                "BILIBILI_OUTPUT_DIR": temp,
                "BILIBILI_STATE_DIR": str(root / "state"),
                "BILIBILI_INCREMENTAL_STATE_ENABLED": "false",
            }
            runner.append_processed("123", cfg)
            failure_path = runner.save_batch_failures(
                cfg,
                {"type": "favorite", "id": "1", "mid": "2"},
                [{"bvid": "BV123", "stage": "qwen"}],
            )
            self.assertIsNone(failure_path)
            self.assertFalse((root / "state" / "processed_videos.txt").exists())
            self.assertFalse((root / ".local-note-studio-batch-failures.json").exists())
            with self.assertRaisesRegex(RuntimeError, "隐身模式"):
                runner.load_batch_failures(cfg, {"type": "favorite", "id": "1", "mid": "2"})

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
