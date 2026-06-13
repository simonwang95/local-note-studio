#!/usr/bin/env python3
"""Command worker for Local Note Studio MVP tasks."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "worker"
SCRIPTS_DIR = WORKER_DIR / "scripts"


REQUIRED_PYTHON_PACKAGES = {
    "pypdf": "pip install pypdf",
    "lxml": "pip install lxml",
    "requests": "pip install requests",
}

REQUIRED_COMMANDS = {
    "yt-dlp": "pip install yt-dlp",
    "ffmpeg": "Install ffmpeg with Homebrew (`brew install ffmpeg`) or conda.",
}

OPTIONAL_PYTHON_PACKAGES = {
    "mlx_whisper": "pip install mlx-whisper",
}

OPTIONAL_COMMANDS = {
    "opencc": "Install opencc if you want traditional-to-simplified conversion.",
}

ASR_MODEL_HINT = "Set ASR_LOCAL_MODEL in worker/env.local when videos have no usable subtitles."


@dataclass
class TaskRequest:
    task: str
    source: str = ""
    output_dir: str = ""
    conda_env: str = ""
    python_bin: str = "python3"
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    cookies: str = ""
    subtitle_strategy: str = "yt-dlp"
    favorite_limit: int = 1
    dry_run: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TaskRequest":
        return cls(
            task=str(data.get("task") or ""),
            source=str(data.get("source") or ""),
            output_dir=str(data.get("output_dir") or ""),
            conda_env=str(data.get("conda_env") or ""),
            python_bin=str(data.get("python_bin") or "python3"),
            api_base=str(data.get("api_base") or ""),
            api_key=str(data.get("api_key") or ""),
            model=str(data.get("model") or ""),
            cookies=str(data.get("cookies") or ""),
            subtitle_strategy=str(data.get("subtitle_strategy") or "yt-dlp"),
            favorite_limit=parse_int(data.get("favorite_limit"), 1),
            dry_run=bool(data.get("dry_run")),
        )


def load_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def build_env(req: TaskRequest) -> dict[str, str]:
    env = os.environ.copy()
    env.update(load_env_file(WORKER_DIR / "env.local"))
    env["PYTHONUNBUFFERED"] = "1"
    if req.conda_env:
        env["CONDA_ENV"] = req.conda_env
    if req.api_base:
        env["DEFAULT_LLM_API_BASE"] = req.api_base
        env["SUMMARY_API_URL"] = req.api_base
    if req.api_key:
        env["DEFAULT_LLM_API_KEY"] = req.api_key
        env["SUMMARY_API_KEY"] = req.api_key
    if req.model:
        env["DEFAULT_LLM_MODEL"] = req.model
        env["SUMMARY_MODEL"] = req.model
    if req.cookies:
        env["BILIBILI_COOKIES_FILE"] = req.cookies
        env["BILI_COOKIE_FILE"] = req.cookies
    if req.output_dir:
        env["BILIBILI_OUTPUT_DIR"] = req.output_dir
    subtitle_strategy = (req.subtitle_strategy or "yt-dlp").strip().lower()
    if subtitle_strategy == "web":
        env["BILIBILI_PREFER_WEB_SUBTITLE"] = "true"
        env["FORCE_ASR"] = "false"
    elif subtitle_strategy == "asr":
        env["BILIBILI_PREFER_WEB_SUBTITLE"] = "false"
        env["FORCE_ASR"] = "true"
    else:
        env["BILIBILI_PREFER_WEB_SUBTITLE"] = "false"
        env["FORCE_ASR"] = "false"
    return env


def python_cmd(req: TaskRequest, script: pathlib.Path) -> list[str]:
    if req.conda_env:
        return ["conda", "run", "--no-capture-output", "-n", req.conda_env, "python3", "-u", str(script)]
    return [req.python_bin or "python3", "-u", str(script)]


def python_eval_cmd(req: TaskRequest, code: str) -> list[str]:
    if req.conda_env:
        return ["conda", "run", "--no-capture-output", "-n", req.conda_env, "python3", "-c", code]
    return [req.python_bin or "python3", "-c", code]


def tool_cmd(req: TaskRequest, tool: str, *args: str) -> list[str]:
    if req.conda_env:
        return ["conda", "run", "--no-capture-output", "-n", req.conda_env, tool, *args]
    return [tool, *args]


def probe(command: list[str], env: dict[str, str], timeout: int = 20) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            cwd=str(WORKER_DIR),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    output = (result.stdout or "").strip()
    return result.returncode == 0, output


def first_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if lines[0].startswith("Traceback") and len(lines) > 1:
        return lines[-1]
    return lines[0]


def status_line(ok: bool, label: str, detail: str = "", hint: str = "", required: bool = True) -> str:
    status = "[OK]" if ok else ("[MISSING]" if required else "[WARN]")
    parts = [f"{status} {label}"]
    if detail:
        parts.append(f"- {detail}")
    if not ok and hint:
        parts.append(f"Hint: {hint}")
    return " ".join(parts)


def check_environment(req: TaskRequest, env: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append("Local Note Studio environment check")
    lines.append("")
    lines.append("Selected runtime")
    if req.conda_env:
        lines.append(f"- conda environment: {req.conda_env}")
    else:
        lines.append(f"- Python command: {req.python_bin or 'python3'}")
    lines.append(f"- LLM API base: {req.api_base or env.get('DEFAULT_LLM_API_BASE', '(not set)')}")
    lines.append(f"- model: {req.model or env.get('DEFAULT_LLM_MODEL', '(not set)')}")
    lines.append("")

    required_ok = True
    warning_count = 0

    ok, output = probe(
        python_eval_cmd(
            req,
            "import sys; print(sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 2)",
        ),
        env,
    )
    required_ok = required_ok and ok
    lines.append(
        status_line(
            ok,
            "Python runtime",
            first_line(output),
            "Install Python 3.10/3.11 or choose a conda environment that contains Python.",
        )
    )

    for package, hint in REQUIRED_PYTHON_PACKAGES.items():
        ok, output = probe(python_eval_cmd(req, f"import {package}; print('import ok')"), env)
        required_ok = required_ok and ok
        lines.append(status_line(ok, f"Python package `{package}`", first_line(output), hint))

    for command, hint in REQUIRED_COMMANDS.items():
        version_args = ("-version",) if command == "ffmpeg" else ("--version",)
        ok, output = probe(tool_cmd(req, command, *version_args), env)
        required_ok = required_ok and ok
        lines.append(status_line(ok, f"Command `{command}`", first_line(output), hint))

    for package, hint in OPTIONAL_PYTHON_PACKAGES.items():
        ok, output = probe(python_eval_cmd(req, f"import {package}; print('import ok')"), env)
        if not ok:
            warning_count += 1
        lines.append(status_line(ok, f"Optional Python package `{package}`", first_line(output), hint, required=False))

    for command, hint in OPTIONAL_COMMANDS.items():
        ok, output = probe(tool_cmd(req, command, "--version"), env)
        if not ok:
            warning_count += 1
        lines.append(status_line(ok, f"Optional command `{command}`", first_line(output), hint, required=False))

    textutil = "/usr/bin/textutil" if pathlib.Path("/usr/bin/textutil").exists() else "textutil"
    ok, output = probe([textutil, "-help"], env)
    if not ok:
        warning_count += 1
    lines.append(
        status_line(
            ok,
            "Optional command `textutil`",
            "available for legacy .doc conversion" if ok else first_line(output),
            "macOS textutil is required when converting legacy .doc files.",
            required=False,
        )
    )

    lines.append("")
    lines.append("Configuration checks")
    api_base = req.api_base or env.get("DEFAULT_LLM_API_BASE", "")
    api_key = req.api_key or env.get("DEFAULT_LLM_API_KEY", "")
    model = req.model or env.get("DEFAULT_LLM_MODEL", "")
    for label, value, hint in [
        ("LLM API base", api_base, "Set an OpenAI-compatible API base such as http://127.0.0.1:1234/v1."),
        ("LLM API key", api_key, "Set an API key. LM Studio can use a placeholder such as lm-studio."),
        ("LLM model", model, "Set the model name served by your OpenAI-compatible endpoint."),
    ]:
        ok = bool(value)
        required_ok = required_ok and ok
        lines.append(status_line(ok, label, value if ok and label != "LLM API key" else ("set" if ok else ""), hint))

    if req.cookies:
        cookie_path = pathlib.Path(req.cookies).expanduser()
        ok = cookie_path.exists()
        if not ok:
            warning_count += 1
        lines.append(
            status_line(
                ok,
                "Bilibili cookie file",
                str(cookie_path) if ok else "",
                "Export a Netscape cookies.txt file and set its absolute path.",
                required=False,
            )
        )
    else:
        warning_count += 1
        lines.append(
            status_line(
                False,
                "Bilibili cookie file",
                "",
                "Optional for public videos, usually required for private favorites or restricted subtitles.",
                required=False,
            )
        )

    if req.output_dir:
        output_path = pathlib.Path(req.output_dir).expanduser()
        exists_or_parent = output_path.exists() or output_path.parent.exists()
        if not exists_or_parent:
            warning_count += 1
        lines.append(
            status_line(
                exists_or_parent,
                "Default output path",
                str(output_path),
                "Choose an existing notes root or a path whose parent directory exists.",
                required=False,
            )
        )

    asr_engine = (env.get("ASR_ENGINE") or "whisper").strip().lower()
    asr_model = (env.get("ASR_LOCAL_MODEL") or "").strip()
    if asr_engine == "whisper":
        if asr_model:
            asr_model_path = pathlib.Path(asr_model).expanduser()
            if not asr_model_path.is_absolute():
                asr_model_path = WORKER_DIR / asr_model_path
            ok = asr_model_path.exists()
            if not ok:
                warning_count += 1
            lines.append(
                status_line(
                    ok,
                    "ASR local model",
                    str(asr_model_path),
                    "Set ASR_LOCAL_MODEL to an existing local Whisper model directory.",
                    required=False,
                )
            )
        else:
            warning_count += 1
            lines.append(status_line(False, "ASR local model", "", ASR_MODEL_HINT, required=False))
    elif asr_engine == "qwen3":
        ok, output = probe(python_eval_cmd(req, "import qwen_asr; print('import ok')"), env)
        if not ok:
            warning_count += 1
        lines.append(status_line(ok, "Qwen3-ASR package", first_line(output), "pip install qwen-asr", required=False))
    else:
        warning_count += 1
        lines.append(status_line(False, "ASR engine", asr_engine, "Use ASR_ENGINE=whisper or ASR_ENGINE=qwen3.", required=False))

    lines.append("")
    if required_ok:
        if warning_count:
            lines.append(f"Result: required dependencies are ready, with {warning_count} warning(s).")
        else:
            lines.append("Result: environment looks ready.")
    else:
        lines.append("Result: required dependencies are missing. Fix the [MISSING] items before running tasks.")
    lines.append("")
    lines.append("Common setup hints")
    if req.conda_env:
        lines.append(f"- conda install -n {req.conda_env} -c conda-forge ffmpeg")
        lines.append(f"- conda run -n {req.conda_env} python3 -m pip install pypdf lxml requests yt-dlp mlx-whisper")
    else:
        python = req.python_bin or "python3"
        lines.append(f"- {python} -m pip install pypdf lxml requests yt-dlp mlx-whisper")
        lines.append("- brew install ffmpeg")
    return "\n".join(lines) + "\n"


def command_for(req: TaskRequest) -> list[str]:
    if not req.task:
        raise ValueError("task is required")
    if req.task == "env-check":
        raise ValueError("env-check is handled internally")
    if not req.source and req.task not in {"bilibili-favorite"}:
        raise ValueError("source is required")
    if not req.output_dir:
        raise ValueError("output_dir is required")

    if req.task == "bilibili-url":
        return [
            *python_cmd(req, SCRIPTS_DIR / "run_bilibili_transcript.py"),
            "--url",
            req.source,
        ]
    if req.task == "bilibili-favorite":
        command = [
            *python_cmd(req, SCRIPTS_DIR / "run_bilibili_transcript.py"),
            "--favorite",
        ]
        if req.favorite_limit > 0:
            command.extend(["--limit", str(req.favorite_limit)])
        return command
    if req.task == "local-video":
        source_path = pathlib.Path(req.source)
        args = ["--local-dir" if source_path.is_dir() else "--local-file", req.source]
        return [*python_cmd(req, SCRIPTS_DIR / "run_bilibili_transcript.py"), *args]
    if req.task == "web-url":
        return [
            *python_cmd(req, SCRIPTS_DIR / "convert_sources_to_md.py"),
            "--url",
            req.source,
            "--output-dir",
            req.output_dir,
            "--overwrite",
        ]
    if req.task == "source-file":
        return [
            *python_cmd(req, SCRIPTS_DIR / "convert_sources_to_md.py"),
            "--source",
            req.source,
            "--output-dir",
            req.output_dir,
            "--overwrite",
        ]
    if req.task == "paper-quickread":
        return [
            *python_cmd(req, SCRIPTS_DIR / "quick_read_pdf.py"),
            "--source",
            req.source,
            "--output-dir",
            req.output_dir,
            "--overwrite",
        ]
    raise ValueError(f"unsupported task: {req.task}")


def run_command(command: list[str], env: dict[str, str], dry_run: bool) -> str:
    if dry_run:
        return render_command(command) + "\n"
    run_process(command, env)
    return ""


def run_convert_and_organize_task(req: TaskRequest, env: dict[str, str]) -> str:
    convert_command = command_for(req)
    if req.dry_run:
        organize_preview = [
            *python_cmd(req, SCRIPTS_DIR / "qwen_organize_notes.py"),
            "--source",
            "<converted-markdown-path>",
            "--output-dir",
            req.output_dir,
            "--overwrite",
        ]
        return "\n".join(
            [
                render_command(convert_command),
                "",
                "then organize converted Markdown with Qwen:",
                render_command(organize_preview),
            ]
        ) + "\n"

    output = run_process(convert_command, env)
    converted_paths = extract_converted_paths(output)
    if not converted_paths:
        print("未从转换输出中识别到 Markdown 路径，跳过 Qwen 整理。", file=sys.stderr)
        return ""

    organize_command = [
        *python_cmd(req, SCRIPTS_DIR / "qwen_organize_notes.py"),
        "--source",
        converted_paths[-1],
        "--output-dir",
        req.output_dir,
        "--overwrite",
    ]
    print("")
    print("organize:", render_command(organize_command))
    run_process(organize_command, env)
    return ""


def render_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_process(command: list[str], env: dict[str, str]) -> str:
    process = subprocess.Popen(
        command,
        cwd=str(WORKER_DIR),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    lines: list[str] = []
    if process.stdout:
        for line in process.stdout:
            lines.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
    returncode = process.wait()
    output = "".join(lines)
    if returncode != 0:
        raise RuntimeError(f"command failed ({returncode}):\n{render_command(command)}\n\n{output}")
    return output


def extract_converted_paths(output: str) -> list[str]:
    paths: list[str] = []
    seen = set()
    for line in output.splitlines():
        match = re.search(r"\bconverted\s+.+?\s+->\s+(.+\.md)$", line.strip())
        if not match:
            continue
        path = match.group(1).strip()
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", help="Task request JSON from the desktop app.")
    parser.add_argument("--task", help="Task type.")
    parser.add_argument("--source", default="", help="URL or file path.")
    parser.add_argument("--output-dir", default="", help="Markdown output directory.")
    parser.add_argument("--conda-env", default="", help="Existing conda environment to use.")
    parser.add_argument("--python-bin", default="python3", help="Python command when conda is not used.")
    parser.add_argument("--api-base", default="", help="OpenAI-compatible API base.")
    parser.add_argument("--api-key", default="", help="OpenAI-compatible API key.")
    parser.add_argument("--model", default="", help="LLM model name.")
    parser.add_argument("--cookies", default="", help="Bilibili Netscape cookies.txt path.")
    parser.add_argument(
        "--subtitle-strategy",
        default="yt-dlp",
        choices=["yt-dlp", "web", "asr"],
        help="Preferred Bilibili transcript source.",
    )
    parser.add_argument("--favorite-limit", type=int, default=1, help="Maximum videos to process in favorite mode. Use 0 for full run.")
    parser.add_argument("--dry-run", action="store_true", help="Print command without running.")
    return parser.parse_args(argv)


def request_from_args(args: argparse.Namespace) -> TaskRequest:
    if args.request_json:
        return TaskRequest.from_mapping(json.loads(args.request_json))
    return TaskRequest(
        task=args.task or "",
        source=args.source,
        output_dir=args.output_dir,
        conda_env=args.conda_env,
        python_bin=args.python_bin,
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        cookies=args.cookies,
        subtitle_strategy=args.subtitle_strategy,
        favorite_limit=args.favorite_limit,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    req = request_from_args(args)
    env = build_env(req)
    if req.task == "env-check":
        sys.stdout.write(check_environment(req, env))
        return 0
    if req.task in {"web-url", "source-file"}:
        sys.stdout.write(run_convert_and_organize_task(req, env))
        return 0
    command = command_for(req)
    sys.stdout.write(run_command(command, env, req.dry_run))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
