"""Exercise local-video timing through the real shell, without an ASR model."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
SEGMENTS = [
    {"start": 0.1, "end": 0.6, "text": "第一段讨论模型训练。"},
    {"start": 0.6, "end": 1.0, "text": "第二段说明人工复核。"},
]


@unittest.skipUnless(FFMPEG and FFPROBE, "local-video shell regression requires ffmpeg and ffprobe")
class LocalVideoShellTimingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lns-shell-timing-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        script_dir = self.root / "worker" / "scripts" / "bilibili"
        script_dir.mkdir(parents=True)
        self.script = script_dir / "bilibili_transcript.sh"
        shutil.copy2(ROOT / "worker/scripts/bilibili/bilibili_transcript.sh", self.script)
        for name in ("transcript_timing.py", "transcript_quality.py", "task_diagnostics.py"):
            shutil.copy2(ROOT / "worker/scripts" / name, script_dir.parent / name)
        (script_dir / "whisper_transcribe.py").write_text("""
import argparse, json, os, pathlib, wave
parser = argparse.ArgumentParser()
parser.add_argument('--audio')
parser.add_argument('--output-file')
parser.add_argument('--segments-output')
args, _ = parser.parse_known_args()
with wave.open(args.audio) as audio:
    assert audio.getframerate() == 16000 and audio.getnchannels() == 1
segments = json.loads(os.environ['MOCK_ASR_SEGMENTS'])
pathlib.Path(args.output_file).write_text('fixture-whisper\\n' + '\\n'.join(row['text'] for row in segments), encoding='utf-8')
pathlib.Path(args.segments_output).write_text('{invalid' if os.environ.get('MOCK_INVALID_TIMING') else json.dumps({'segments': segments}), encoding='utf-8')
pathlib.Path(os.environ['MOCK_ASR_CALL']).write_text(json.dumps({'audio': args.audio, 'timing': args.segments_output}), encoding='utf-8')
""", encoding="utf-8")
        self.media = self.root / "clip.mp4"
        subprocess.run([
            FFMPEG, "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=16x16:r=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
            "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(self.media),
        ], check=True, capture_output=True, timeout=30)
        self.output = self.root / "notes"
        self.cache = self.root / "audio-cache"
        self.transcripts = self.root / "transcripts"
        self.call = self.root / "asr-call.json"
        self.env = {
            "PATH": os.pathsep.join(dict.fromkeys((str(Path(FFMPEG).parent), str(Path(FFPROBE).parent), "/usr/bin", "/bin"))),
            "LOCAL_NOTE_STUDIO_ENV_LOADED": "1",
            "LOCAL_NOTE_STUDIO_PYTHON_BIN": sys.executable,
            "LOCAL_NOTE_STUDIO_INCOGNITO": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CONDA_ENV": "", "ENABLE_OPENCC": "false", "FORCE_ASR": "true",
            "ASR_ENGINE": "whisper", "ASR_LOCAL_MODEL": str(self.root),
            "CACHE_DIR": str(self.cache), "MODEL_CACHE_DIR": str(self.root / "models"),
            "LOCAL_NOTE_STUDIO_STATE_DIR": str(self.root / "state"),
            "TRANSCRIPT_CACHE_DIR": str(self.transcripts),
            "MOCK_ASR_SEGMENTS": json.dumps(SEGMENTS), "MOCK_ASR_CALL": str(self.call),
        }

    def transcribe(self):
        return subprocess.run([
            "/bin/bash", str(self.script), "--local-file", str(self.media),
            "--output-dir", str(self.output), "--output-filename", "result.md",
        ], env=self.env, text=True, capture_output=True, timeout=30)

    def assert_work_cleaned(self):
        invocation = json.loads(self.call.read_text())
        work = Path(invocation["timing"]).parent
        self.assertEqual(work.parent, self.cache)
        self.assertTrue(work.name.startswith("local_work_"))
        self.assertFalse(work.exists())
        self.assertEqual(list(self.cache.glob("local_work_*")), [])

    def test_local_video_asr_timing_survives_work_cleanup(self):
        completed = self.transcribe()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        note = self.output / "result.md"
        self.assertIn(f"GENERATED_MARKDOWN_PATH:{note}", completed.stdout)
        self.assertIn("音频已提取", completed.stdout)
        self.assertIn(SEGMENTS[0]["text"], note.read_text())
        sidecar = note.with_name(f".{note.name}.lns-timing.json")
        self.assertEqual(json.loads(sidecar.read_text())["segments"], SEGMENTS)
        identity = hashlib.sha256(str(note.resolve()).encode()).hexdigest()
        private_cache = self.transcripts / "timing-by-note" / f"{identity}.json"
        self.assertEqual(json.loads(private_cache.read_text())["segments"], SEGMENTS)
        self.assert_work_cleaned()

    def test_timing_attachment_failure_fails_item_and_cleans_work(self):
        self.env["MOCK_INVALID_TIMING"] = "1"
        completed = self.transcribe()
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("GENERATED_MARKDOWN_PATH:", completed.stdout)
        self.assertFalse((self.output / ".result.md.lns-timing.json").exists())
        self.assertFalse((self.transcripts / "timing-by-note").exists())
        self.assert_work_cleaned()

    def test_markdown_write_failure_fails_item_and_cleans_work(self):
        (self.output / "result.md").mkdir(parents=True)
        completed = self.transcribe()
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("GENERATED_MARKDOWN_PATH:", completed.stdout)
        self.assertFalse((self.transcripts / "timing-by-note").exists())
        self.assert_work_cleaned()


if __name__ == "__main__":
    unittest.main()
