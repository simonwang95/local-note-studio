import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, "..");
const viteBin = path.join(
  projectRoot,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "vite.cmd" : "vite",
);
const originalParentPid = process.ppid;

const child = spawn(viteBin, ["--host", "127.0.0.1", "--port", "1420", "--strictPort"], {
  cwd: projectRoot,
  env: { ...process.env, BROWSER: "none" },
  stdio: "inherit",
});

let stopping = false;
let forceKillTimer;

function stop(signal = "SIGTERM") {
  if (stopping) return;
  stopping = true;
  if (child.exitCode === null && !child.killed) {
    child.kill(signal === "SIGINT" ? "SIGINT" : "SIGTERM");
    forceKillTimer = setTimeout(() => {
      if (child.exitCode === null && !child.killed) child.kill("SIGKILL");
    }, 3000);
    forceKillTimer.unref();
  }
}

for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => stop(signal));
}

const parentWatcher = setInterval(() => {
  if (originalParentPid > 1 && process.ppid === 1) {
    stop("SIGTERM");
  }
}, 1000);
parentWatcher.unref();

child.on("exit", (code, signal) => {
  if (forceKillTimer) clearTimeout(forceKillTimer);
  if (signal) {
    process.exitCode = signal === "SIGINT" ? 130 : 143;
  } else {
    process.exitCode = code ?? 0;
  }
});
