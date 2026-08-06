from __future__ import annotations

import importlib.util
import datetime as dt
import hashlib
import io
import json
import os
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
batch_transcriber = load_module("batch_transcribe_test", ROOT / "worker" / "scripts" / "bilibili" / "batch_transcribe.py")
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

    def test_up_opus_existing_notes_report_exact_no_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            output_dir = root / "notes"
            output_dir.mkdir()
            state_dir = root / "state"
            req = worker.TaskRequest(
                task="bilibili-up-opus",
                source="https://space.bilibili.com/1420210197",
                output_dir=str(output_dir),
                favorite_limit=5,
            )
            result = worker.TaskResult(
                "existing-opus",
                "gui",
                req.task,
                "completed",
                worker.utc_now(),
                output_dir=req.output_dir,
            )
            call_count = 0

            def fake_run_process(command, _env):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    staging_dir = pathlib.Path(command[command.index("--output-dir") + 1])
                    for index in range(5):
                        (staging_dir / f"draft-{index}.md").write_text(f"# 草稿 {index}\n", encoding="utf-8")
                    return "草稿准备完成：生成 5，源级跳过 0，失败 0；尚未改动正式笔记。\n"
                return (
                    "整理阶段完成：成功 0，跳过 5，失败 0。\n"
                    "[无需更新] 5 篇已有完整笔记保持不变。\n"
                    "[模型调用] 0 次；本批没有新增或更新文件。\n"
                )

            with (
                mock.patch.object(worker, "build_env", return_value={"LOCAL_NOTE_STUDIO_STATE_DIR": str(state_dir)}),
                mock.patch.object(worker, "run_process", side_effect=fake_run_process),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                worker.execute_request(req, result)

            log = stdout.getvalue()
            self.assertEqual(result.status, "no_changes")
            self.assertEqual(result.counts["discovered"], 5)
            self.assertEqual(result.counts["skipped"], 5)
            self.assertEqual(result.counts["created"], 0)
            self.assertEqual(result.counts["updated"], 0)
            self.assertIn("开始完整性比对：共 5 篇；仅对新增或不完整笔记调用 Qwen", log)
            self.assertIn("[临时恢复点] 本批全部处理完成，临时草稿与恢复点已清理。", log)
            self.assertIn("[完整性 OK] 5 篇已有完整结果，无需更新；正式文件保持不变。", log)
            self.assertIn("[任务结果] 无需更新：已检查 5 项，其中 5 项已有完整结果", log)
            self.assertNotIn("完整性 WARN", log)

    def test_up_opus_space_logs_candidates_instead_of_formal_new_notes(self):
        payload = {
            "code": 0,
            "data": {
                "has_more": False,
                "items": [
                    {
                        "id_str": "123",
                        "modules": {"module_dynamic": {"major": {"type": "MAJOR_TYPE_OPUS"}}},
                    },
                    {
                        "id_str": "456",
                        "modules": {"module_dynamic": {"major": {"type": "MAJOR_TYPE_OPUS"}}},
                    },
                ],
            },
        }
        with (
            mock.patch.object(converter, "bilibili_cookie_path", return_value=pathlib.Path("cookies.txt")),
            mock.patch.object(converter, "ensure_bilibili_cookie_login"),
            mock.patch.object(converter, "fetch_json_with_cookies", return_value=payload),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            urls = converter.fetch_bilibili_space_opus_urls("1420210197", {}, limit=2)

        self.assertEqual(
            urls,
            ["https://www.bilibili.com/opus/123", "https://www.bilibili.com/opus/456"],
        )
        self.assertIn("收集到候选图文 2 条，达到处理上限 2 条", stdout.getvalue())
        self.assertNotIn("新增图文", stdout.getvalue())

    def test_up_opus_recovery_cleanup_failure_is_visible_and_non_destructive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            output_dir = root / "notes"
            output_dir.mkdir()
            state_dir = root / "state"
            req = worker.TaskRequest(
                task="bilibili-up-opus",
                source="https://space.bilibili.com/1420210197",
                output_dir=str(output_dir),
            )
            result = worker.TaskResult(
                "cleanup-warning",
                "gui",
                req.task,
                "completed",
                worker.utc_now(),
                output_dir=req.output_dir,
            )
            call_count = 0

            def fake_run_process(command, _env):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    staging_dir = pathlib.Path(command[command.index("--output-dir") + 1])
                    (staging_dir / "draft.md").write_text("# 草稿\n", encoding="utf-8")
                    return "草稿准备完成：生成 1，源级跳过 0，失败 0；尚未改动正式笔记。\n"
                return "整理阶段完成：成功 0，跳过 1，失败 0。\n"

            original_rmtree = shutil.rmtree

            def fail_recovery_cleanup(path, *args, **kwargs):
                if pathlib.Path(path).is_relative_to(state_dir / "recovery"):
                    raise OSError("fixture cleanup denied")
                return original_rmtree(path, *args, **kwargs)

            with (
                mock.patch.object(worker, "build_env", return_value={"LOCAL_NOTE_STUDIO_STATE_DIR": str(state_dir)}),
                mock.patch.object(worker, "run_process", side_effect=fake_run_process),
                mock.patch.object(worker.shutil, "rmtree", side_effect=fail_recovery_cleanup),
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                worker.execute_request(req, result)

            self.assertTrue(any((state_dir / "recovery").iterdir()))
            self.assertIn("恢复点未能自动清理", stderr.getvalue())
            self.assertIn("completed outputs but failed to clean temporary recovery drafts", result.warnings)

    def test_raw_subtitles_default_off_and_can_be_explicitly_enabled(self):
        self.assertFalse(worker.TaskRequest(task="").keep_original_subtitles)
        self.assertFalse(worker.TaskRequest.from_mapping({}).keep_original_subtitles)
        default_args = worker.parse_args(["--task", "bilibili-url"])
        enabled_args = worker.parse_args(["--task", "bilibili-url", "--keep-original-subtitles"])
        disabled_args = worker.parse_args(["--task", "bilibili-url", "--no-keep-original-subtitles"])
        self.assertFalse(worker.request_from_args(default_args).keep_original_subtitles)
        self.assertTrue(worker.request_from_args(enabled_args).keep_original_subtitles)
        self.assertFalse(worker.request_from_args(disabled_args).keep_original_subtitles)
        self.assertEqual(runner.DEFAULTS["KEEP_ORIGINAL_SUBTITLES"], "false")

    def test_summary_only_preserves_raw_subtitles_for_retry_when_llm_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            note = pathlib.Path(temp_dir) / "video.md"
            note.write_text("# 视频\n\n## 原始字幕\n\n不应保留的原始转写\n", encoding="utf-8")
            with (
                mock.patch.object(batch_transcriber, "SUMMARY_API_KEY", "set"),
                mock.patch.object(batch_transcriber, "KEEP_ORIGINAL_SUBTITLES", False),
                mock.patch.object(batch_transcriber, "generate_summary", side_effect=RuntimeError("fixture failure")),
            ):
                result = batch_transcriber.run_summary_only(str(note))
            self.assertEqual(result, 1)
            self.assertIn("## 原始字幕", note.read_text(encoding="utf-8"))

    def test_summary_only_removes_raw_subtitles_after_note_is_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            note = pathlib.Path(temp_dir) / "video.md"
            note.write_text("# 视频\n\n## 校对正文\n\n已完成\n\n## 原始字幕\n\n原始转写\n", encoding="utf-8")
            with (
                mock.patch.object(batch_transcriber, "SUMMARY_API_KEY", "set"),
                mock.patch.object(batch_transcriber, "KEEP_ORIGINAL_SUBTITLES", False),
                mock.patch.object(batch_transcriber, "generate_summary", return_value=False),
            ):
                result = batch_transcriber.run_summary_only(str(note))
            self.assertEqual(result, 0)
            self.assertNotIn("## 原始字幕", note.read_text(encoding="utf-8"))

    def test_summary_only_preserves_raw_only_legacy_note_without_placeholders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            note = pathlib.Path(temp_dir) / "legacy-video.md"
            original = "# 视频\n\n## 原始字幕\n\n唯一可恢复的原始转写\n"
            note.write_text(original, encoding="utf-8")
            with (
                mock.patch.object(batch_transcriber, "SUMMARY_API_KEY", "set"),
                mock.patch.object(batch_transcriber, "KEEP_ORIGINAL_SUBTITLES", False),
                mock.patch.object(batch_transcriber, "generate_summary", return_value=False),
            ):
                result = batch_transcriber.run_summary_only(str(note))
            self.assertEqual(result, 0)
            self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_empty_generated_section_does_not_consume_details_raw_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            note = pathlib.Path(temp_dir) / "legacy-details-video.md"
            original = (
                "# 视频\n\n## 校对正文\n\n---\n\n"
                "<details>\n<summary>📄 原始字幕</summary>\n\n"
                "唯一可恢复的原始转写\n\n## 结构化正文\n\n这只是字幕内容\n\n</details>\n"
            )
            note.write_text(original, encoding="utf-8")
            with mock.patch.object(batch_transcriber, "KEEP_ORIGINAL_SUBTITLES", False):
                removed = batch_transcriber.apply_original_subtitle_preference(str(note))
            self.assertFalse(removed)
            self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_raw_subtitle_removal_preserves_sections_appended_after_transcript(self):
        fixtures = {
            "heading": (
                "# 视频\n\n## 校对正文\n\n已完成\n\n"
                "## 原始字幕\n\n原始转写\n\n## 我的备注\n\n必须保留\n"
            ),
            "details": (
                "# 视频\n\n## 校对正文\n\n已完成\n\n"
                "<details>\n<summary>📄 原始字幕</summary>\n\n原始转写\n\n</details>\n\n"
                "## 我的备注\n\n必须保留\n"
            ),
        }
        for label, original in fixtures.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                note = pathlib.Path(temp_dir) / f"{label}.md"
                note.write_text(original, encoding="utf-8")
                with mock.patch.object(batch_transcriber, "KEEP_ORIGINAL_SUBTITLES", False):
                    removed = batch_transcriber.apply_original_subtitle_preference(str(note))
                updated = note.read_text(encoding="utf-8")
                self.assertTrue(removed)
                self.assertNotIn("原始转写", updated)
                self.assertIn("## 我的备注\n\n必须保留", updated)

    def test_opus_image_analysis_request_reaches_isolated_worker_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp_dir) / "state")}
        ):
            req = worker.TaskRequest.from_mapping(
                {"task": "bilibili-opus", "opus_image_analysis": "vision", "cooldown_delay": 60}
            )
            env = worker.build_env(req)
        self.assertEqual(req.opus_image_analysis, "vision")
        self.assertEqual(env["OPUS_IMAGE_ANALYSIS"], "vision")
        self.assertEqual(env["OPUS_IMAGE_ANALYSIS_COOLDOWN_DELAY"], "60")
        self.assertTrue(env["OPUS_IMAGE_ANALYSIS_CACHE_DIR"].endswith("state/opus-image-analysis-cache"))
        with self.assertRaisesRegex(ValueError, "off, ocr, or vision"):
            worker.TaskRequest.from_mapping({"task": "bilibili-opus", "opus_image_analysis": "invalid"})

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
        managed_env = runner.project_env(managed_cfg)
        self.assertEqual(managed_env["CONDA_ENV"], "")
        self.assertEqual(managed_env["LOCAL_NOTE_STUDIO_PYTHON_BIN"], sys.executable)

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

    def test_env_check_uses_effective_lm_studio_defaults_without_leaking_key(self):
        req = worker.TaskRequest(task="env-check", runtime_backend="python")
        with mock.patch.object(worker, "probe", return_value=(True, "ok")):
            report = worker.check_environment(req, {})
        self.assertIn(f"[OK] LLM API base - {worker.BUILTIN_LLM_API_BASE}", report)
        self.assertIn(f"[OK] LLM model - {worker.BUILTIN_LLM_MODEL}", report)
        self.assertIn("[OK] LLM API key - set", report)
        self.assertNotIn(worker.BUILTIN_LLM_API_KEY, report)
        self.assertNotIn("[MISSING] LLM API", report)

        custom_key = "test-secret-key"
        custom_env = {
            "DEFAULT_LLM_API_BASE": "http://127.0.0.1:9999/v1",
            "DEFAULT_LLM_API_KEY": custom_key,
            "DEFAULT_LLM_MODEL": "fixture-model",
        }
        with mock.patch.object(worker, "probe", return_value=(True, "ok")):
            custom_report = worker.check_environment(req, custom_env)
        self.assertIn("http://127.0.0.1:9999/v1", custom_report)
        self.assertIn("fixture-model", custom_report)
        self.assertNotIn(custom_key, custom_report)

    def test_real_task_environment_uses_same_effective_lm_studio_defaults(self):
        req = worker.TaskRequest(task="bilibili-opus", runtime_backend="python")
        with mock.patch.object(worker, "load_env_file", return_value={}), mock.patch.dict(
            os.environ, {}, clear=True
        ):
            env = worker.build_env(req)
        self.assertEqual(
            worker.effective_llm_config(req, env),
            (
                worker.BUILTIN_LLM_API_BASE,
                worker.BUILTIN_LLM_API_KEY,
                worker.BUILTIN_LLM_MODEL,
            ),
        )
        self.assertEqual(env["DEFAULT_LLM_API_BASE"], converter.DEFAULTS["DEFAULT_LLM_API_BASE"])
        self.assertEqual(env["DEFAULT_LLM_API_KEY"], converter.DEFAULTS["DEFAULT_LLM_API_KEY"])
        self.assertEqual(env["DEFAULT_LLM_MODEL"], organizer.DEFAULTS["DEFAULT_LLM_MODEL"])

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
            with mock.patch.dict(os.environ, {"LOCAL_NOTE_STUDIO_STATE_DIR": str(pathlib.Path(temp_dir) / "state")}):
                with mock.patch.object(worker, "run_command", side_effect=lambda *_args: print("refreshed") or ""):
                    with mock.patch.object(worker, "output_snapshot", side_effect=AssertionError("unexpected output scan")):
                        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                            self.assertEqual(worker.main(["--request-json", request]), 0)
                            self.assertTrue(stdout.getvalue().startswith("refreshed\nTASK_RESULT_JSON:"))

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
        self.assertEqual(env["OPUS_IMAGE_ANALYSIS_TIMEOUT_SECONDS"], "600")
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


class OpusImageAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.cache = self.root / "cache"
        converter._LAST_OPUS_IMAGE_MODEL_CALL_MONOTONIC = None

    def tearDown(self):
        converter._LAST_OPUS_IMAGE_MODEL_CALL_MONOTONIC = None
        self.temp.cleanup()

    def cfg(self, mode: str) -> dict[str, str]:
        return {
            **converter.DEFAULTS,
            "OPUS_IMAGE_ANALYSIS": mode,
            "OPUS_IMAGE_ANALYSIS_CACHE_DIR": str(self.cache),
            "OPUS_IMAGE_ANALYSIS_COOLDOWN_DELAY": "0",
            "DEFAULT_LLM_MODEL": "fixture-vision",
        }

    def asset(self, index: int, url: str, content: bytes | None = None) -> dict[str, object]:
        path = self.root / f"image-{index}.png"
        path.write_bytes(content or f"image-{index}".encode())
        return {
            "source_url": url,
            "path": str(path),
            "markdown_path": f"assets/post/image-{index}.png",
            "hash": converter.sha256_file(path),
            "status": "downloaded",
        }

    def completion(self, content: str, finish_reason: str = "stop", completion_tokens: int = 32):
        return converter.ChatCompletionResult(content, finish_reason, completion_tokens)

    def test_off_mode_keeps_legacy_download_only_behavior_without_model_call(self):
        url = "https://i.example/one.png"
        with mock.patch.object(converter, "_chat_completion") as model:
            section, summary = converter.build_opus_image_analysis([url], [self.asset(1, url)], "正文", self.cfg("off"))
        self.assertEqual(section, "")
        self.assertEqual(summary["status"], "off")
        self.assertEqual(summary["model_calls"], 0)
        model.assert_not_called()

    def test_ocr_and_vision_preserve_image_order_and_limit_low_relevance(self):
        urls = ["https://i.example/one.png", "https://i.example/two.png"]
        assets = [self.asset(1, urls[0]), self.asset(2, urls[1])]
        with mock.patch.object(
            converter, "_chat_completion", return_value=self.completion("第一行\n第二行")
        ) as model:
            ocr, ocr_summary = converter.build_opus_image_analysis(urls[:1], assets[:1], "正文", self.cfg("ocr"))
        self.assertIn("第一行\n  第二行", ocr)
        self.assertIn("OCR 模式只提取", ocr)
        self.assertEqual(ocr_summary["model_calls"], 1)
        image_content = model.call_args.args[1][0]["content"]
        self.assertEqual(image_content[1]["type"], "image_url")
        self.assertTrue(image_content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

        responses = [
            json.dumps(
                {
                    "relevance": "低",
                    "visible_text": "粉丝观看榜",
                    "visual_information": "互动排行榜截图",
                    "contextual_summary": "据此推荐买入某股票",
                    "uncertainties": "具体名次待核验",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "relevance": "高",
                    "visible_text": "行业收入 100",
                    "visual_information": "行业表格",
                    "contextual_summary": "表格补充了正文数据",
                    "uncertainties": "单位待核验",
                },
                ensure_ascii=False,
            ),
        ]
        with mock.patch.object(
            converter, "_chat_completion", side_effect=[self.completion(value) for value in responses]
        ):
            vision, summary = converter.build_opus_image_analysis(urls, assets, "行业复盘", self.cfg("vision"))
        self.assertLess(vision.index("### 图片 1"), vision.index("### 图片 2"))
        self.assertLess(vision.index("assets/post/image-1.png"), vision.index("assets/post/image-2.png"))
        self.assertIn("与正文相关性：低", vision)
        self.assertIn("不据此扩展财经、交易或投资结论", vision)
        self.assertNotIn("推荐买入", vision)
        self.assertIn("行业收入 100", vision)
        self.assertEqual(summary["model_calls"], 2)

    def test_failed_analysis_retains_traceable_placeholder_and_structured_warning(self):
        url = "https://i.example/fail.png"
        with mock.patch.object(converter, "_chat_completion", side_effect=RuntimeError("API_KEY=secret")):
            section, summary = converter.build_opus_image_analysis([url], [self.asset(1, url)], "正文", self.cfg("vision"))
        self.assertIn("图片分析失败/待重试", section)
        self.assertIn("不得据此推断图片内容", section)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["model_calls"], 1)
        self.assertNotIn("secret", json.dumps(summary, ensure_ascii=False))

    def test_sha256_cache_avoids_repeat_model_call(self):
        url = "https://i.example/cache.png"
        asset = self.asset(1, url)
        response = json.dumps(
            {
                "relevance": "高",
                "visible_text": "K线",
                "visual_information": "日K图",
                "contextual_summary": "与正文一致",
                "uncertainties": "价格不可辨",
            },
            ensure_ascii=False,
        )
        cfg = self.cfg("vision")
        cfg["OPUS_IMAGE_ANALYSIS_COOLDOWN_DELAY"] = "60"
        with (
            mock.patch.object(converter, "_chat_completion", return_value=self.completion(response)) as model,
            mock.patch.object(converter.time, "sleep") as sleep,
        ):
            _first, first_summary = converter.build_opus_image_analysis([url], [asset], "正文", cfg)
            second, second_summary = converter.build_opus_image_analysis([url], [asset], "正文", cfg)
        self.assertEqual(model.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(first_summary["model_calls"], 1)
        self.assertEqual(second_summary["model_calls"], 0)
        self.assertEqual(second_summary["cache_hits"], 1)
        self.assertIn("分析状态：缓存复用", second)

    def test_opus_ocr_and_vision_requests_have_independent_budgets(self):
        url = "https://i.example/budget.png"
        asset = self.asset(1, url)
        vision_json = json.dumps(
            {
                "relevance": "低",
                "visible_text": "互动榜",
                "visual_information": "排行榜",
                "contextual_summary": "低相关",
                "uncertainties": "无",
            },
            ensure_ascii=False,
        )
        for mode, content in (("ocr", "可见文字"), ("vision", vision_json)):
            with self.subTest(mode=mode):
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = json.dumps(
                    {
                        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                        "usage": {"completion_tokens": 17},
                    }
                ).encode()
                with mock.patch.object(converter.urllib.request, "urlopen", return_value=response) as urlopen:
                    _section, summary = converter.build_opus_image_analysis(
                        [url], [asset], "正文", self.cfg(mode)
                    )
                request = urlopen.call_args.args[0]
                payload = json.loads(request.data.decode("utf-8"))
                self.assertEqual(payload["max_tokens"], 4096)
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 300)
                self.assertEqual(summary["max_tokens"], 4096)
                self.assertEqual(summary["timeout_seconds"], 300)
                self.assertEqual(summary["completion_tokens"], 17)

    def test_opus_budget_defaults_overrides_and_hard_cap(self):
        self.assertEqual(converter.opus_image_analysis_max_tokens(converter.DEFAULTS), 4096)
        self.assertEqual(
            converter.opus_image_analysis_max_tokens(
                {**converter.DEFAULTS, "OPUS_IMAGE_ANALYSIS_MAX_TOKENS": "8192"}
            ),
            8192,
        )
        self.assertEqual(
            converter.opus_image_analysis_max_tokens(
                {**converter.DEFAULTS, "OPUS_IMAGE_ANALYSIS_MAX_TOKENS": "999999"}
            ),
            converter.OPUS_IMAGE_ANALYSIS_MAX_TOKENS_HARD_CAP,
        )
        self.assertEqual(
            converter.opus_image_analysis_timeout_seconds(
                {**converter.DEFAULTS, "OPUS_IMAGE_ANALYSIS_TIMEOUT_SECONDS": "45"}
            ),
            45,
        )
        with mock.patch.dict(
            os.environ,
            {
                "OPUS_IMAGE_ANALYSIS_MAX_TOKENS": "6144",
                "OPUS_IMAGE_ANALYSIS_TIMEOUT_SECONDS": "75",
            },
        ):
            configured = converter.config()
        self.assertEqual(converter.opus_image_analysis_max_tokens(configured), 6144)
        self.assertEqual(converter.opus_image_analysis_timeout_seconds(configured), 75)

    def test_legacy_pdf_completion_does_not_inherit_opus_budget(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "PDF 正文"}, "finish_reason": "stop"}]}
        ).encode()
        cfg = {**converter.DEFAULTS, "QWEN_PDF_POLISH_TIMEOUT_SECONDS": "777"}
        with mock.patch.object(converter.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(converter.call_chat_completion(cfg, [{"role": "user", "content": "PDF"}]), "PDF 正文")
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 777)

    def test_length_empty_and_invalid_json_retry_once_then_cache_success(self):
        valid = json.dumps(
            {
                "relevance": "高",
                "visible_text": "表格",
                "visual_information": "行业表",
                "contextual_summary": "补充正文",
                "uncertainties": "无",
            },
            ensure_ascii=False,
        )
        cases = (
            ("length", self.completion(valid, "length", 4096), "length"),
            ("empty", self.completion("", "stop", 48), "empty_content"),
            ("invalid-json", self.completion("not-json", "stop", 12), "invalid_json"),
        )
        for index, (label, first_completion, retry_reason) in enumerate(cases, 1):
            with self.subTest(label=label):
                url = f"https://i.example/{label}.png"
                success = self.completion(valid, "stop", 31)
                with mock.patch.object(
                    converter, "_chat_completion", side_effect=[first_completion, success]
                ) as model:
                    section, summary = converter.build_opus_image_analysis(
                        [url], [self.asset(index, url, label.encode())], "正文", self.cfg("vision")
                    )
                self.assertNotIn("图片分析失败/待重试", section)
                self.assertIn("- 分析状态：完成", section)
                self.assertEqual(summary["failed"], 0)
                self.assertEqual(summary["model_calls"], 2)
                self.assertEqual(summary["retry_count"], 1)
                self.assertEqual(summary["retry_reasons"], [retry_reason])
                self.assertEqual(summary["finish_reason"], "stop")
                self.assertEqual(model.call_count, 2)
                for call in model.call_args_list:
                    self.assertEqual(call.kwargs["max_tokens"], 4096)
                    self.assertEqual(call.kwargs["timeout_seconds"], 300)
        self.assertEqual(len(list(self.cache.glob("*.json"))), len(cases))

    def test_retryable_vision_failures_retry_only_once_and_never_cache(self):
        valid = json.dumps(
            {
                "relevance": "高",
                "visible_text": "表格",
                "visual_information": "行业表",
                "contextual_summary": "补充正文",
                "uncertainties": "无",
            },
            ensure_ascii=False,
        )
        cases = (
            ("length", self.completion(valid, "length", 4096), "length"),
            ("empty", self.completion("", "stop", 48), "stop"),
            ("reasoning-only", self.completion("", "length", 4096), "length"),
            ("invalid-json", self.completion("not-json", "stop", 12), "stop"),
        )
        for index, (label, completion, expected_reason) in enumerate(cases, 1):
            with self.subTest(label=label):
                url = f"https://i.example/failed-{label}.png"
                with mock.patch.object(converter, "_chat_completion", return_value=completion) as model:
                    section, summary = converter.build_opus_image_analysis(
                        [url], [self.asset(index, url, f"failed-{label}".encode())], "正文", self.cfg("vision")
                    )
                self.assertIn("图片分析失败/待重试", section)
                self.assertEqual(summary["failed"], 1)
                self.assertEqual(summary["model_calls"], 2)
                self.assertEqual(summary["retry_count"], 1)
                self.assertEqual(summary["finish_reason"], expected_reason)
                self.assertEqual(model.call_count, 2)
        self.assertEqual(list(self.cache.glob("*.json")) if self.cache.exists() else [], [])

    def test_automatic_retry_obeys_the_configured_sixty_second_cooldown(self):
        valid = json.dumps(
            {
                "relevance": "高",
                "visible_text": "表格",
                "visual_information": "行业表",
                "contextual_summary": "补充正文",
                "uncertainties": "无",
            },
            ensure_ascii=False,
        )
        cfg = {**self.cfg("vision"), "OPUS_IMAGE_ANALYSIS_COOLDOWN_DELAY": "60"}
        first = self.completion(valid, "length", 4096)
        second = self.completion(valid, "stop", 31)
        url = "https://i.example/cooldown.png"
        with mock.patch.object(converter, "_chat_completion", side_effect=[first, second]), mock.patch.object(
            converter.time, "sleep"
        ) as sleep:
            _section, summary = converter.build_opus_image_analysis(
                [url], [self.asset(9, url, b"cooldown")], "正文", cfg
            )
        sleep.assert_called_once()
        self.assertGreater(sleep.call_args.args[0], 59)
        self.assertLessEqual(sleep.call_args.args[0], 60)
        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["retry_count"], 1)

    def test_cache_rules_version_invalidates_without_deleting_old_entry(self):
        url = "https://i.example/versioned.png"
        asset = self.asset(1, url)
        context_hash = converter.opus_image_analysis_context_hash("正文", "", "Asia/Shanghai", "", "")
        result = {
            "relevance": "低",
            "visible_text": "榜单",
            "visual_information": "排行榜",
            "contextual_summary": "低相关",
            "uncertainties": "无",
        }
        converter.save_opus_image_analysis_cache(
            self.cfg("vision"), str(asset["hash"]), "vision", context_hash, result
        )
        old_path = converter.opus_image_cache_path(
            self.cfg("vision"), str(asset["hash"]), "vision", context_hash
        )
        self.assertTrue(old_path.exists())
        with mock.patch.object(converter, "OPUS_IMAGE_ANALYSIS_RULES_VERSION", "next-rules"):
            self.assertIsNone(
                converter.load_opus_image_analysis_cache(
                    self.cfg("vision"), str(asset["hash"]), "vision", context_hash
                )
            )
        self.assertTrue(old_path.exists())

    def test_timeout_retains_post_body_downloaded_image_and_retry_placeholder(self):
        url = "https://i.example/timeout.png"
        parsed = {
            "item": {"id": "timeout"},
            "title": "超时保留测试",
            "author": "作者",
            "author_mid": "42",
            "published": "2026-07-21T15:11:51+08:00",
            "content": "必须保留的正文",
            "images": [url],
        }
        asset = self.asset(1, url)
        cfg = {**self.cfg("vision"), "BILIBILI_COOKIES_FILE": str(self.root / "cookies.txt")}
        with (
            mock.patch.object(converter, "bilibili_cookie_path", return_value=self.root / "cookies.txt"),
            mock.patch.object(converter, "ensure_bilibili_cookie_login"),
            mock.patch.object(converter, "fetch_json_with_cookies", return_value={}),
            mock.patch.object(converter, "parse_bilibili_opus_payload", return_value=parsed),
            mock.patch.object(converter, "fetch_bilibili_opus_page_content", return_value=("", [])),
            mock.patch.object(
                converter,
                "download_markdown_assets",
                return_value=(f"必须保留的正文\n\n![动态图片 1]({asset['markdown_path']})", [asset]),
            ),
            mock.patch.object(converter, "_chat_completion", side_effect=TimeoutError("server timeout")),
        ):
            path, item, skipped = converter.convert_bilibili_opus(
                "https://www.bilibili.com/opus/999", self.root / "timeout-output", "fixture", cfg, {"items": []}, False, True
            )
        markdown = path.read_text(encoding="utf-8")
        self.assertFalse(skipped)
        self.assertIn("必须保留的正文", markdown)
        self.assertIn(str(asset["markdown_path"]), markdown)
        self.assertIn("图片分析失败/待重试", markdown)
        self.assertEqual(item["opus_image_analysis_status"], "failed")
        self.assertEqual(item["opus_image_analysis_model_calls"], 1)

    def test_retry_success_writes_complete_note_and_retry_diagnostics(self):
        url = "https://i.example/retry-success.png"
        parsed = {
            "item": {"id": "retry-success"},
            "title": "重试成功测试",
            "author": "作者",
            "author_mid": "42",
            "published": "2026-07-21T15:11:51+08:00",
            "content": "必须保留并完成整理的正文",
            "images": [url],
        }
        asset = self.asset(11, url, b"retry-success")
        valid = json.dumps(
            {
                "relevance": "高",
                "visible_text": "完整表格",
                "visual_information": "行业表",
                "contextual_summary": "补充正文",
                "uncertainties": "无",
            },
            ensure_ascii=False,
        )
        cfg = {**self.cfg("vision"), "BILIBILI_COOKIES_FILE": str(self.root / "cookies.txt")}
        with (
            mock.patch.object(converter, "bilibili_cookie_path", return_value=self.root / "cookies.txt"),
            mock.patch.object(converter, "ensure_bilibili_cookie_login"),
            mock.patch.object(converter, "fetch_json_with_cookies", return_value={}),
            mock.patch.object(converter, "parse_bilibili_opus_payload", return_value=parsed),
            mock.patch.object(converter, "fetch_bilibili_opus_page_content", return_value=("", [])),
            mock.patch.object(
                converter,
                "download_markdown_assets",
                return_value=(f"必须保留并完成整理的正文\n\n![动态图片 1]({asset['markdown_path']})", [asset]),
            ),
            mock.patch.object(
                converter,
                "_chat_completion",
                side_effect=[self.completion(valid, "length", 4096), self.completion(valid, "stop", 33)],
            ) as model,
        ):
            path, item, skipped = converter.convert_bilibili_opus(
                "https://www.bilibili.com/opus/997",
                self.root / "retry-success-output",
                "fixture",
                cfg,
                {"items": []},
                False,
                True,
            )
        markdown = path.read_text(encoding="utf-8")
        self.assertFalse(skipped)
        self.assertIn("必须保留并完成整理的正文", markdown)
        self.assertIn("- 分析状态：完成", markdown)
        self.assertIn("完整表格", markdown)
        self.assertNotIn("图片分析失败/待重试", markdown)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(item["opus_image_analysis_status"], "complete")
        self.assertEqual(item["opus_image_analysis_model_calls"], 2)
        self.assertEqual(item["opus_image_analysis_retry_count"], 1)
        self.assertEqual(item["opus_image_analysis_retry_reasons"], ["length"])
        self.assertEqual(len(list(self.cache.glob("*.json"))), 1)

    def test_retry_exhaustion_writes_body_attachment_placeholder_without_cache(self):
        url = "https://i.example/retry-failed.png"
        parsed = {
            "item": {"id": "retry-failed"},
            "title": "重试失败测试",
            "author": "作者",
            "author_mid": "42",
            "published": "2026-07-21T15:11:51+08:00",
            "content": "重试失败也必须保留的正文",
            "images": [url],
        }
        asset = self.asset(12, url, b"retry-failed")
        cfg = {**self.cfg("vision"), "BILIBILI_COOKIES_FILE": str(self.root / "cookies.txt")}
        with (
            mock.patch.object(converter, "bilibili_cookie_path", return_value=self.root / "cookies.txt"),
            mock.patch.object(converter, "ensure_bilibili_cookie_login"),
            mock.patch.object(converter, "fetch_json_with_cookies", return_value={}),
            mock.patch.object(converter, "parse_bilibili_opus_payload", return_value=parsed),
            mock.patch.object(converter, "fetch_bilibili_opus_page_content", return_value=("", [])),
            mock.patch.object(
                converter,
                "download_markdown_assets",
                return_value=(f"重试失败也必须保留的正文\n\n![动态图片 1]({asset['markdown_path']})", [asset]),
            ),
            mock.patch.object(
                converter,
                "_chat_completion",
                return_value=self.completion("not-json", "stop", 12),
            ) as model,
        ):
            path, item, skipped = converter.convert_bilibili_opus(
                "https://www.bilibili.com/opus/998",
                self.root / "retry-failed-output",
                "fixture",
                cfg,
                {"items": []},
                False,
                True,
            )
        markdown = path.read_text(encoding="utf-8")
        self.assertFalse(skipped)
        self.assertIn("重试失败也必须保留的正文", markdown)
        self.assertIn(str(asset["markdown_path"]), markdown)
        self.assertIn("图片分析失败/待重试", markdown)
        self.assertIn("不得据此推断图片内容", markdown)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(item["opus_image_analysis_status"], "failed")
        self.assertEqual(item["opus_image_analysis_model_calls"], 2)
        self.assertEqual(item["opus_image_analysis_retry_count"], 1)
        self.assertEqual(item["opus_image_analysis_retry_reasons"], ["invalid_json"])
        self.assertEqual(list(self.cache.glob("*.json")) if self.cache.exists() else [], [])

    def test_image_analysis_has_a_per_opus_model_call_limit(self):
        urls = [f"https://i.example/{index}.png" for index in range(1, 5)]
        assets = [self.asset(index, url) for index, url in enumerate(urls, 1)]
        response = json.dumps(
            {
                "relevance": "中",
                "visible_text": "表格",
                "visual_information": "财经表格",
                "contextual_summary": "补充正文",
                "uncertainties": "数值待核验",
            },
            ensure_ascii=False,
        )
        cfg = self.cfg("vision")
        cfg["OPUS_IMAGE_ANALYSIS_MAX_IMAGES"] = "2"
        with mock.patch.object(converter, "_chat_completion", return_value=self.completion(response)) as model:
            section, summary = converter.build_opus_image_analysis(urls, assets, "正文", cfg)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["limited"], 2)
        self.assertEqual(section.count("超过安全调用上限"), 2)

    def test_server_rendered_opus_page_exposes_complete_body_and_inline_images(self):
        image_url = "https://i0.hdslb.com/bfs/new_dyn/fixture.png@1192w"
        html = (
            '<html><body><div class="opus-module-content opus-paragraph-children">'
            '<h1>完整正文标题</h1><p>这是动态 API 摘要之后仍应保留的完整段落。</p>'
            f'<p><img src="//i0.hdslb.com/bfs/new_dyn/fixture.png@1192w"></p>'
            '<p>正文结尾和免责声明。</p></div></body></html>'
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = html.encode("utf-8")
        response.headers.get_content_charset.return_value = "utf-8"
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch.object(converter, "build_cookie_opener", return_value=opener):
            content, images = converter.fetch_bilibili_opus_page_content(
                "https://www.bilibili.com/opus/123", self.cfg("off")
            )
        self.assertIn("# 完整正文标题", content)
        self.assertIn("正文结尾和免责声明", content)
        self.assertIn(f"![image]({image_url})", content)
        self.assertEqual(images, [image_url])

    def test_converter_replaces_api_summary_with_page_body_without_duplicating_inline_image(self):
        image_url = "https://i0.hdslb.com/bfs/new_dyn/full-page.png@1192w"
        page_content = (
            "# 完整正文标题\n\nAPI 摘要之后的完整正文。\n\n"
            f"![image]({image_url})\n\n正文结尾。"
        )
        parsed = {
            "item": {"id": "long-opus"},
            "title": "长图文测试",
            "author": "作者",
            "author_mid": "42",
            "published": "2026-08-02T22:28:11+08:00",
            "content": "只有摘要...",
            "images": [],
        }
        asset = self.asset(13, image_url, b"full-page")

        def fake_download(markdown, _out_path, _referer, _cfg, _enabled):
            self.assertEqual(markdown.count(image_url), 1)
            return markdown.replace(image_url, str(asset["markdown_path"])), [asset]

        cfg = {**self.cfg("off"), "BILIBILI_COOKIES_FILE": str(self.root / "cookies.txt")}
        with (
            mock.patch.object(converter, "bilibili_cookie_path", return_value=self.root / "cookies.txt"),
            mock.patch.object(converter, "ensure_bilibili_cookie_login"),
            mock.patch.object(converter, "fetch_json_with_cookies", return_value={}),
            mock.patch.object(converter, "parse_bilibili_opus_payload", return_value=parsed),
            mock.patch.object(
                converter, "fetch_bilibili_opus_page_content", return_value=(page_content, [image_url])
            ),
            mock.patch.object(converter, "download_markdown_assets", side_effect=fake_download),
            mock.patch.object(converter, "bilibili_future_warning", return_value=""),
        ):
            path, item, skipped = converter.convert_bilibili_opus(
                "https://www.bilibili.com/opus/123",
                self.root / "full-page-output",
                "fixture",
                cfg,
                {"items": []},
                False,
                True,
            )
        markdown = path.read_text(encoding="utf-8")
        self.assertFalse(skipped)
        self.assertNotIn("只有摘要...", markdown)
        self.assertIn("API 摘要之后的完整正文", markdown)
        self.assertIn("正文结尾", markdown)
        self.assertEqual(markdown.count(str(asset["markdown_path"])), 1)
        expected_original = page_content.replace(image_url, str(asset["markdown_path"]))
        self.assertIn(f"original_content_sha256: {converter.sha256_bytes(expected_original.encode('utf-8'))}", markdown)
        self.assertIn(converter.ORIGINAL_CONTENT_END_MARKER, markdown)
        self.assertEqual(item["asset_count"], 1)

    def test_converter_keeps_original_markdown_image_references_in_order(self):
        urls = ["https://i.example/one.png", "https://i.example/two.png"]
        parsed = {
            "item": {"id": "123"},
            "title": "图文测试",
            "author": "作者",
            "author_mid": "42",
            "published": "2026-07-21T15:11:00+08:00",
            "content": "正文内容",
            "images": urls,
        }

        def fake_download(markdown, out_path, _referer, _cfg, _enabled):
            assets = [self.asset(1, urls[0]), self.asset(2, urls[1])]
            return (
                markdown.replace(urls[0], str(assets[0]["markdown_path"]))
                .replace(urls[1], str(assets[1]["markdown_path"])),
                assets,
            )

        cfg = {**self.cfg("off"), "BILIBILI_COOKIES_FILE": str(self.root / "cookies.txt")}
        with (
            mock.patch.object(converter, "bilibili_cookie_path", return_value=self.root / "cookies.txt"),
            mock.patch.object(converter, "ensure_bilibili_cookie_login"),
            mock.patch.object(converter, "fetch_json_with_cookies", return_value={}),
            mock.patch.object(converter, "parse_bilibili_opus_payload", return_value=parsed),
            mock.patch.object(converter, "fetch_bilibili_opus_page_content", return_value=("", [])),
            mock.patch.object(converter, "download_markdown_assets", side_effect=fake_download),
            mock.patch.object(converter, "bilibili_future_warning", return_value=""),
        ):
            path, item, skipped = converter.convert_bilibili_opus(
                "https://www.bilibili.com/opus/123", self.root / "output", "fixture", cfg, {"items": []}, False, True
            )
        markdown = path.read_text(encoding="utf-8")
        self.assertFalse(skipped)
        self.assertLess(markdown.index("![动态图片 1](assets/post/image-1.png)"), markdown.index("![动态图片 2](assets/post/image-2.png)"))
        self.assertEqual(item["opus_image_analysis"], "off")


class BilibiliMetadataIsolationTests(unittest.TestCase):
    def test_nested_source_headings_do_not_truncate_opus_model_or_preserved_original(self):
        original = (
            "# 一、一级标题\n\n引言段落。\n\n## 1｜二级标题\n\n"
            "这是摘要之后的正文。\n\n## 五、风险提示\n\n免责声明。"
        )
        body = (
            "# 草稿\n\n## 来源信息\n\n元数据\n\n## 原文抽取\n\n"
            f"{original}\n\n{organizer.ORIGINAL_CONTENT_END_MARKER}\n\n"
            "## 图片分析\n\n### 图片 1\n\n可见文字"
        )
        self.assertEqual(organizer.original_content_from_body(body, "bilibili-opus"), original)
        model_body = organizer.model_body_for_source(body, "bilibili-opus")
        self.assertIn("这是摘要之后的正文", model_body)
        self.assertIn("## 五、风险提示", model_body)
        preserved = organizer.original_source_section(body, "bilibili-opus")
        self.assertIn(original, preserved)
        self.assertTrue(preserved.endswith(organizer.ORIGINAL_CONTENT_END_MARKER))
        self.assertEqual(organizer.original_payload_for_hash(preserved, "bilibili-opus"), original)

    def test_opus_completion_rejects_an_original_that_does_not_match_its_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            note = pathlib.Path(temp_dir) / "truncated.md"
            expected = "完整正文\n\n## 原文中的标题\n\n正文结尾"
            note.write_text(
                "---\nstatus: organized\nsource_type: bilibili-opus\n"
                f"original_content_sha256: {organizer.sha256_text(expected)}\n---\n\n"
                "# 标题\n\n## 原文抽取\n\n"
                f"{organizer.ORIGINAL_SECTION_NOTICE}\n\n只有摘要...\n\n"
                f"{organizer.ORIGINAL_CONTENT_END_MARKER}\n",
                encoding="utf-8",
            )
            self.assertFalse(organizer.organized_note_complete(note, "bilibili-opus"))

    def test_opus_source_hash_ignores_volatile_api_fields(self):
        base = {
            "item": {"statistics": {"likes": 1}},
            "title": "稳定标题",
            "author": "作者",
            "author_mid": "42",
            "published": "2026-07-21T15:11:00+08:00",
            "content": "稳定正文",
            "images": ["https://i.example/image.png?token=one"],
        }
        changed = {
            **base,
            "item": {"statistics": {"likes": 999}, "render_id": "volatile"},
            "images": ["https://i.example/image.png?token=two"],
        }
        self.assertEqual(
            converter.bilibili_opus_source_hash(base, "123"),
            converter.bilibili_opus_source_hash(changed, "123"),
        )
        self.assertNotEqual(
            converter.bilibili_opus_source_hash(base, "123"),
            converter.bilibili_opus_source_hash({**base, "content": "正文已更新"}, "123"),
        )

    def test_future_time_validation_uses_explicit_asia_shanghai_timezone(self):
        now = dt.datetime(2026, 7, 21, 16, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        self.assertEqual(converter.bilibili_future_warning("2026-07-21 15:11", now=now), "")
        self.assertEqual(converter.bilibili_future_warning("2026-07-21T07:11:00Z", now=now), "")
        self.assertIn("明显晚于", converter.bilibili_future_warning("2026-07-21T17:00:00+08:00", now=now))
        self.assertEqual(converter.bilibili_future_warning("", now=now), "")
        self.assertEqual(converter.bilibili_future_warning("not-a-time", now=now), "")

    def test_real_opus_date_context_keeps_previous_evening_as_visible_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            image_path = root / "date.png"
            image_path.write_bytes(b"fixture")
            asset = {
                "source_url": "https://i.example/date.png",
                "path": str(image_path),
                "markdown_path": "assets/date.png",
                "hash": converter.sha256_file(image_path),
                "status": "downloaded",
            }
            cfg = {
                **converter.DEFAULTS,
                "OPUS_IMAGE_ANALYSIS": "vision",
                "OPUS_IMAGE_ANALYSIS_CACHE_DIR": str(root / "cache"),
                "OPUS_IMAGE_ANALYSIS_COOLDOWN_DELAY": "0",
            }
            response = converter.ChatCompletionResult(
                json.dumps(
                    {
                        "relevance": "高",
                        "visible_text": "2026年6月17日晚间，72家澄清公告",
                        "visual_information": "行业分组表",
                        "contextual_summary": "图片补充了公告分组",
                        "uncertainties": "模糊细节待核验",
                    },
                    ensure_ascii=False,
                ),
                "stop",
                128,
            )
            with mock.patch.object(converter, "_chat_completion", return_value=response) as model:
                section, summary = converter.build_opus_image_analysis(
                    [str(asset["source_url"])],
                    [asset],
                    "72家上市公司澄清",
                    cfg,
                    published="2026-06-18T09:01:45+08:00",
                    timezone_name="Asia/Shanghai",
                    current_date="2026-07-21",
                    time_warning="",
                )
            prompt = model.call_args.args[1][0]["content"][0]["text"]
            self.assertIn("动态发布时间：2026-06-18T09:01:45+08:00", prompt)
            self.assertIn("当前日期：2026-07-21", prompt)
            self.assertIn("图片日期比动态发布时间早一天属于正常情况", prompt)
            self.assertIn("2026年6月17日晚间", section)
            for forbidden in ("应为2024年", "未来预设", "时间戳错误", "年份疑似笔误"):
                self.assertNotIn(forbidden, section)
            self.assertEqual(summary["finish_reason"], "stop")

    def test_time_hallucination_guard_only_changes_unsupported_model_corrections(self):
        model_text = "## 待核验\n\n2026年6月17日晚间疑似错误，应为2024年；另有原文时间待核验。"
        sanitized = organizer.sanitize_bilibili_time_hallucinations(model_text, "## 原文抽取\n\n普通正文", False)
        self.assertNotIn("应为2024年", sanitized)
        self.assertIn("2026年6月17日晚间", sanitized)
        self.assertIn("原文时间待核验", sanitized)
        self.assertIn(
            "应为2024年",
            organizer.sanitize_bilibili_time_hallucinations(model_text, "## 原文抽取\n\n普通正文", True),
        )

    def test_organize_prompts_forbid_unsupported_english_aliases(self):
        cfg = {**organizer.DEFAULTS, "A_SHARE_TERMS_ENABLED": "false"}
        with mock.patch.object(organizer, "call_chat_completion", return_value="## 核心观点\n\n保留沃什。") as model:
            organizer.organize_chunk("标题", "source.md", "来源只写沃什", 1, 1, "bilibili-opus", cfg)
        chunk_system = model.call_args.args[1][0]["content"]
        self.assertIn("不得为来源中没有", chunk_system)
        self.assertIn("沃什", chunk_system)
        self.assertIn("Waller", chunk_system)
        self.assertIn("待核验", chunk_system)

        with mock.patch.object(organizer, "call_chat_completion", return_value="## 核心观点\n\n保留沃什。") as model:
            organizer.synthesize_text("标题", "source.md", "来源只写沃什", cfg, "bilibili-opus")
        synthesis_system = model.call_args.args[1][0]["content"]
        self.assertIn("不得为来源中没有", synthesis_system)
        self.assertIn("沃什", synthesis_system)
        self.assertIn("Waller", synthesis_system)
        self.assertIn("待核验", synthesis_system)

    def test_model_added_wrong_stock_suffixes_are_removed_and_reference_table_stays_correct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            draft = root / "draft.md"
            draft.write_text(
                "---\n"
                "title: 72家澄清公告\nsource_type: bilibili-opus\n"
                "source_url: https://www.bilibili.com/opus/1215072468872462337\n"
                "dynamic_id: 1215072468872462337\npublished: 2026-06-18T09:01:45+08:00\n"
                "source_hash: fixture\nopus_image_analysis: vision\nopus_image_analysis_status: complete\n"
                "time_warning: false\n---\n\n# 72家澄清公告\n\n"
                "## 原文抽取\n\n中材科技、山东墨龙、通鼎互联、中核科技发布澄清公告。\n\n"
                "## 图片分析\n\n- 可见文字：2026年6月17日晚间\n",
                encoding="utf-8",
            )
            cfg = {
                **organizer.DEFAULTS,
                "INDEX_DIR": str(root / "index"),
                "QWEN_ORGANIZE_COOLDOWN_DELAY": "0",
                "A_SHARE_TERMS_ENABLED": "true",
            }
            wrong = (
                "## 核心观点\n\n中材科技（002080.SH）、山东墨龙(002490.SH)、"
                "通鼎互联（002491.SH）、中核科技(000777.SH)均在表中。\n\n"
                "## 待核验\n\n图片中的2026年被判断为年份疑似笔误，应为2024年。"
            )
            with mock.patch.object(organizer, "call_chat_completion", return_value=wrong):
                output, _item = organizer.organize_file(draft, root / "output", cfg, omit_draft_path=True)
            final = output.read_text(encoding="utf-8")
            for wrong_code in ("002080.SH", "002490.SH", "002491.SH", "000777.SH"):
                self.assertNotIn(wrong_code, final)
            for correct_code in ("002080.SZ", "002490.SZ", "002491.SZ", "000777.SZ"):
                self.assertIn(correct_code, final)
            for forbidden in ("应为2024年", "未来预设", "时间戳错误", "年份疑似笔误"):
                self.assertNotIn(forbidden, final)
            self.assertIn("2026年6月17日晚间", final)

    def test_source_backed_stock_codes_get_deterministic_exchange_suffix(self):
        source = "原文代码：002080、002490、002491、000777"
        model = "002080.SH 002490.SH 002491.SH 000777.SH"
        sanitized = organizer.sanitize_model_stock_codes(model, source, True)
        self.assertEqual(sanitized, "002080.SZ 002490.SZ 002491.SZ 000777.SZ")

    def test_failed_image_retry_never_overwrites_existing_complete_note(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            output_dir = root / "output"
            output_dir.mkdir()
            source_url = "https://www.bilibili.com/opus/1215072468872462337"
            existing = output_dir / "existing.md"
            original_existing = (
                "---\nsource_type: bilibili-opus\nsource_url: " + source_url + "\n"
                "status: organized\nopus_image_analysis: vision\nopus_image_analysis_status: complete\n---\n\n"
                "# 已有完整笔记\n\n## 原文抽取\n\n完整正文\n"
            )
            existing.write_text(original_existing, encoding="utf-8")
            draft = root / "draft.md"
            draft.write_text(
                "---\ntitle: 重试草稿\nsource_type: bilibili-opus\nsource_url: " + source_url + "\n"
                "dynamic_id: 1215072468872462337\nsource_hash: changed\n"
                "opus_image_analysis: vision\nopus_image_analysis_status: failed\n---\n\n"
                "# 重试草稿\n\n## 原文抽取\n\n新正文\n\n## 图片分析\n\n- 分析状态：图片分析失败/待重试\n",
                encoding="utf-8",
            )
            cfg = {
                **organizer.DEFAULTS,
                "INDEX_DIR": str(root / "index"),
                "ORGANIZED_OUTPUT_DIR": str(output_dir),
            }
            with mock.patch.object(organizer, "config", return_value=cfg), mock.patch.object(
                organizer, "call_chat_completion", side_effect=AssertionError("model must not run")
            ), mock.patch.object(
                sys,
                "argv",
                ["qwen_organize_notes.py", "--source", str(draft), "--output-dir", str(output_dir), "--overwrite"],
            ):
                self.assertEqual(organizer.main(), 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), original_existing)

    def test_bilibili_model_context_excludes_deterministic_metadata_and_python_restores_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            draft = root / "draft.md"
            draft.write_text(
                "---\n"
                "title: 测试动态\nsource_type: bilibili-opus\n"
                "source_url: https://www.bilibili.com/opus/123\n"
                "dynamic_id: 123\nauthor: 作者\nauthor_mid: 42\n"
                "published: 2026-07-21T15:11:00+08:00\nsource_hash: abcdef\n"
                "opus_image_analysis: vision\nopus_image_analysis_status: complete\n"
                "---\n\n# 测试动态\n\n"
                "## 来源信息\n\n- 转换时间：2026-07-21T18:00:00+08:00\n- SHA256：`abcdef`\n\n"
                "## 原文抽取\n\n正文明确讨论交易心态。\n\n![动态图片 1](assets/one.png)\n\n"
                "## 图片分析\n\n### 图片 1\n- 与正文相关性：低\n- 图表/画面信息：粉丝互动榜\n",
                encoding="utf-8",
            )
            cfg = {
                **organizer.DEFAULTS,
                "INDEX_DIR": str(root / "index"),
                "QWEN_ORGANIZE_COOLDOWN_DELAY": "0",
                "A_SHARE_TERMS_ENABLED": "false",
            }
            captured = []

            def fake_model(_cfg, messages):
                captured.append(messages)
                return "## 速读摘要\n\n正文讨论交易心态；粉丝互动榜与交易观点低相关。"

            with mock.patch.object(organizer, "call_chat_completion", side_effect=fake_model):
                output, _item = organizer.organize_file(draft, root / "output", cfg, omit_draft_path=True)
            prompt = json.dumps(captured, ensure_ascii=False)
            for excluded in (
                "https://www.bilibili.com/opus/123",
                "author_mid",
                "2026-07-21T15:11:00+08:00",
                "abcdef",
                "转换时间",
            ):
                self.assertNotIn(excluded, prompt)
            self.assertIn("正文明确讨论交易心态", prompt)
            self.assertIn("粉丝互动榜", prompt)
            self.assertIn("不要重新判断", prompt)

            final = output.read_text(encoding="utf-8")
            final_meta, _final_body = organizer.parse_frontmatter(final)
            self.assertEqual(final_meta["published"], "2026-07-21T15:11:00+08:00")
            self.assertEqual(final_meta["source_url"], "https://www.bilibili.com/opus/123")
            self.assertIn("- 发布时间：2026-07-21T15:11:00+08:00", final)
            self.assertNotIn("未来预设", final)
            self.assertTrue(organizer.organized_note_complete(output, "bilibili-opus", "vision", "abcdef"))

            failed = final.replace("opus_image_analysis_status: complete", "opus_image_analysis_status: failed")
            output.write_text(failed, encoding="utf-8")
            self.assertFalse(organizer.organized_note_complete(output, "bilibili-opus", "vision", "abcdef"))


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

    def test_integrity_rejects_truncated_original_when_conversion_hash_is_present(self):
        expected = "完整正文\n\n## 原文内标题\n\n正文结尾"
        path = self.root / "truncated-opus.md"
        path.write_text(
            "---\nsource_url: https://www.bilibili.com/opus/123\nstatus: organized\n"
            f"original_content_sha256: {hashlib.sha256(expected.encode('utf-8')).hexdigest()}\n---\n\n"
            "# 标题\n\n## 原文抽取\n\n"
            f"{worker.ORIGINAL_SECTION_NOTICE}\n\n只有摘要...\n\n"
            f"{worker.ORIGINAL_CONTENT_END_MARKER}\n",
            encoding="utf-8",
        )
        errors = worker.validate_markdown_output(path, worker.TaskRequest(task="bilibili-opus"))
        self.assertTrue(any("完整原文校验失败" in error for error in errors))

    def test_identical_staged_assets_are_reused_without_touching_complete_output(self):
        staged = self.root / "staged" / "assets" / "post"
        output = self.root / "output" / "assets" / "post"
        staged.mkdir(parents=True)
        output.mkdir(parents=True)
        source = staged / "image.png"
        target = output / "image.png"
        source.write_bytes(b"same-image")
        target.write_bytes(b"same-image")
        old_mtime = target.stat().st_mtime_ns
        worker.promote_staged_assets(self.root / "staged", self.root / "output")
        self.assertEqual(target.stat().st_mtime_ns, old_mtime)

    def test_image_paths_with_parentheses_pass_integrity_check(self):
        asset_dir = self.root / "assets" / "BILI-OPUS-7.8(复盘)_1222699793902469157"
        asset_dir.mkdir(parents=True)
        (asset_dir / "image-001.png").write_bytes(b"png")
        path = self.root / "BILI-OPUS-7.8(复盘)_1222699793902469157.md"
        path.write_text(
            "---\nsource_url: https://www.bilibili.com/opus/1222699793902469157\nstatus: organized\n---\n\n"
            "# 7.8(复盘)\n\n"
            "![动态图片 1](assets/BILI-OPUS-7.8(复盘)_1222699793902469157/image-001.png)\n\n"
            "## 原文抽取\n\n正文\n",
            encoding="utf-8",
        )
        self.assertEqual(worker.markdown_image_targets(path.read_text(encoding="utf-8")), ["assets/BILI-OPUS-7.8(复盘)_1222699793902469157/image-001.png"])
        self.assertEqual(worker.validate_markdown_output(path, worker.TaskRequest(task="bilibili-opus")), [])

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
    def test_generated_path_contract_excludes_skipped_existing_markdown(self):
        with tempfile.TemporaryDirectory() as temp:
            generated = pathlib.Path(temp) / "new note.md"
            skipped = pathlib.Path(temp) / "old note.md"
            generated.write_text("new", encoding="utf-8")
            skipped.write_text("old", encoding="utf-8")
            output = "\n".join([
                f"SKIPPED_EXISTING_MARKDOWN_PATH:{skipped}",
                f"GENERATED_MARKDOWN_PATH:{generated}",
            ])
            self.assertEqual(runner.extract_markdown_paths(output), [str(generated)])
            self.assertEqual(batch_transcriber._extract_output_paths(output), [str(generated)])

    def test_skipped_local_file_does_not_enter_postprocessing(self):
        cfg = {"CONDA_ENV": "", "VIDEO_MANIFEST_ENABLED": "false"}
        skipped_output = "SKIPPED_EXISTING_MARKDOWN_PATH:/tmp/already-exists.md\n"
        with (
            mock.patch.object(runner, "project_env", return_value={}),
            mock.patch.object(runner, "bash_command", return_value=["bash", "transcribe.sh"]),
            mock.patch.object(runner, "stream_command", return_value=(0, skipped_output)) as stream,
            mock.patch.object(runner, "postprocess_video_notes") as postprocess,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = runner.run_local_file(ROOT / "worker", cfg, "/tmp/fixture.mp3", False)
        self.assertEqual(code, 0)
        self.assertEqual(stream.call_count, 1)
        postprocess.assert_not_called()
        self.assertIn("[无需更新] 已有同名完整笔记", stdout.getvalue())
        result_line = next(
            line for line in stdout.getvalue().splitlines() if line.startswith("LOCAL_BATCH_RESULT_JSON:")
        )
        self.assertEqual(
            json.loads(result_line.split(":", 1)[1]),
            {"total": 1, "changed": 0, "skipped": 1, "failed": 0},
        )

    def test_local_media_batch_summary_updates_worker_counts(self):
        req = worker.TaskRequest(task="local-video", output_dir="/tmp/notes")
        result = worker.TaskResult(
            "local-existing",
            "gui",
            req.task,
            "completed",
            worker.utc_now(),
            output_dir=req.output_dir,
        )
        output = 'LOCAL_BATCH_RESULT_JSON:{"total":3,"changed":0,"skipped":3,"failed":0}\n'
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            worker.record_batch_task_summaries(output, result, req)
        self.assertEqual(result.counts["discovered"], 3)
        self.assertEqual(result.counts["skipped"], 3)
        self.assertEqual(result.details["local_media_batch"]["changed"], 0)
        self.assertIn("[本地媒体] 已检查 3 个：新建/更新 0，无需更新 3，失败 0。", stdout.getvalue())

    def test_skipped_incomplete_local_file_retries_summary_from_preserved_transcript(self):
        with tempfile.TemporaryDirectory() as temp:
            note = pathlib.Path(temp) / "incomplete.md"
            note.write_text(
                "# 视频\n\n## 速读摘要\n\n【AI待处理：重试】\n\n"
                "<details>\n<summary>📄 原始字幕</summary>\n\n可恢复转写\n\n</details>\n",
                encoding="utf-8",
            )
            cfg = {"CONDA_ENV": "", "VIDEO_MANIFEST_ENABLED": "false"}
            skipped_output = f"SKIPPED_EXISTING_MARKDOWN_PATH:{note}\n"
            with (
                mock.patch.object(runner, "project_env", return_value={}),
                mock.patch.object(runner, "bash_command", return_value=["bash", "transcribe.sh"]),
                mock.patch.object(runner, "python_command", return_value=["python", "summary.py"]),
                mock.patch.object(runner, "stream_command", side_effect=[(0, skipped_output), (0, "summary ok")]) as stream,
                mock.patch.object(runner, "postprocess_video_notes") as postprocess,
            ):
                code = runner.run_local_file(ROOT / "worker", cfg, "/tmp/fixture.mp3", False)
            self.assertEqual(code, 0)
            self.assertEqual(stream.call_count, 2)
            self.assertEqual(postprocess.call_count, 2)

    def test_skipped_incomplete_legacy_note_accepts_complete_original_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            note = pathlib.Path(temp) / "legacy-incomplete.md"
            note.write_text(
                "# 视频\n\n## 速读摘要\n\n【AI待处理：重试】\n\n"
                "## 完整原文\n\n可恢复转写\n",
                encoding="utf-8",
            )
            output = f"SKIPPED_EXISTING_MARKDOWN_PATH:{note}\n"
            self.assertEqual(runner.extract_retryable_existing_markdown_paths(output), [str(note)])

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
