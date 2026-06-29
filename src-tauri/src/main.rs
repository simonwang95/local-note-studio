use serde::Serialize;
use serde_json::Value;
use std::fs;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::thread;
use std::time::Duration;
use tauri::{Emitter, Manager, State};

struct WorkerState {
    child: Mutex<Option<Child>>,
    cancel_requested: AtomicBool,
}

#[derive(Clone, Serialize)]
struct WorkerLogPayload {
    line: String,
}

const MANAGED_RUNTIME_VERSION: &str = "2026.06-py311";
const PYTHON_STANDALONE_TAG: &str = "20260610";
const MANAGED_ASR_MODEL_REPO: &str = "mlx-community/whisper-large-v3-turbo";
const MANAGED_ASR_MODEL_DIR: &str = "whisper-large-v3-turbo";
const MANAGED_PIP_FALLBACK_INDEXES: &[(&str, &str)] = &[
    ("清华 PyPI 镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里云 PyPI 镜像", "https://mirrors.aliyun.com/pypi/simple"),
];

#[tauri::command]
async fn run_worker(app: tauri::AppHandle, request: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_worker_blocking(app, request, false))
        .await
        .map_err(|err| err.to_string())?
}

#[tauri::command]
async fn run_worker_stream(
    app: tauri::AppHandle,
    state: State<'_, Arc<WorkerState>>,
    request: String,
) -> Result<String, String> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || run_worker_streaming(app, state, request))
        .await
        .map_err(|err| err.to_string())?
}

#[tauri::command]
fn cancel_worker(
    app: tauri::AppHandle,
    state: State<'_, Arc<WorkerState>>,
) -> Result<bool, String> {
    if state.child.lock().map_err(|err| err.to_string())?.is_some() {
        emit_log(&app, "Cancel requested. Stopping current worker...\n");
    }
    stop_worker(&state)
}

#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    if !target.exists() {
        return Err(format!("Path does not exist: {}", target.display()));
    }
    Command::new("open")
        .arg(&target)
        .status()
        .map_err(|err| err.to_string())?
        .success()
        .then_some(())
        .ok_or_else(|| format!("Failed to open {}", target.display()))
}

#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    if !target.exists() {
        return Err(format!("Path does not exist: {}", target.display()));
    }
    Command::new("open")
        .arg("-R")
        .arg(&target)
        .status()
        .map_err(|err| err.to_string())?
        .success()
        .then_some(())
        .ok_or_else(|| format!("Failed to reveal {}", target.display()))
}

#[tauri::command]
async fn manage_runtime(app: tauri::AppHandle, action: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || manage_runtime_blocking(&app, &action))
        .await
        .map_err(|err| err.to_string())?
}

fn stop_worker(state: &WorkerState) -> Result<bool, String> {
    state.cancel_requested.store(true, Ordering::SeqCst);
    let mut child_guard = state.child.lock().map_err(|err| err.to_string())?;
    match child_guard.as_mut() {
        Some(child) => {
            terminate_child(child)?;
            Ok(true)
        }
        None => Ok(false),
    }
}

fn terminate_child(child: &mut Child) -> Result<(), String> {
    #[cfg(unix)]
    {
        let process_group = format!("-{}", child.id());
        let _ = Command::new("kill")
            .args(["-TERM", &process_group])
            .status();
        for _ in 0..20 {
            if child.try_wait().map_err(|err| err.to_string())?.is_some() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(50));
        }
    }
    child.kill().map_err(|err| err.to_string())?;
    Ok(())
}

fn configure_child_lifecycle(command: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
}

fn run_worker_blocking(
    app: tauri::AppHandle,
    request: String,
    emit_logs: bool,
) -> Result<String, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let worker = resolve_worker_path(resource_dir)?;
    let python = resolve_python(&app, &request)?;
    let mut command = Command::new(python);
    configure_worker_command(&app, &mut command, &request)?;
    configure_child_lifecycle(&mut command);
    let output = command
        .arg(worker)
        .arg("--request-json")
        .arg(request)
        .output()
        .map_err(|err| err.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let text = format!("{}{}", stdout, stderr);
    if emit_logs && !text.is_empty() {
        emit_log(&app, &text);
    }
    if output.status.success() {
        Ok(stdout.to_string())
    } else {
        Err(text)
    }
}

fn run_worker_streaming(
    app: tauri::AppHandle,
    state: Arc<WorkerState>,
    request: String,
) -> Result<String, String> {
    state.cancel_requested.store(false, Ordering::SeqCst);
    {
        let child_guard = state.child.lock().map_err(|err| err.to_string())?;
        if child_guard.is_some() {
            return Err("Another worker task is already running.".to_string());
        }
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let worker = resolve_worker_path(resource_dir)?;
    let python = resolve_python(&app, &request)?;
    let mut command = Command::new(python);
    configure_worker_command(&app, &mut command, &request)?;
    configure_child_lifecycle(&mut command);
    let mut child = command
        .arg(worker)
        .arg("--request-json")
        .arg(request)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| err.to_string())?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    {
        let mut child_guard = state.child.lock().map_err(|err| err.to_string())?;
        *child_guard = Some(child);
    }

    let collected = Arc::new(Mutex::new(String::new()));
    let stdout_handle =
        stdout.map(|stream| spawn_log_reader(app.clone(), collected.clone(), stream));
    let stderr_handle =
        stderr.map(|stream| spawn_log_reader(app.clone(), collected.clone(), stream));

    let status = loop {
        let maybe_status = {
            let mut child_guard = state.child.lock().map_err(|err| err.to_string())?;
            match child_guard.as_mut() {
                Some(child) => child.try_wait().map_err(|err| err.to_string())?,
                None => None,
            }
        };
        if let Some(status) = maybe_status {
            let mut child_guard = state.child.lock().map_err(|err| err.to_string())?;
            child_guard.take();
            break status;
        }
        thread::sleep(Duration::from_millis(100));
    };

    if let Some(handle) = stdout_handle {
        let _ = handle.join();
    }
    if let Some(handle) = stderr_handle {
        let _ = handle.join();
    }

    let output = collected.lock().map_err(|err| err.to_string())?.clone();
    if status.success() {
        Ok(output)
    } else if state.cancel_requested.load(Ordering::SeqCst) {
        Err("Task cancelled. See log above.".to_string())
    } else {
        Err(format!("worker failed ({status}). See log above."))
    }
}

fn spawn_log_reader<R: Read + Send + 'static>(
    app: tauri::AppHandle,
    collected: Arc<Mutex<String>>,
    stream: R,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let reader = BufReader::new(stream);
        for line in reader.lines() {
            let mut line = match line {
                Ok(line) => line,
                Err(err) => format!("log read error: {err}"),
            };
            line.push('\n');
            if let Ok(mut text) = collected.lock() {
                text.push_str(&line);
            }
            emit_log(&app, &line);
        }
    })
}

fn emit_log(app: &tauri::AppHandle, line: &str) {
    let _ = app.emit(
        "worker-log",
        WorkerLogPayload {
            line: line.to_string(),
        },
    );
}

fn app_data_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    #[cfg(target_os = "macos")]
    {
        return app
            .path()
            .home_dir()
            .map(|home| home.join("Library/Application Support/Local Note Studio"))
            .map_err(|err| err.to_string());
    }
    #[allow(unreachable_code)]
    app.path().app_data_dir().map_err(|err| err.to_string())
}

fn request_string(request: &str, key: &str) -> Option<String> {
    serde_json::from_str::<Value>(request)
        .ok()
        .and_then(|value| {
            value
                .get(key)
                .and_then(Value::as_str)
                .map(str::trim)
                .map(str::to_owned)
        })
        .filter(|value| !value.is_empty())
}

fn request_runtime_backend(request: &str) -> String {
    request_string(request, "runtime_backend").unwrap_or_else(|| "managed".to_string())
}

fn expand_home_path(value: &str, home: &Path) -> PathBuf {
    value
        .strip_prefix("~/")
        .map(|suffix| home.join(suffix))
        .unwrap_or_else(|| PathBuf::from(value))
}

fn conda_bin_directories(home: &Path) -> Vec<PathBuf> {
    vec![
        home.join("miniforge3/bin"),
        home.join("miniconda3/bin"),
        home.join("anaconda3/bin"),
        home.join("mambaforge/bin"),
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/opt/anaconda3/bin"),
        PathBuf::from("/opt/miniconda3/bin"),
    ]
}

fn find_conda_executable(home: &Path, configured: Option<&str>) -> Option<PathBuf> {
    if let Some(value) = configured {
        return Some(expand_home_path(value, home));
    }
    conda_bin_directories(home)
        .into_iter()
        .map(|directory| directory.join("conda"))
        .find(|candidate| candidate.exists())
}

fn push_unique_path(paths: &mut Vec<PathBuf>, path: PathBuf) {
    if !paths.contains(&path) {
        paths.push(path);
    }
}

fn configure_worker_command(
    app: &tauri::AppHandle,
    command: &mut Command,
    request: &str,
) -> Result<(), String> {
    let root = app_data_root(app)?;
    let home = app.path().home_dir().map_err(|err| err.to_string())?;
    let state_dir = root.join("state");
    let index_dir = state_dir.join("indexes");
    fs::create_dir_all(&index_dir).map_err(|err| err.to_string())?;
    command
        .env("LOCAL_NOTE_STUDIO_APP_DATA_DIR", &root)
        .env("LOCAL_NOTE_STUDIO_STATE_DIR", &state_dir)
        .env("INDEX_DIR", &index_dir)
        .env("BILIBILI_STATE_DIR", index_dir.join("bilibili-state"))
        .env("OCR_CHECKPOINT_DIR", state_dir.join("ocr-checkpoints"));
    let configured_conda = request_string(request, "conda_bin");
    let conda = find_conda_executable(&home, configured_conda.as_deref());
    if let Some(path) = &conda {
        command.env("CONDA_EXE", path);
    }

    let mut paths: Vec<PathBuf> = Vec::new();
    if let Some(parent) = conda.as_ref().and_then(|path| path.parent()) {
        push_unique_path(&mut paths, parent.to_path_buf());
    }
    let managed_bin = root.join("runtime/current/bin");
    if managed_bin.exists() {
        push_unique_path(&mut paths, managed_bin);
    }
    for path in conda_bin_directories(&home) {
        push_unique_path(&mut paths, path);
    }
    for path in ["/usr/bin", "/bin", "/usr/sbin", "/sbin"] {
        push_unique_path(&mut paths, PathBuf::from(path));
    }
    let inherited = std::env::var_os("PATH").unwrap_or_default();
    for path in std::env::split_paths(&inherited) {
        push_unique_path(&mut paths, path);
    }
    if let Ok(joined) = std::env::join_paths(paths) {
        command.env("PATH", joined);
    }
    Ok(())
}

fn resolve_python(app: &tauri::AppHandle, request: &str) -> Result<PathBuf, String> {
    let backend = request_runtime_backend(request);
    if backend == "managed" {
        let root = app_data_root(app)?;
        let python = root.join("runtime/current/bin/python3");
        if !python.exists() {
            return Err("应用托管环境尚未安装；请先点击“安装/修复”。".to_string());
        }
        let task = serde_json::from_str::<Value>(request)
            .ok()
            .and_then(|value| value.get("task").and_then(Value::as_str).map(str::to_owned))
            .unwrap_or_default();
        if task == "epub-export" {
            install_managed_pandoc(&root.join("runtime"), &root.join("runtime/current/bin"))?;
        }
        return Ok(python);
    }
    let home = app.path().home_dir().map_err(|err| err.to_string())?;
    let requested_python = std::env::var("LOCAL_NOTE_STUDIO_PYTHON")
        .ok()
        .or_else(|| request_string(request, "python_bin"))
        .unwrap_or_else(|| "python3".to_string());
    if requested_python == "python3" {
        let configured_conda = request_string(request, "conda_bin");
        if let Some(conda) = find_conda_executable(&home, configured_conda.as_deref()) {
            if let Some(parent) = conda.parent() {
                let candidate = parent.join("python3");
                if candidate.exists() {
                    return Ok(candidate);
                }
            }
        }
    }
    Ok(expand_home_path(&requested_python, &home))
}

fn manage_runtime_blocking(app: &tauri::AppHandle, action: &str) -> Result<String, String> {
    let root = app_data_root(app)?;
    let runtime_root = root.join("runtime");
    let current = runtime_root.join("current");
    if action == "status" {
        return Ok(runtime_status_text(&root));
    }
    if action == "remove" {
        if runtime_root.exists() {
            fs::remove_dir_all(&runtime_root).map_err(|err| err.to_string())?;
        }
        return Ok(format!(
            "托管环境已移除：{}\n用户笔记、任务历史和模型目录未删除。\n",
            runtime_root.display()
        ));
    }
    if action != "install" {
        return Err(format!("Unsupported runtime action: {action}"));
    }

    emit_log(app, "托管环境安装/修复开始。\n");
    let resource_dir = app
        .path()
        .resource_dir()
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let worker = resolve_worker_path(resource_dir)?;
    let requirements = worker
        .parent()
        .ok_or_else(|| "Cannot resolve worker directory".to_string())?
        .join("requirements-managed.lock");
    let version_dir = runtime_root.join("versions").join(MANAGED_RUNTIME_VERSION);
    fs::create_dir_all(version_dir.parent().unwrap_or(&runtime_root))
        .map_err(|err| err.to_string())?;

    if !version_dir.join("bin/python3").exists() {
        emit_log(app, "正在下载并安装 Python 运行时...\n");
        install_standalone_python(&runtime_root, &version_dir)?;
    } else {
        emit_log(app, "Python 运行时已存在，跳过下载。\n");
    }
    emit_log(app, "正在初始化 pip...\n");
    let ensurepip = Command::new(version_dir.join("bin/python3"))
        .args(["-m", "ensurepip", "--upgrade"])
        .output()
        .map_err(|err| err.to_string())?;
    if !ensurepip.status.success() {
        return Err(String::from_utf8_lossy(&ensurepip.stderr).to_string());
    }
    emit_log(
        app,
        "正在安装/修复 Python 依赖（含 yt-dlp 与 mlx-whisper）...\n",
    );
    install_managed_python_packages(&version_dir.join("bin/python3"), &requirements)?;
    emit_log(app, "正在安装/修复 ffmpeg 与 ffprobe...\n");
    install_managed_media_tools(&runtime_root, &version_dir.join("bin"))?;
    emit_log(app, "正在安装/修复 pandoc（EPUB 导出组件）...\n");
    if let Err(err) = install_managed_pandoc(&runtime_root, &version_dir.join("bin")) {
        emit_log(
            app,
            &format!("pandoc 安装失败，已继续初始化；EPUB 导出暂不可用，可稍后重试“安装/修复”。\n{err}\n"),
        );
    }
    fs::create_dir_all(root.join("models")).map_err(|err| err.to_string())?;
    emit_log(app, "正在下载/修复默认 Whisper ASR 模型...\n");
    install_managed_asr_model(&version_dir.join("bin/python3"), &root)?;

    if current.exists() {
        fs::remove_file(&current)
            .or_else(|_| fs::remove_dir_all(&current))
            .map_err(|err| err.to_string())?;
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(&version_dir, &current).map_err(|err| err.to_string())?;
    fs::create_dir_all(root.join("tools")).map_err(|err| err.to_string())?;
    fs::create_dir_all(root.join("state")).map_err(|err| err.to_string())?;
    fs::write(
        root.join("state/runtime-version.json"),
        format!("{{\"version\":\"{MANAGED_RUNTIME_VERSION}\",\"active\":true}}\n"),
    )
    .map_err(|err| err.to_string())?;
    emit_log(app, "托管环境安装/修复完成。\n");
    Ok(runtime_status_text(&root))
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct PipIndexAttempt {
    label: String,
    index_url: Option<String>,
}

fn managed_pip_index_attempts() -> Vec<PipIndexAttempt> {
    let mut attempts = Vec::new();
    if let Ok(value) = std::env::var("LOCAL_NOTE_STUDIO_PIP_INDEX_URL") {
        let value = value.trim();
        if !value.is_empty() {
            attempts.push(PipIndexAttempt {
                label: "自定义 PyPI 源".to_string(),
                index_url: Some(value.to_string()),
            });
        }
    }
    attempts.push(PipIndexAttempt {
        label: "默认 PyPI 源".to_string(),
        index_url: None,
    });
    for (label, url) in MANAGED_PIP_FALLBACK_INDEXES {
        attempts.push(PipIndexAttempt {
            label: (*label).to_string(),
            index_url: Some((*url).to_string()),
        });
    }
    attempts
}

fn install_managed_python_packages(python: &Path, requirements: &Path) -> Result<(), String> {
    let mut attempt_logs = Vec::new();
    for attempt in managed_pip_index_attempts() {
        let mut command = Command::new(python);
        command
            .args([
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--prefer-binary",
                "--retries",
                "8",
                "--timeout",
                "45",
                "-r",
            ])
            .arg(requirements)
            .env("PIP_DISABLE_PIP_VERSION_CHECK", "1")
            .env("PIP_NO_INPUT", "1");
        if let Some(index_url) = &attempt.index_url {
            command.args(["--index-url", index_url]);
        }
        let output = command.output().map_err(|err| err.to_string())?;
        let text = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        if output.status.success() {
            return Ok(());
        }
        attempt_logs.push(format!("--- {} ---\n{}", attempt.label, text.trim()));
        if attempt.index_url.is_none() && !pip_failure_looks_network_related(&text) {
            break;
        }
    }

    Err(format!(
        "托管依赖安装失败：\n{}\n\n排查建议：当前失败通常是网络、代理或 TLS/证书链路问题，而不是依赖版本不存在。请换一个网络，关闭会拦截 HTTPS 的代理/安全软件，或在终端启动前设置 LOCAL_NOTE_STUDIO_PIP_INDEX_URL 指向可访问的 PyPI 镜像后重试“安装/修复”。",
        attempt_logs.join("\n\n")
    ))
}

fn pip_failure_looks_network_related(text: &str) -> bool {
    let lower = text.to_lowercase();
    [
        "could not fetch url",
        "ssl",
        "ssleoferror",
        "certificate",
        "connection",
        "connectionpool",
        "timed out",
        "timeout",
        "temporary failure",
        "proxy",
        "network is unreachable",
        "name or service not known",
    ]
    .iter()
    .any(|needle| lower.contains(needle))
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct CurlDownloadAttempt {
    label: &'static str,
    args: &'static [&'static str],
}

fn curl_download_attempts() -> Vec<CurlDownloadAttempt> {
    vec![
        CurlDownloadAttempt {
            label: "HTTP/1.1",
            args: &["--http1.1"],
        },
        CurlDownloadAttempt {
            label: "默认协议",
            args: &[],
        },
    ]
}

fn download_with_curl(
    label: &str,
    url: &str,
    target: &Path,
    override_env: Option<&str>,
) -> Result<(), String> {
    let mut attempt_logs = Vec::new();
    for attempt in curl_download_attempts() {
        let _ = fs::remove_file(target);
        let mut command = Command::new("curl");
        command
            .args(attempt.args)
            .args([
                "-L",
                "--fail",
                "--silent",
                "--show-error",
                "--retry-all-errors",
                "--retry",
                "5",
                "--retry-delay",
                "2",
                "--connect-timeout",
                "30",
                "--max-time",
                "900",
                "-o",
            ])
            .arg(target)
            .arg(url);
        let output = command
            .output()
            .map_err(|err| format!("无法启动系统 curl：{err}"))?;
        let text = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        if output.status.success() {
            return Ok(());
        }
        attempt_logs.push(format!("--- curl {} ---\n{}", attempt.label, text.trim()));
    }
    let _ = fs::remove_file(target);
    let override_hint = override_env
        .map(|key| format!("也可以用 {key} 指定同文件镜像。"))
        .unwrap_or_default();
    Err(format!(
        "{label}下载失败：\n{}\n\n排查建议：当前失败通常是网络、代理、GitHub/CDN 可达性或 HTTP/2 链路问题。应用已优先使用 HTTP/1.1 并自动重试；如果仍失败，请换网络/代理后重试。{override_hint}",
        attempt_logs.join("\n\n"),
    ))
}

fn install_standalone_python(
    runtime_root: &std::path::Path,
    version_dir: &std::path::Path,
) -> Result<(), String> {
    let (arch, checksum) = match std::env::consts::ARCH {
        "aarch64" => (
            "aarch64",
            "8c56f1f59142e0f9f8861ad897bdfd97fd84403afa7b3d8b0f33b208ec471355",
        ),
        "x86_64" => (
            "x86_64",
            "8cd3878c656ba1698314cbcb65f78df4c37b7c8eabff958558115c6db11adb3d",
        ),
        other => return Err(format!("暂不支持的 Mac 架构：{other}")),
    };
    let file_name = format!(
        "cpython-3.11.15+{PYTHON_STANDALONE_TAG}-{arch}-apple-darwin-install_only_stripped.tar.gz"
    );
    let encoded_name = file_name.replace('+', "%2B");
    let default_url = format!(
        "https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_STANDALONE_TAG}/{encoded_name}"
    );
    let url = std::env::var("LOCAL_NOTE_STUDIO_PYTHON_RUNTIME_URL")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or(default_url);
    let downloads = runtime_root.join("downloads");
    let archive = downloads.join(&file_name);
    let staging = runtime_root.join(format!(".installing-{MANAGED_RUNTIME_VERSION}"));
    fs::create_dir_all(&downloads).map_err(|err| err.to_string())?;
    if staging.exists() {
        fs::remove_dir_all(&staging).map_err(|err| err.to_string())?;
    }
    fs::create_dir_all(&staging).map_err(|err| err.to_string())?;

    download_with_curl(
        "Python 运行时",
        &url,
        &archive,
        Some("LOCAL_NOTE_STUDIO_PYTHON_RUNTIME_URL"),
    )?;
    let digest = Command::new("shasum")
        .args(["-a", "256"])
        .arg(&archive)
        .output()
        .map_err(|err| err.to_string())?;
    let actual = String::from_utf8_lossy(&digest.stdout)
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_string();
    if !digest.status.success() || actual != checksum {
        let _ = fs::remove_file(&archive);
        return Err(format!(
            "Python 运行时 SHA-256 校验失败：期望 {checksum}，实际 {actual}"
        ));
    }
    let extract = Command::new("tar")
        .args(["-xzf"])
        .arg(&archive)
        .arg("-C")
        .arg(&staging)
        .output()
        .map_err(|err| err.to_string())?;
    if !extract.status.success() {
        return Err(format!(
            "Python 运行时解压失败：{}",
            String::from_utf8_lossy(&extract.stderr)
        ));
    }
    let extracted = staging.join("python");
    if !extracted.join("bin/python3").exists() {
        return Err("Python 运行时包结构异常：缺少 python/bin/python3".to_string());
    }
    if version_dir.exists() {
        fs::remove_dir_all(version_dir).map_err(|err| err.to_string())?;
    }
    fs::rename(&extracted, version_dir).map_err(|err| err.to_string())?;
    let _ = fs::remove_dir_all(&staging);
    Ok(())
}

fn install_managed_media_tools(
    runtime_root: &std::path::Path,
    bin_dir: &std::path::Path,
) -> Result<(), String> {
    let tools = [
        (
            "ffmpeg",
            "https://evermeet.cx/ffmpeg/ffmpeg-8.1.2.zip",
            "e91df72a1ee7c26606f90dd2dd4dcccc6a75140ff9ea6fdd50faae828b82ba69",
        ),
        (
            "ffprobe",
            "https://evermeet.cx/ffmpeg/ffprobe-8.1.2.zip",
            "399b93f0b9862f69767afa343e90c2f48d7e7958cadbb6deb76a012d0e3b7ce3",
        ),
    ];
    for (name, url, checksum) in tools {
        install_zip_tool(runtime_root, bin_dir, name, url, checksum)?;
    }
    Ok(())
}

fn install_managed_pandoc(
    runtime_root: &std::path::Path,
    bin_dir: &std::path::Path,
) -> Result<(), String> {
    let (url, checksum) = match std::env::consts::ARCH {
        "aarch64" => (
            "https://github.com/jgm/pandoc/releases/download/3.10/pandoc-3.10-arm64-macOS.zip",
            "d9cad01d96ae774a0dc8c8c45bb1ad3e4c5ff2cc2e24f45958f5f9b7974aee34",
        ),
        "x86_64" => (
            "https://github.com/jgm/pandoc/releases/download/3.10/pandoc-3.10-x86_64-macOS.zip",
            "6334f4d9af7c9e37e761dfad56fa5507685f6d29724ebf31c4be6d5c654a3161",
        ),
        other => return Err(format!("暂不支持的 Mac 架构：{other}")),
    };
    install_zip_tool(runtime_root, bin_dir, "pandoc", url, checksum)
}

fn managed_asr_model_repo() -> String {
    std::env::var("LOCAL_NOTE_STUDIO_ASR_MODEL_REPO")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| MANAGED_ASR_MODEL_REPO.to_string())
}

fn managed_asr_model_path(root: &Path) -> PathBuf {
    let repo = managed_asr_model_repo();
    let name = repo
        .rsplit('/')
        .next()
        .filter(|value| !value.is_empty())
        .unwrap_or(MANAGED_ASR_MODEL_DIR);
    root.join("models").join(name)
}

fn managed_asr_model_ready(path: &Path) -> bool {
    path.join("config.json").exists() && directory_contains_extension(path, "safetensors")
}

fn directory_contains_extension(path: &Path, extension: &str) -> bool {
    let Ok(entries) = fs::read_dir(path) else {
        return false;
    };
    for entry in entries.filter_map(Result::ok) {
        let child = entry.path();
        if child.is_dir() {
            if directory_contains_extension(&child, extension) {
                return true;
            }
        } else if child
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(extension))
        {
            return true;
        }
    }
    false
}

fn install_managed_asr_model(python: &Path, root: &Path) -> Result<(), String> {
    let repo = managed_asr_model_repo();
    let model_dir = managed_asr_model_path(root);
    if managed_asr_model_ready(&model_dir) {
        return Ok(());
    }
    fs::create_dir_all(root.join("models")).map_err(|err| err.to_string())?;
    let code = r#"
import sys
from huggingface_hub import snapshot_download

repo_id = sys.argv[1]
local_dir = sys.argv[2]
snapshot_download(
    repo_id=repo_id,
    local_dir=local_dir,
    local_dir_use_symlinks=False,
    resume_download=True,
)
print(local_dir)
"#;
    let model_dir_string = model_dir.to_string_lossy().to_string();
    let output = Command::new(python)
        .args(["-c", code, &repo, &model_dir_string])
        .env("HF_HOME", root.join("models").join(".hf-cache"))
        .env("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        .output()
        .map_err(|err| err.to_string())?;
    if !output.status.success() || !managed_asr_model_ready(&model_dir) {
        return Err(format!(
            "默认 Whisper ASR 模型下载失败：\n{}{}\n\n排查建议：请确认当前网络可访问 Hugging Face，或用 LOCAL_NOTE_STUDIO_ASR_MODEL_REPO 指定兼容的 MLX Whisper 模型仓库后重试“安装/修复”。",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    Ok(())
}

fn install_zip_tool(
    runtime_root: &std::path::Path,
    bin_dir: &std::path::Path,
    name: &str,
    url: &str,
    checksum: &str,
) -> Result<(), String> {
    let target = bin_dir.join(name);
    if target.exists() {
        return Ok(());
    }
    let downloads = runtime_root.join("downloads");
    let archive = downloads.join(format!("{name}.zip"));
    let staging = runtime_root.join(format!(".installing-{name}"));
    fs::create_dir_all(&downloads).map_err(|err| err.to_string())?;
    fs::create_dir_all(bin_dir).map_err(|err| err.to_string())?;
    if staging.exists() {
        fs::remove_dir_all(&staging).map_err(|err| err.to_string())?;
    }
    fs::create_dir_all(&staging).map_err(|err| err.to_string())?;
    let override_key = tool_url_override_key(name);
    let source_url = std::env::var(&override_key)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| url.to_string());
    download_with_curl(name, &source_url, &archive, Some(&override_key))?;
    let digest = Command::new("shasum")
        .args(["-a", "256"])
        .arg(&archive)
        .output()
        .map_err(|err| err.to_string())?;
    let actual = String::from_utf8_lossy(&digest.stdout)
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_string();
    if !digest.status.success() || actual != checksum {
        let _ = fs::remove_file(&archive);
        return Err(format!(
            "{name} SHA-256 校验失败：期望 {checksum}，实际 {actual}"
        ));
    }
    let extract = Command::new("ditto")
        .args(["-x", "-k"])
        .arg(&archive)
        .arg(&staging)
        .output()
        .map_err(|err| err.to_string())?;
    if !extract.status.success() {
        return Err(format!(
            "{name} 解压失败：{}",
            String::from_utf8_lossy(&extract.stderr)
        ));
    }
    let extracted =
        find_named_file(&staging, name).ok_or_else(|| format!("{name} 压缩包中缺少可执行文件"))?;
    fs::copy(&extracted, &target).map_err(|err| err.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&target, fs::Permissions::from_mode(0o755))
            .map_err(|err| err.to_string())?;
    }
    let _ = fs::remove_dir_all(&staging);
    Ok(())
}

fn tool_url_override_key(name: &str) -> String {
    format!(
        "LOCAL_NOTE_STUDIO_{}_URL",
        name.to_ascii_uppercase().replace('-', "_")
    )
}

fn find_named_file(root: &std::path::Path, name: &str) -> Option<PathBuf> {
    let entries = fs::read_dir(root).ok()?;
    for entry in entries.filter_map(Result::ok) {
        let path = entry.path();
        if path.is_file() && path.file_name().and_then(|value| value.to_str()) == Some(name) {
            return Some(path);
        }
        if path.is_dir() {
            if let Some(found) = find_named_file(&path, name) {
                return Some(found);
            }
        }
    }
    None
}

fn runtime_status_text(root: &std::path::Path) -> String {
    let python = root.join("runtime/current/bin/python3");
    let bin = root.join("runtime/current/bin");
    let asr_model = managed_asr_model_path(root);
    let tools = ["python3", "yt-dlp", "ffmpeg", "ffprobe", "pandoc"];
    let packages = [
        ("pypdf", "pypdf"),
        ("lxml", "lxml"),
        ("requests", "requests"),
        ("whisper (mlx-whisper)", "mlx_whisper"),
    ];
    let mut component_lines: Vec<String> = Vec::new();
    let mut missing_components: Vec<String> = Vec::new();
    for tool in tools {
        let ready = bin.join(tool).exists();
        if !ready {
            missing_components.push(tool.to_string());
        }
        component_lines.push(format!(
            "- {} {tool}",
            if ready { "[OK]" } else { "[MISSING]" }
        ));
    }
    for (label, module) in packages {
        let ready = python_package_available(&python, module);
        if !ready {
            missing_components.push(label.to_string());
        }
        component_lines.push(format!(
            "- {} {label}",
            if ready { "[OK]" } else { "[MISSING]" }
        ));
    }
    let asr_ready = managed_asr_model_ready(&asr_model);
    if !asr_ready {
        missing_components.push("默认 ASR 模型".to_string());
    }
    component_lines.push(format!(
        "- {} 默认 ASR 模型 ({})",
        if asr_ready { "[OK]" } else { "[MISSING]" },
        managed_asr_model_repo()
    ));
    let status = if !python.exists() {
        "未安装"
    } else if missing_components.is_empty() {
        "已安装"
    } else {
        "需要修复"
    };
    let mut lines = vec![
        format!("托管环境目录：{}", root.join("runtime").display()),
        format!("版本：{MANAGED_RUNTIME_VERSION}"),
        format!("状态：{status}"),
        format!(
            "磁盘占用：{}",
            human_bytes(directory_size(&root.join("runtime")))
        ),
        "".to_string(),
        "组件：".to_string(),
    ];
    lines.extend(component_lines);
    if !missing_components.is_empty() {
        lines.push("".to_string());
        lines.push(format!(
            "缺少组件：{}。请点击“安装/修复”补齐托管环境。",
            missing_components.join("、")
        ));
    }
    lines.push("".to_string());
    lines.push(format!("模型目录：{}", root.join("models").display()));
    lines.push(format!("默认 ASR 模型：{}", asr_model.display()));
    lines.push(
        "Whisper 运行库和默认 ASR 模型会随托管环境安装；也可以在配置页选择其他已有模型目录。"
            .to_string(),
    );
    lines.join("\n") + "\n"
}

fn directory_size(path: &std::path::Path) -> u64 {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return 0;
    };
    if metadata.is_file() || metadata.file_type().is_symlink() {
        return metadata.len();
    }
    fs::read_dir(path)
        .ok()
        .into_iter()
        .flat_map(|entries| entries.filter_map(Result::ok))
        .map(|entry| directory_size(&entry.path()))
        .sum()
}

fn human_bytes(bytes: u64) -> String {
    if bytes >= 1024 * 1024 * 1024 {
        format!("{:.1} GB", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
    } else if bytes >= 1024 * 1024 {
        format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
    } else {
        format!("{:.1} KB", bytes as f64 / 1024.0)
    }
}

fn python_package_available(python: &Path, module: &str) -> bool {
    if !python.exists() {
        return false;
    }
    Command::new(python)
        .args(["-c", &format!("import {module}")])
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn resolve_worker_path(resource_dir: PathBuf) -> Result<PathBuf, String> {
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut candidates = vec![
        resource_dir
            .join("_up_")
            .join("worker")
            .join("local_note_studio_worker.py"),
        resource_dir
            .join("worker")
            .join("local_note_studio_worker.py"),
        current_dir
            .join("worker")
            .join("local_note_studio_worker.py"),
        current_dir
            .parent()
            .map(|path| path.join("worker").join("local_note_studio_worker.py"))
            .unwrap_or_else(|| PathBuf::from("worker").join("local_note_studio_worker.py")),
        manifest_dir
            .parent()
            .map(|path| path.join("worker").join("local_note_studio_worker.py"))
            .unwrap_or_else(|| PathBuf::from("worker").join("local_note_studio_worker.py")),
    ];
    candidates.dedup();
    candidates
        .into_iter()
        .find(|path| path.exists())
        .ok_or_else(|| "Cannot find worker/local_note_studio_worker.py. In development, run npm run tauri:dev from the project root.".to_string())
}

fn main() {
    let worker_state = Arc::new(WorkerState {
        child: Mutex::new(None),
        cancel_requested: AtomicBool::new(false),
    });
    let exit_state = worker_state.clone();
    let app = tauri::Builder::default()
        .manage(worker_state)
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            run_worker,
            run_worker_stream,
            cancel_worker,
            open_path,
            reveal_path,
            manage_runtime
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Local Note Studio");
    app.run(move |_app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
            let _ = stop_worker(&exit_state);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancellation_kills_the_active_child_and_sets_the_flag() {
        let mut command = Command::new("sh");
        command.arg("-c").arg("sleep 30");
        configure_child_lifecycle(&mut command);
        let child = command.spawn().expect("spawn cancellation fixture");
        let state = WorkerState {
            child: Mutex::new(Some(child)),
            cancel_requested: AtomicBool::new(false),
        };

        assert!(stop_worker(&state).expect("cancel child"));
        assert!(state.cancel_requested.load(Ordering::SeqCst));
        let mut guard = state.child.lock().expect("lock child");
        let status = guard
            .as_mut()
            .expect("child present")
            .wait()
            .expect("wait child");
        assert!(!status.success());
    }

    #[test]
    fn packaged_requests_default_to_managed_runtime() {
        assert_eq!(request_runtime_backend("{}"), "managed");
        assert_eq!(
            request_runtime_backend(r#"{"runtime_backend":"conda"}"#),
            "conda"
        );
    }

    #[test]
    fn managed_runtime_status_reports_repair_when_components_are_missing() {
        let root =
            std::env::temp_dir().join(format!("local-note-runtime-status-{}", std::process::id()));
        let bin = root.join("runtime/current/bin");
        fs::create_dir_all(&bin).expect("create runtime bin");
        fs::write(bin.join("python3"), "").expect("create python marker");

        let status = runtime_status_text(&root);

        assert!(status.contains("状态：需要修复"));
        assert!(status.contains("[MISSING] pandoc"));
        assert!(status.contains("请点击“安装/修复”补齐托管环境"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn gui_conda_search_includes_user_and_system_locations() {
        let home = Path::new("/Users/tester");
        let directories = conda_bin_directories(home);
        assert!(directories.contains(&home.join("miniforge3/bin")));
        assert!(directories.contains(&PathBuf::from("/opt/homebrew/bin")));
        assert_eq!(
            find_conda_executable(home, Some("~/custom-conda/bin/conda")),
            Some(home.join("custom-conda/bin/conda"))
        );
    }

    #[test]
    fn managed_pip_install_uses_default_then_fallback_indexes() {
        let attempts = managed_pip_index_attempts();
        assert!(attempts.len() >= 3);
        let default_position = attempts
            .iter()
            .position(|attempt| attempt.label == "默认 PyPI 源" && attempt.index_url.is_none())
            .expect("default PyPI attempt");
        let tuna_position = attempts
            .iter()
            .position(|attempt| {
                attempt.index_url.as_deref() == Some("https://pypi.tuna.tsinghua.edu.cn/simple")
            })
            .expect("tuna mirror attempt");
        let aliyun_position = attempts
            .iter()
            .position(|attempt| {
                attempt.index_url.as_deref() == Some("https://mirrors.aliyun.com/pypi/simple")
            })
            .expect("aliyun mirror attempt");
        assert!(default_position < tuna_position);
        assert!(tuna_position < aliyun_position);
    }

    #[test]
    fn pip_network_error_detection_matches_ssl_fetch_failures() {
        let text = "Could not fetch URL https://pypi.org/simple/pypdf/: There was a problem confirming the ssl certificate: SSLEOFError";
        assert!(pip_failure_looks_network_related(text));
        assert!(!pip_failure_looks_network_related(
            "ERROR: Could not find a version that satisfies the requirement definitely-not-real==0"
        ));
    }

    #[test]
    fn curl_download_prefers_http1_before_default_protocol() {
        let attempts = curl_download_attempts();
        assert!(attempts.len() >= 2);
        assert_eq!(attempts[0].label, "HTTP/1.1");
        assert!(attempts[0].args.contains(&"--http1.1"));
        assert_eq!(attempts[1].label, "默认协议");
    }

    #[test]
    fn managed_tool_downloads_have_specific_url_overrides() {
        assert_eq!(
            tool_url_override_key("pandoc"),
            "LOCAL_NOTE_STUDIO_PANDOC_URL"
        );
        assert_eq!(
            tool_url_override_key("ffmpeg"),
            "LOCAL_NOTE_STUDIO_FFMPEG_URL"
        );
        assert_eq!(
            tool_url_override_key("ffprobe"),
            "LOCAL_NOTE_STUDIO_FFPROBE_URL"
        );
    }
}
