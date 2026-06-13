use std::path::PathBuf;
use std::process::Command;
use tauri::Manager;

#[tauri::command]
async fn run_worker(app: tauri::AppHandle, request: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_worker_blocking(app, request))
        .await
        .map_err(|err| err.to_string())?
}

fn run_worker_blocking(app: tauri::AppHandle, request: String) -> Result<String, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let worker = resolve_worker_path(resource_dir)?;
    let python = std::env::var("LOCAL_NOTE_STUDIO_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let output = Command::new(python)
        .arg(worker)
        .arg("--request-json")
        .arg(request)
        .output()
        .map_err(|err| err.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if output.status.success() {
        Ok(stdout.to_string())
    } else {
        Err(format!("{}\n{}", stdout, stderr))
    }
}

fn resolve_worker_path(resource_dir: PathBuf) -> Result<PathBuf, String> {
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut candidates = vec![
        current_dir.join("worker").join("local_note_studio_worker.py"),
        current_dir
            .parent()
            .map(|path| path.join("worker").join("local_note_studio_worker.py"))
            .unwrap_or_else(|| PathBuf::from("worker").join("local_note_studio_worker.py")),
        manifest_dir
            .parent()
            .map(|path| path.join("worker").join("local_note_studio_worker.py"))
            .unwrap_or_else(|| PathBuf::from("worker").join("local_note_studio_worker.py")),
        resource_dir.join("worker").join("local_note_studio_worker.py"),
    ];
    candidates.dedup();
    candidates
        .into_iter()
        .find(|path| path.exists())
        .ok_or_else(|| "Cannot find worker/local_note_studio_worker.py. In development, run npm run tauri:dev from the project root.".to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![run_worker])
        .run(tauri::generate_context!())
        .expect("failed to run Local Note Studio");
}
