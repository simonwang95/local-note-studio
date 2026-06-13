use std::path::PathBuf;
use std::process::Command;
use tauri::Manager;

#[tauri::command]
fn run_worker(app: tauri::AppHandle, request: String) -> Result<String, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let project_dir = std::env::current_dir().unwrap_or(resource_dir);
    let worker = project_dir.join("worker").join("local_note_studio_worker.py");
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

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![run_worker])
        .run(tauri::generate_context!())
        .expect("failed to run Local Note Studio");
}
