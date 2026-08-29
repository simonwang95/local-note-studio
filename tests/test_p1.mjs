import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const storage = new Map();
globalThis.localStorage = {
  getItem(key) {
    return storage.has(key) ? storage.get(key) : null;
  },
  setItem(key, value) {
    storage.set(key, String(value));
  },
  removeItem(key) {
    storage.delete(key);
  },
};

const sourceUrl = new URL("../src/p1.ts", import.meta.url);
const source = fs.readFileSync(sourceUrl, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const history = await import(moduleUrl);

const shellSourceUrl = new URL("../src/app-shell.ts", import.meta.url);
const shellSource = fs.readFileSync(shellSourceUrl, "utf8");
const compiledShell = ts.transpileModule(shellSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const shell = await import(`data:text/javascript;base64,${Buffer.from(compiledShell).toString("base64")}`);

const manifestStateUrl = new URL("../src/manifest-state.ts", import.meta.url);
const manifestStateSource = fs.readFileSync(manifestStateUrl, "utf8");
const compiledManifestState = ts.transpileModule(manifestStateSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const manifestState = await import(`data:text/javascript;base64,${Buffer.from(compiledManifestState).toString("base64")}`);

localStorage.setItem(
  "local-note-studio.task-history.v1",
  JSON.stringify([{ id: "legacy", task: "source-file", status: "running", startedAt: "2026-01-01T00:00:00Z" }]),
);
const entries = history.loadTaskHistory();
assert.equal(entries.length, 1);
assert.equal(entries[0].status, "interrupted");
assert.deepEqual(entries[0].request, {});
assert.deepEqual(entries[0].outputs, []);
assert.equal(entries[0].log, "");
assert.match(entries[0].error, /应用退出或进程中断/);

const sampleEntries = [
  { ...entries[0], id: "done", status: "completed" },
  { ...entries[0], id: "failed", status: "failed" },
];
assert.deepEqual(history.filterTaskHistory(sampleEntries, "failed").map((item) => item.id), ["failed"]);
assert.deepEqual(history.removeHistoryEntry(sampleEntries, "done").map((item) => item.id), ["failed"]);
assert.deepEqual(history.loadRecentValues("outputDir"), []);
assert.deepEqual(history.rememberRecentValue("outputDir", " /tmp/out "), ["/tmp/out"]);
assert.deepEqual(history.rememberRecentValue("outputDir", "/tmp/second"), ["/tmp/second", "/tmp/out"]);
assert.deepEqual(history.rememberRecentValue("outputDir", "/tmp/out"), ["/tmp/out", "/tmp/second"]);
assert.deepEqual(history.rememberRecentValue("source", "/tmp/source.pdf"), ["/tmp/source.pdf"]);
assert.deepEqual(history.loadRecentValues("outputDir"), ["/tmp/out", "/tmp/second"]);
assert.deepEqual(history.loadRecentValues("source"), ["/tmp/source.pdf"]);
assert.equal(history.pathDialogDefault("outputDir", ""), "/tmp/out");
assert.equal(history.pathDialogDefault("source", "/tmp/current-video.mp4"), "/tmp/current-video.mp4");
assert.equal(history.pathDialogDefault("source", "https://www.bilibili.com/video/BV1test"), "/tmp/source.pdf");
assert.deepEqual(history.removeRecentValue("outputDir", "/tmp/out"), ["/tmp/second"]);
assert.deepEqual(history.loadRecentValues("outputDir"), ["/tmp/second"]);
history.clearRecentValues("outputDir");
assert.deepEqual(history.loadRecentValues("outputDir"), []);
assert.deepEqual(history.migrateRuntimePreference({ runtimeBackend: "conda", condaEnv: "course-whisper" }), {
  runtimeBackend: "managed",
  condaEnv: "course-whisper",
  runtimePreferenceConfirmed: true,
});
assert.equal(history.migrateRuntimePreference({ runtimeBackend: "conda", runtimePreferenceConfirmed: true }).runtimeBackend, "conda");
assert.equal(history.migrateRuntimePreference({ cookies: "./bili_cookies.txt", runtimePreferenceConfirmed: true }).cookies, "");
assert.deepEqual(
  history.migrateRuntimePreference({
    runtimePreferenceConfirmed: true,
    apiBase: "http://127.0.0.1:1234/v1",
    apiKey: "lm-studio",
    model: "qwen3.6-35b-a3b-nvfp4",
  }),
  {
    runtimePreferenceConfirmed: true,
    apiBase: "http://127.0.0.1:8000/v1",
    apiKey: "mtplx-local",
    model: "mtplx-qwen38-27b-optimized-speed",
  },
);
assert.equal(
  history.migrateRuntimePreference({
    runtimePreferenceConfirmed: true,
    apiBase: "http://127.0.0.1:1234/v1",
    apiKey: "custom-key",
    model: "qwen3.6-35b-a3b-nvfp4",
  }).apiBase,
  "http://127.0.0.1:1234/v1",
);
assert.deepEqual(history.runtimeSelectionPayload("managed", "course-whisper", "/tmp/conda"), {
  runtime_backend: "managed",
  conda_env: "",
  conda_bin: "",
});
assert.deepEqual(history.runtimeSelectionPayload("conda", "course-whisper", "/tmp/conda"), {
  runtime_backend: "conda",
  conda_env: "course-whisper",
  conda_bin: "/tmp/conda",
});
assert.deepEqual(
  history.historyReplayRequest({
    task: "bilibili-up-opus",
    source: "123",
    asr_model: "/models/old",
    model: "old-model",
    cooldown_delay: "12",
  }),
  { task: "bilibili-up-opus", source: "123", cooldown_delay: "12" },
);
assert.equal(
  history.noChangesStatusLabel({ task: "bilibili-up-opus", status: "no_changes", outputs: [], output_dir: "/tmp", counts: { skipped: 5 }, details: { existing_complete: 5 } }),
  "无需更新（已有 5 项完整内容）",
);
assert.equal(
  history.noChangesStatusLabel({ task: "bilibili-up-video", status: "no_changes", outputs: [], output_dir: "/tmp", counts: { skipped: 2 }, details: { existing_complete: 1 } }),
  "无需更新（本次跳过 2 项）",
);
assert.equal(history.noChangesStatusLabel(null), "无需更新（没有新增内容）");
assert.equal(shell.resolveAppTab("validation"), "validation");
assert.equal(shell.resolveAppTab("unknown"), "config");
assert.equal(shell.adjacentAppTab("validation", 1), "config");
assert.equal(shell.adjacentAppTab("config", -1), "validation");
const manifestViews = new manifestState.ManifestViewStateStore();
manifestViews.remember("/tmp/source-manifest.json", true, "failed");
assert.deepEqual(manifestViews.get("/tmp/source-manifest.json"), { open: true, filter: "failed" });
manifestViews.keepOpen("/tmp/source-manifest.json", "attention");
assert.deepEqual(manifestViews.get("/tmp/source-manifest.json"), { open: true, filter: "attention" });

console.log(`frontend history compatibility: ok (${pathToFileURL(sourceUrl.pathname).pathname})`);
