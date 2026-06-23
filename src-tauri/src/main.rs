use serde::Serialize;
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
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

fn stop_worker(state: &WorkerState) -> Result<bool, String> {
    state.cancel_requested.store(true, Ordering::SeqCst);
    let mut child_guard = state.child.lock().map_err(|err| err.to_string())?;
    match child_guard.as_mut() {
        Some(child) => {
            child.kill().map_err(|err| err.to_string())?;
            Ok(true)
        }
        None => Ok(false),
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
    let python =
        std::env::var("LOCAL_NOTE_STUDIO_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let output = Command::new(python)
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
    let python =
        std::env::var("LOCAL_NOTE_STUDIO_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let mut child = Command::new(python)
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

fn resolve_worker_path(resource_dir: PathBuf) -> Result<PathBuf, String> {
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut candidates = vec![
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
        resource_dir
            .join("worker")
            .join("local_note_studio_worker.py"),
    ];
    candidates.dedup();
    candidates
        .into_iter()
        .find(|path| path.exists())
        .ok_or_else(|| "Cannot find worker/local_note_studio_worker.py. In development, run npm run tauri:dev from the project root.".to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(Arc::new(WorkerState {
            child: Mutex::new(None),
            cancel_requested: AtomicBool::new(false),
        }))
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            run_worker,
            run_worker_stream,
            cancel_worker
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Local Note Studio");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancellation_kills_the_active_child_and_sets_the_flag() {
        let child = Command::new("sh")
            .arg("-c")
            .arg("sleep 30")
            .spawn()
            .expect("spawn cancellation fixture");
        let state = WorkerState {
            child: Mutex::new(Some(child)),
            cancel_requested: AtomicBool::new(false),
        };

        assert!(stop_worker(&state).expect("cancel child"));
        assert!(state.cancel_requested.load(Ordering::SeqCst));
        let mut guard = state.child.lock().expect("lock child");
        let status = guard.as_mut().expect("child present").wait().expect("wait child");
        assert!(!status.success());
    }
}
