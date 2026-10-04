import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const compile = (source) => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const helperSource = fs.readFileSync(new URL("../src/cache-maintenance.ts", import.meta.url), "utf8");
const cache = await import(`data:text/javascript;base64,${Buffer.from(compile(helperSource)).toString("base64")}`);

const queue = [{ id: "one", status: "waiting", request: { task: "local-video", run_id: "worker-run-one", source: "/media/a.mp4", output_dir: "/notes", api_key: "private", cookies: "/private/cookies", browser_profile: "/private/chrome", debug: { password: "private" } } }];
const history = [{ id: "one", status: "failed", request: { task: "local-video", source: "/media/a.mp4", output_dir: "/notes" }, outputs: ["/notes/a.md"], log: "SECRET ORIGINAL" }];
const refs = cache.cacheReferences(queue, history);
assert.deepEqual(refs.entries.map((entry) => entry.id), ["queue:one", "history:one"], "queue and history identifiers cannot collide");
assert.equal(JSON.stringify(refs).includes("private"), false);
assert.equal(JSON.stringify(refs).includes("SECRET ORIGINAL"), false);
assert.deepEqual(refs.entries[1].outputs, ["/notes/a.md"]);
assert.equal(refs.entries[0].request.run_id, "worker-run-one");
queue[0].request.source = "/media/changed.mp4";
assert.equal(refs.entries[0].request.source, "/media/a.mp4", "queued reference requests must be stable snapshots");
assert.equal(cache.cacheMetric(null), "未知");
assert.equal(cache.cacheMetric(undefined), "未知");
assert.equal(cache.cacheMetric(0), "0", "observed zero is distinct from missing metrics");
assert.equal(cache.cacheMetric({ asr: null, proofread: 1.5 }), '{"asr":"未知","proofread":1.5}');
assert.equal(cache.cacheBytes(undefined), "未知");
assert.equal(cache.cacheBytes(1024), "1.0 KiB");
assert.equal(cache.cacheRetentionDays("0"), 0);
assert.equal(cache.cacheRetentionDays("3650"), 3650);
for (const invalid of ["", "-1", "1.5", "3651", "NaN"]) assert.throws(() => cache.cacheRetentionDays(invalid));
assert.equal(cache.filterCacheItems([{ category: "asr_diagnostics", task: "local-video", path: "/cache/a.json", source_ref: "Source A", status: "failed" }, { category: "source_evidence", path: "/cache/b.json" }], "asr_diagnostics", " source a ").length, 1);
assert.equal(cache.cacheSourceMatches(["BV1Example"], "https://www.bilibili.com/video/BV1Example?source=1"), true);
assert.equal(cache.filterCacheItems([{ category: "source_evidence", run_id: "task-one", references: [] }], "all", "task-one").length, 1);
assert.equal(cache.filterCacheItems([{ category: "source_evidence", references: ["history:task-two"] }], "all", "task-two").length, 1);
assert.equal(cache.filterCacheItems([{ category: "source_evidence", references: [{ type: "task", run_id: "task-three", protects: true }] }], "all", "task-three").length, 1);

const preview = { preview_id: "approved-preview", expires_at: "2026-10-04T10:00:00Z", candidate_count: 1, total_bytes: 42, candidates: [{ path: "/cache/one.json", size_bytes: 42 }], skipped: [], selection: { categories: ["disposable_cache"], older_than_days: 30 } };
assert.match(cache.cachePreviewConfirmation(preview), /1 项（42 B）/);
assert.match(cache.cachePreviewConfirmation(preview), /\/cache\/one\.json（42 B）/);
assert.match(cache.cachePreviewConfirmation(preview), /可清理缓存/);

const mainSource = fs.readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const ast = ts.createSourceFile("main.ts", mainSource, ts.ScriptTarget.Latest, true);
const declarations = ast.statements.filter(ts.isFunctionDeclaration);
const namedFunction = (name) => {
  const fn = declarations.find((item) => item.name?.text === name);
  assert.ok(fn, `missing ${name}`);
  return fn.getText(ast);
};
const handlers = ["persistTaskQueue", "persistTaskHistory", "scheduleCacheReferences", "syncCacheReferences", "invalidateCachePreview"].map(namedFunction);
const notices = new Map();
const snapshots = [];
let releaseFirst;
let shouldFail = false;
let persistedRegistry;
const context = vm.createContext({
  cacheReferences: cache.cacheReferences,
  hasTauriRuntime: () => true,
  updateCacheButtons() {},
  document: { querySelector(selector) { if (!notices.has(selector)) notices.set(selector, { textContent: "" }); return notices.get(selector); } },
  saveQueue() {}, saveTaskHistory() {}, errorMessage: String,
  async invokeCache(action, payload) {
    assert.equal(action, "references");
    snapshots.push(payload);
    if (snapshots.length === 1) await new Promise((resolve) => { releaseFirst = resolve; });
    if (shouldFail) throw new Error("cache lock busy");
    persistedRegistry = payload;
    return { ok: true };
  },
});
vm.runInContext(compile(`let queueEntries = []; let taskHistory = []; let cachePreview = null; let cacheReferencesError = ''; let cacheReferencesReady = Promise.resolve(); ${handlers.join("\n")}
  function replaceQueue(entries) { queueEntries = entries; persistTaskQueue(); }
  function replaceHistory(entries) { taskHistory = entries; persistTaskHistory(); }
  function barrier() { return cacheReferencesReady; }
`), context);
context.replaceQueue([{ id: "first", status: "waiting", request: { task: "local-video", source: "/a.mp4", api_key: "hidden" } }]);
await Promise.resolve();
await Promise.resolve();
assert.equal(snapshots.length, 1);
context.replaceQueue([{ id: "second", status: "waiting", request: { task: "local-video", source: "/b.mp4" } }]);
assert.equal(snapshots.length, 1, "a later registry write must wait for the previous write");
releaseFirst();
await context.barrier();
assert.deepEqual(snapshots.map((snapshot) => snapshot.entries[0].id), ["queue:first", "queue:second"]);
const protectedBeforeFailure = persistedRegistry;
shouldFail = true;
context.replaceQueue([{ id: "third", status: "running", request: { source: "/c.mp4" } }]);
await assert.rejects(context.barrier(), /cache lock busy/);
assert.equal(persistedRegistry, protectedBeforeFailure, "lock failure must retain previously registered task protections");
assert.match(notices.get("#cacheReferencesNotice").textContent, /保留上次引用/);
shouldFail = false;
await context.syncCacheReferences();
assert.equal(persistedRegistry.entries[0].id, "queue:third");
context.replaceHistory(history);
await context.barrier();
assert.equal(persistedRegistry.entries[1].id, "history:one", "failed GUI history must protect its recovery materials");
context.replaceQueue([]);
await context.barrier();
assert.equal(persistedRegistry.entries.length, 1, "removing queue entries must resynchronize remaining history protections");

for (const name of ["runTask", "startQueue"]) {
  const body = namedFunction(name);
  assert.match(body, /await syncCacheReferences\(\);\s*const (?:result|output) = await invokeWorker\(request\)/, "task execution must wait for the latest persisted reference registry");
  assert.match(body, /request\.run_id = (?:activeHistoryEntry|history)\.id/, "the GUI task history and Worker diagnostics must use the same run identifier");
  assert.match(body, /(?:activeHistoryEntry|history)\.request\.run_id = (?:activeHistoryEntry|history)\.id/);
}
for (const name of ["enqueueCurrentTask", "deleteHistoryEntry", "clearHistory", "startQueue", "runTask"]) assert.match(namedFunction(name), /if \([^)]*cacheBusy/, `${name} must not change references during a cache cleanup`);
assert.match(namedFunction("renderQueue"), /if \(cacheBusy\) return/, "queue removal and retry must not race cleanup");
assert.match(namedFunction("renderHistory"), /entry\.request\.run_id \|\| taskResultFromLog\(entry\.log\)\?\.run_id \|\| entry\.task/);
const savedQueueCalls = mainSource.match(/saveQueue\(queueEntries\)/g) || [];
const savedHistoryCalls = mainSource.match(/saveTaskHistory\(taskHistory\)/g) || [];
assert.equal(savedQueueCalls.length, 1, "queue saves must pass through reference synchronization");
assert.equal(savedHistoryCalls.length, 1, "history saves must pass through reference synchronization");
assert.match(mainSource, /scheduleCacheReferences\(\);\s*void runEnvironmentCheck\(\)/, "restored queue references must be synchronized at startup");

const cleanCalls = [];
const cleanContext = vm.createContext({
  cachePreviewConfirmation: cache.cachePreviewConfirmation,
  cacheBytes: cache.cacheBytes,
  window: { confirm: () => true },
  document: { querySelector: () => ({ textContent: "" }) },
  async syncCacheReferences() { cleanCalls.push(["references"]); },
  async cacheOperation(operation) { await operation(); },
  async invokeCache(action, options) {
    cleanCalls.push([action, options]);
    return action === "clean" ? { deleted: [{}], skipped: [], deleted_bytes: 42 } : {};
  },
  renderCacheInventory() {}, cacheNotice() {},
});
vm.runInContext(compile(`let cachePreview = ${JSON.stringify(preview)}; let cacheInventory = null; let cacheBusy = false; let isWorkerRunning = false; let queueRunning = false; ${namedFunction("cleanCachePreview")}`), cleanContext);
await cleanContext.cleanCachePreview();
assert.deepEqual(JSON.parse(JSON.stringify(cleanCalls)), [["references"], ["clean", { preview_id: "approved-preview" }], ["inventory", null]]);
const exportFunction = namedFunction("exportCacheDiagnostics");
assert.match(exportFunction, /include_original: checkboxChecked\("cacheIncludeOriginal"\)/);
assert.match(exportFunction, /include_paths: checkboxChecked\("cacheIncludePaths"\)/);
assert.doesNotMatch(exportFunction, /(?:setOutput|appendOutput|saveTaskHistory)\(/, "exported originals must never enter GUI task logs");
assert.match(mainSource, /id="cacheIncludeOriginal" type="checkbox" \/>/);
assert.match(mainSource, /id="cacheIncludePaths" type="checkbox" \/>/);
assert.match(mainSource, /id="cacheAutoEnabled" type="checkbox" \/>/);

// Match the inventory shape emitted by handle_cache_request: task references
// are structured objects, telemetry is flat, and missing metrics are null.
const inventoryFixture = {
  schema_version: 1,
  items: [{ id: "fixture-item", category: "source_evidence", path: "/cache/<file>.json", size_bytes: 42, created_at: "2026-10-04T01:00:00Z", modified_at: "2026-10-04T02:00:00Z", status: "completed", run_id: "fixture-run", task: "local-video", source_ref: "file:1234567890abcdef", references: [{ type: "task", id: "history:fixture-run", run_id: "fixture-run", status: "failed", protects: true }], protected: true, protection_reasons: ["referenced_by_task_or_transaction"] }],
  summary: { total_count: 1, total_bytes: 42, protected_count: 1, protected_bytes: 42, diagnostics_count: 3, diagnostics_bytes: 2048, by_category: { source_evidence: { count: 1, size_bytes: 42, protected_count: 1 }, disposable_cache: { count: 0, size_bytes: 0, protected_count: 0 } } },
  policy: { auto_enabled: false, retention_days: 30, categories: ["disposable_cache"] },
  diagnostics: [{ run_id: "fixture-run", task: "local-video", source_ref: "file:1234567890abcdef", status: "failed", started_at: "2026-10-04T01:00:00Z", finished_at: "2026-10-04T02:00:00Z", stage_durations: { asr: null }, model_calls: 0, asr_calls: 0, llm_calls: 0, prompt_tokens: null, completion_tokens: null, total_tokens: null, reasoning_tokens: null, retries: null, cache_hits: 0, effective_config: null }],
  references_available: true,
};
const elements = new Map();
const renderContext = vm.createContext({
  cacheBytes: cache.cacheBytes, cacheMetric: cache.cacheMetric, cacheCategoryLabels: cache.cacheCategoryLabels,
  filterCacheItems: cache.filterCacheItems, cacheSourceMatches: cache.cacheSourceMatches,
  inputValue: (id) => id === "cacheCategoryFilter" ? "all" : id === "cacheSourceFilter" ? "fixture-run" : "",
  document: { querySelector(selector) { if (!elements.has(selector)) elements.set(selector, { innerHTML: "", querySelectorAll: () => [] }); return elements.get(selector); } },
});
vm.runInContext(compile(`let cacheInventory = ${JSON.stringify(inventoryFixture)}; const taskLabels = {'local-video':'本地视频/音频'}; ${["cacheTime", "escapeHtml", "renderCacheInventory"].map(namedFunction).join("\n")}`), renderContext);
renderContext.renderCacheInventory();
assert.match(elements.get("#cacheSummary").innerHTML, /引用保护 1 项 · 42 B/);
assert.match(elements.get("#cacheSummary").innerHTML, /缓存材料 1 项 · 42 B/);
assert.match(elements.get("#cacheSummary").innerHTML, /诊断审计保留 3 项 · 2.0 KiB（暂不支持清理）/);
assert.match(elements.get("#cacheSummary").innerHTML, /可清理缓存 0 项 · 0 B/);
assert.match(elements.get("#cacheList").innerHTML, /\/cache\/&lt;file&gt;\.json/);
assert.match(elements.get("#cacheList").innerHTML, /history:fixture-run/);
assert.match(elements.get("#cacheDiagnosticList").innerHTML, /模型调用总次数<\/dt><dd>0/);
assert.match(elements.get("#cacheDiagnosticList").innerHTML, /总 tokens<\/dt><dd>未知/);
assert.match(elements.get("#cacheDiagnosticList").innerHTML, /总耗时（秒）<\/dt><dd>未知/);
assert.match(elements.get("#cacheDiagnosticList").innerHTML, /&quot;asr&quot;:&quot;未知&quot;/);
let quietRequest;
const invokeContext = vm.createContext({
  inputValue: () => "",
  runtimeSelectionPayload: () => ({ runtime_backend: "managed", conda_env: "", conda_bin: "" }),
  invokeWorkerQuiet: async (request) => { quietRequest = request; return `TASK_RESULT_JSON:${JSON.stringify({ status: "completed", details: { cache: inventoryFixture } })}`; },
  taskResultFromLog: (log) => JSON.parse(log.split("TASK_RESULT_JSON:")[1]),
});
vm.runInContext(compile(namedFunction("invokeCache")), invokeContext);
const actualInventory = await invokeContext.invokeCache("inventory");
assert.deepEqual(JSON.parse(JSON.stringify(actualInventory)), inventoryFixture);
assert.equal(quietRequest.task, "cache-manage");
assert.equal(quietRequest.cache_action, "inventory");
assert.equal("api_key" in quietRequest, false);
if (process.argv[2]) {
  const backendFixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  vm.runInContext(`cacheInventory = ${JSON.stringify(backendFixture)};`, renderContext);
  renderContext.renderCacheInventory();
  assert.match(elements.get("#cacheList").innerHTML, /引用保护/);
  assert.match(elements.get("#cacheDiagnosticList").innerHTML, /未知/);
  console.log("cache UI rendered the live backend fixture: ok");
}
console.log("cache frontend references, cleanup approval, privacy, and unknown metrics: ok");
