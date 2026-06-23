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

console.log(`frontend history compatibility: ok (${pathToFileURL(sourceUrl.pathname).pathname})`);
