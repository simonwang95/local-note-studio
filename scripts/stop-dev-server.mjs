import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, "..");
const needles = [
  path.join(projectRoot, "node_modules", ".bin", "vite"),
  path.join(projectRoot, "scripts", "dev-server.mjs"),
];

const ps = spawnSync("ps", ["-axo", "pid=,ppid=,command="], { encoding: "utf8" });
if (ps.error) {
  console.error(`无法读取进程列表：${ps.error.message}`);
  process.exit(1);
}

const rows = new Map();
for (const line of ps.stdout.split("\n")) {
  const match = line.match(/^\s*(\d+)\s+(\d+)\s+(.+)$/);
  if (!match) continue;
  const [, pidText, ppidText, command] = match;
  rows.set(Number(pidText), {
    ppid: Number(ppidText),
    command,
  });
}

const candidates = new Set();
for (const [pid, row] of rows) {
  if (pid === process.pid) continue;
  if (needles.some((needle) => row.command.includes(needle))) {
    candidates.add(pid);
    const parent = rows.get(row.ppid);
    if (parent?.command.trim() === "npm run dev") candidates.add(row.ppid);
  }
}

if (candidates.size === 0) {
  console.log("没有发现当前项目的 Vite 开发服务器。");
  process.exit(0);
}

for (const pid of candidates) {
  try {
    process.kill(pid, "SIGTERM");
    console.log(`已请求停止进程 ${pid}`);
  } catch (error) {
    console.warn(`停止进程 ${pid} 失败：${error.message}`);
  }
}
