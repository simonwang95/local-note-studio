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
assert.deepEqual(history.migrateRuntimePreference({ runtimeBackend: "conda", condaEnv: "course-whisper" }), {
  runtimeBackend: "managed",
  condaEnv: "course-whisper",
  runtimePreferenceConfirmed: true,
});
assert.equal(history.migrateRuntimePreference({ runtimeBackend: "conda", runtimePreferenceConfirmed: true }).runtimeBackend, "conda");
assert.equal(history.migrateRuntimePreference({ cookies: "./bili_cookies.txt", runtimePreferenceConfirmed: true }).cookies, "");
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
