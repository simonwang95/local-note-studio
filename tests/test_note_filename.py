from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "worker" / "local_note_studio_worker.py"
SCRIPTS_DIR = ROOT / "worker" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


note_filename = load_module("note_filename_test", SCRIPTS_DIR / "note_filename.py")
renamer = load_module("rename_notes_date_test", SCRIPTS_DIR / "rename_notes_date.py")
organizer = load_module("qwen_organize_notes_test", SCRIPTS_DIR / "qwen_organize_notes.py")
worker = load_module("local_note_studio_worker_test", WORKER_PATH)


class ParsePublishedDateTests(unittest.TestCase):
    def test_iso_with_timezone(self):
        self.assertEqual(note_filename.parse_published_date("2026-07-14T12:12:57+08:00"), "2026-07-14")

    def test_iso_without_timezone(self):
        self.assertEqual(note_filename.parse_published_date("2026-06-16T22:10:58"), "2026-06-16")

    def test_quoted_value(self):
        self.assertEqual(note_filename.parse_published_date('"2026-08-03T09:00:00+08:00"'), "2026-08-03")

    def test_plain_date(self):
        self.assertEqual(note_filename.parse_published_date("2026-05-12"), "2026-05-12")

    def test_empty(self):
        self.assertEqual(note_filename.parse_published_date(""), "")

    def test_unknown(self):
        self.assertEqual(note_filename.parse_published_date("unknown"), "")

    def test_invalid_calendar_date(self):
        self.assertEqual(note_filename.parse_published_date("2026-13-40T00:00:00"), "")


class PrefixHelpersTests(unittest.TestCase):
    def test_has_date_prefix(self):
        self.assertTrue(note_filename.has_date_prefix("2026-07-14-BILI-OPUS-x.md"))
        self.assertFalse(note_filename.has_date_prefix("BILI-OPUS-x.md"))

    def test_strip_date_prefix(self):
        self.assertEqual(note_filename.strip_date_prefix("2026-07-14-BILI-OPUS-x.md"), "BILI-OPUS-x.md")
        self.assertEqual(note_filename.strip_date_prefix("BILI-OPUS-x.md"), "BILI-OPUS-x.md")

    def test_prepend_date_prefix(self):
        self.assertEqual(note_filename.prepend_date_prefix("BILI-OPUS-x.md", "2026-07-14"), "2026-07-14-BILI-OPUS-x.md")

    def test_prepend_is_idempotent(self):
        self.assertEqual(
            note_filename.prepend_date_prefix("2026-07-14-BILI-OPUS-x.md", "2026-07-14"),
            "2026-07-14-BILI-OPUS-x.md",
        )

    def test_prepend_with_empty_date(self):
        self.assertEqual(note_filename.prepend_date_prefix("BILI-OPUS-x.md", ""), "BILI-OPUS-x.md")

    def test_flag_enabled(self):
        self.assertTrue(note_filename.flag_enabled("true"))
        self.assertTrue(note_filename.flag_enabled("1"))
        self.assertFalse(note_filename.flag_enabled("false"))
        self.assertFalse(note_filename.flag_enabled(""))


class OrganizeBuildOutputPathTests(unittest.TestCase):
    def test_no_flag_keeps_default_name(self):
        path = organizer.build_output_path(
            pathlib.Path("/tmp/out"), "5分钟底部结构或许能形成", "bilibili-opus", "", "1224769955262627840",
            "2026-07-14T12:12:57+08:00", False,
        )
        self.assertEqual(path.name, "BILI-OPUS-5分钟底部结构或许能形成_1224769955262627840.md")

    def test_flag_prepends_date(self):
        path = organizer.build_output_path(
            pathlib.Path("/tmp/out"), "5分钟底部结构或许能形成", "bilibili-opus", "", "1224769955262627840",
            "2026-07-14T12:12:57+08:00", True,
        )
        self.assertEqual(path.name, "2026-07-14-BILI-OPUS-5分钟底部结构或许能形成_1224769955262627840.md")

    def test_flag_without_published_keeps_default_name(self):
        path = organizer.build_output_path(
            pathlib.Path("/tmp/out"), "无日期", "bilibili-opus", "", "123", "", True,
        )
        self.assertEqual(path.name, "BILI-OPUS-无日期_123.md")

    def test_custom_output_filename_is_not_prefixed(self):
        path = organizer.build_output_path(
            pathlib.Path("/tmp/out"), "标题", "bilibili-opus", "my-custom-name.md", "123",
            "2026-07-14T12:12:57+08:00", True,
        )
        self.assertEqual(path.name, "my-custom-name.md")


class RenameNotesDateTests(unittest.TestCase):
    def _write(self, directory: pathlib.Path, name: str, published: str) -> None:
        (directory / name).write_text(
            f"---\ntitle: t\nsource_type: bilibili-opus\nsource_url: https://www.bilibili.com/opus/1\n"
            f"published: {published}\nstatus: organized\n---\n# t\n## 来源追溯\n- 原始来源：`x`\n",
            encoding="utf-8",
        )

    def test_renames_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            self._write(directory, "BILI-OPUS-A_1.md", "2026-07-14T12:12:57+08:00")
            self._write(directory, "BILI-OPUS-B_2.md", "2026-08-02T09:00:00+08:00")
            self._write(directory, "BILI-OPUS-NODATE_3.md", "unknown")
            self._write(directory, "2026-05-12-ALREADY_4.md", "2026-05-12T00:00:00+08:00")

            self.assertEqual(renamer.rename_directory(directory, dry_run=False), 0)
            self.assertTrue((directory / "2026-07-14-BILI-OPUS-A_1.md").exists())
            self.assertTrue((directory / "2026-08-02-BILI-OPUS-B_2.md").exists())
            self.assertFalse((directory / "BILI-OPUS-A_1.md").exists())
            # no-date and already-prefixed files are untouched
            self.assertTrue((directory / "BILI-OPUS-NODATE_3.md").exists())
            self.assertTrue((directory / "2026-05-12-ALREADY_4.md").exists())

            # Re-run must be a no-op (idempotent).
            self.assertEqual(renamer.rename_directory(directory, dry_run=False), 0)
            self.assertEqual(len(list(directory.glob("*.md"))), 4)

    def test_dry_run_does_not_rename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            self._write(directory, "BILI-OPUS-A_1.md", "2026-07-14T12:12:57+08:00")
            self.assertEqual(renamer.rename_directory(directory, dry_run=True), 0)
            self.assertTrue((directory / "BILI-OPUS-A_1.md").exists())
            self.assertFalse((directory / "2026-07-14-BILI-OPUS-A_1.md").exists())

    def test_conflict_target_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            self._write(directory, "BILI-OPUS-A_1.md", "2026-07-14T12:12:57+08:00")
            (directory / "2026-07-14-BILI-OPUS-A_1.md").write_text("---\ntitle: other\n---\n# other\n## 来源追溯\n- 原始来源：`y`\n", encoding="utf-8")
            self.assertEqual(renamer.rename_directory(directory, dry_run=False), 0)
            # The original is left in place because the target already exists.
            self.assertTrue((directory / "BILI-OPUS-A_1.md").exists())

    def test_malformed_frontmatter_does_not_read_published_from_body(self):
        markdown = "---\ntitle: broken\n# 正文\npublished: 2026-07-14\n"
        self.assertEqual(renamer.published_from_markdown(markdown), "")


class WorkerDateInFilenameTests(unittest.TestCase):
    def test_from_mapping_parses_flag(self):
        req = worker.TaskRequest.from_mapping({
            "task": "bilibili-opus", "source": "https://www.bilibili.com/opus/1",
            "output_dir": "notes/动态图文", "date_in_filename": True,
        })
        self.assertTrue(req.date_in_filename)

    def test_from_mapping_defaults_false(self):
        req = worker.TaskRequest.from_mapping({
            "task": "bilibili-opus", "source": "s", "output_dir": "out",
        })
        self.assertFalse(req.date_in_filename)

    def test_build_env_sets_flag(self):
        req = worker.TaskRequest.from_mapping({
            "task": "bilibili-opus", "source": "s", "output_dir": "out", "date_in_filename": True,
        })
        env = worker.build_env(req)
        self.assertEqual(env.get("DATE_IN_FILENAME"), "true")
        req_off = worker.TaskRequest.from_mapping({"task": "bilibili-opus", "source": "s", "output_dir": "out"})
        self.assertEqual(worker.build_env(req_off).get("DATE_IN_FILENAME"), "false")

    def test_build_env_sets_thinking_flag(self):
        req = worker.TaskRequest.from_mapping({
            "task": "bilibili-url", "source": "s", "output_dir": "out", "enable_thinking": True,
        })
        self.assertTrue(req.enable_thinking)
        self.assertEqual(worker.build_env(req).get("QWEN_ORGANIZE_ENABLE_THINKING"), "true")
        self.assertEqual(worker.build_env(req).get("SUMMARY_ENABLE_THINKING"), "true")
        req_off = worker.TaskRequest.from_mapping({"task": "bilibili-url", "source": "s", "output_dir": "out"})
        self.assertFalse(req_off.enable_thinking)
        self.assertEqual(worker.build_env(req_off).get("QWEN_ORGANIZE_ENABLE_THINKING"), "false")
        self.assertEqual(worker.build_env(req_off).get("SUMMARY_ENABLE_THINKING"), "false")

    def test_rename_task_command_requires_only_output_dir(self):
        req = worker.TaskRequest.from_mapping({"task": "rename-notes-date", "output_dir": "notes/动态图文"})
        command = worker.command_for(req)
        self.assertIn("rename_notes_date.py", " ".join(command))
        self.assertIn("--output-dir", command)

    def test_rename_task_dry_run_flag(self):
        req = worker.TaskRequest.from_mapping({"task": "rename-notes-date", "output_dir": "out", "dry_run": True})
        command = worker.command_for(req)
        self.assertIn("--dry-run", command)


if __name__ == "__main__":
    unittest.main()
