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

const profiles = history.loadDesktopProfiles({
  runtimePreferenceConfirmed: true,
  apiKey: "do-not-export-api-key",
  cookies: "/private/cookies.txt",
  chromeProfile: "/private/chrome/Profile 1",
  model: "local-model",
  outputRoot: "/notes/main",
});
const defaultProfile = profiles.profiles[0];
assert.equal(defaultProfile.name, "默认");
assert.equal(defaultProfile.settings.apiKey, undefined);
assert.equal(defaultProfile.settings.cookies, undefined);
assert.deepEqual(history.loadCredentials(defaultProfile.credentialRef), {
  apiKey: "do-not-export-api-key",
  cookies: "/private/cookies.txt",
  chromeProfile: "/private/chrome/Profile 1",
});
assert.equal(history.hasCredentialReference(defaultProfile.credentialRef), true);
const exportedProfile = history.exportDesktopProfile(defaultProfile);
assert.equal(exportedProfile.includes("do-not-export-api-key"), false);
assert.equal(exportedProfile.includes("/private/cookies.txt"), false);
assert.equal(exportedProfile.includes("/private/chrome"), false);
assert.equal(history.importedDesktopProfile(exportedProfile).settings.outputRoot, "/notes/main");
let profileState = history.createDesktopProfile(profiles, "第二套", defaultProfile);
const copiedProfile = profileState.profiles.find((item) => item.id === profileState.activeId);
assert.equal(history.loadCredentials(copiedProfile.credentialRef).apiKey, "do-not-export-api-key");
profileState = history.renameDesktopProfile(profileState, copiedProfile.id, "重命名档案");
assert.equal(profileState.profiles.find((item) => item.id === copiedProfile.id).name, "重命名档案");

const queued = {
  id: "queue-running",
  profileId: defaultProfile.id,
  status: "running",
  addedAt: "2026-09-29T00:00:00Z",
  request: { task: "local-video", source: "/media/a.mp4", output_dir: "/notes/video", api_key: "private", cookies: "/private/cookies.txt" },
};
history.saveQueue([queued, {
  ...queued,
  id: "queue-waiting",
  status: "waiting",
  request: { ...queued.request, source: "/media/b.mp4" },
}, {
  ...queued,
  id: "queue-waiting-2",
  status: "waiting",
  request: { ...queued.request, source: "/media/c.mp4" },
}]);
const restoredQueue = history.loadQueue();
assert.equal(restoredQueue[0].status, "interrupted");
assert.equal(restoredQueue[0].request.api_key, undefined);
assert.equal(restoredQueue[0].request.cookies, undefined);
assert.equal(restoredQueue[1].status, "waiting");
assert.equal(history.queueDuplicate(restoredQueue, { source: "/media/a.mp4", output_dir: "/notes/video" }).id, "queue-running");
assert.equal(history.queueDuplicate(restoredQueue, { source: "/media/d.mp4", output_dir: "/notes/video", output_filename: "same.md" }), undefined);
const namedConflict = history.loadQueue();
namedConflict.push({ ...restoredQueue[1], id: "same-name", request: { ...restoredQueue[1].request, source: "/media/c.mp4", output_filename: "same.md" } });
assert.equal(history.queueDuplicate(namedConflict, { source: "/media/d.mp4", output_dir: "/notes/video", output_filename: "SAME.md" }).id, "same-name");
assert.deepEqual(history.moveQueueEntry(restoredQueue, "queue-waiting-2", -1).map((item) => item.id), ["queue-running", "queue-waiting-2", "queue-waiting"]);
assert.equal(history.historyReplayRequest({ task: "local-video", desktop_profile_id: defaultProfile.id }).desktop_profile_id, undefined);

console.log(`frontend history compatibility: ok (${pathToFileURL(sourceUrl.pathname).pathname})`);

// Execute the actual form hydration/switch handlers with synchronous input autosave.
// This catches persisted mixtures of two profiles, beyond storage-helper coverage.
const { default: vm } = await import("node:vm");
const mainSource = fs.readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const mainAst = ts.createSourceFile("main.ts", mainSource, ts.ScriptTarget.Latest, true);
const handlerNames = new Set(["inputValue", "checkboxChecked", "setInputValue", "escapeHtml", "saveSettings", "applySavedSettings", "switchProfile"]);
const handlers = mainAst.statements.filter((statement) => ts.isFunctionDeclaration(statement) && handlerNames.has(statement.name?.text));
assert.equal(handlers.length, handlerNames.size);
const defaultsDeclaration = mainAst.statements.filter(ts.isVariableStatement)
  .flatMap((statement) => [...statement.declarationList.declarations])
  .find((declaration) => declaration.name.getText(mainAst) === "defaults");
assert.ok(defaultsDeclaration?.initializer);
const fields = new Map();
let profileContext;
let profileWrites = 0;
const fakeDocument = {
  querySelector(selector) {
    if (!fields.has(selector)) fields.set(selector, {
      value: "", checked: false, dataset: {}, selectedOptions: [],
      dispatchEvent() { profileContext.saveSettings(); },
      set innerHTML(html) {
        // Model replacement of the selected collection option, including its owner.
        const mid = /data-mid="([^"]*)"/.exec(html)?.[1] || "";
        this.selectedOptions = [{ dataset: { mid } }];
      },
    });
    return fields.get(selector);
  },
};
profileContext = vm.createContext({
  document: fakeDocument, Event: class Event {},
  hydrateRuntimeControls() {}, hydrateTaskControls() {}, hydrateTaskOutput() {}, setState() {},
  saveCredentials: history.saveCredentials, loadCredentials: history.loadCredentials,
  saveDesktopProfiles(state) { profileWrites += 1; history.saveDesktopProfiles(state); },
});
vm.runInContext(ts.transpileModule(`
  const defaults = ${defaultsDeclaration.initializer.getText(mainAst)};
  const first = {id:'first',name:'First',credentialRef:'first-cred',settings:{...defaults,overwriteOutputs:true,enableThinking:true,stockTerms:true,collectionType:'favorite',collectionId:'A',collectionMid:'10',proofreadCooldownDelay:'7'}};
  const second = {id:'second',name:'Second',credentialRef:'second-cred',settings:{...defaults,overwriteOutputs:false,enableThinking:false,stockTerms:false,collectionType:'series',collectionId:'B',collectionMid:'20',proofreadCooldownDelay:'0'}};
  let desktopProfiles = {schemaVersion:1,activeId:'first',profiles:[first,second]};
  let currentProfile = first;
  let savedSettings = first.settings;
  let isWorkerRunning = false;
  let queueRunning = false;
  ${handlers.map((handler) => handler.getText(mainAst)).join("\n")}
  function profileSnapshot() { return JSON.parse(JSON.stringify(desktopProfiles)); }
  function initializeForm() { applySavedSettings(savedSettings); }
`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, profileContext);
history.saveCredentials("second-cred", { apiKey: "second-secret", cookies: "/second/cookies.txt", chromeProfile: "" });
profileContext.initializeForm();
assert.equal(profileWrites, 1, "hydration must autosave only after every field is restored");
profileContext.switchProfile("second");
const switched = profileContext.profileSnapshot();
const restoredSecond = history.loadDesktopProfiles({}).profiles.find((profile) => profile.id === "second");
assert.equal(switched.activeId, "second");
for (const key of ["overwriteOutputs", "enableThinking", "stockTerms"]) {
  assert.equal(fakeDocument.querySelector(`#${key}`).checked, false);
  assert.equal(restoredSecond.settings[key], false, `${key} must survive reload`);
}
assert.equal(restoredSecond.settings.collectionId, "B");
assert.equal(restoredSecond.settings.collectionType, "series");
assert.equal(restoredSecond.settings.collectionMid, "20");
assert.equal(restoredSecond.settings.proofreadCooldownDelay, "0");
assert.equal(history.loadCredentials(restoredSecond.credentialRef).apiKey, "second-secret");
profileContext.switchProfile("first");
assert.equal(fakeDocument.querySelector("#overwriteOutputs").checked, true);
assert.equal(fakeDocument.querySelector("#collectionSelect").value, "A");
assert.equal(fakeDocument.querySelector("#proofreadCooldownDelay").value, "7");
console.log("desktop profile switching and atomic hydration: ok");

// Preserve the real declaration order around the initial UI recovery. Extracting
// only render functions would miss a const initialized after that first call.
const bootstrapLabels = new Set(["taskLabels", "historyStatusLabels", "queueStatusLabels"]);
const bootstrapRenderers = new Set(["renderHistory", "renderQueue", "escapeHtml"]);
const initialRecovery = mainAst.statements.find((statement) =>
  ts.isTryStatement(statement) && statement.tryBlock.getText(mainAst).includes("appTabs.bind()")
);
assert.ok(initialRecovery, "the actual startup UI recovery must be exercised");
const bootstrapStatements = mainAst.statements.filter((statement) =>
  statement === initialRecovery
  || (ts.isVariableStatement(statement) && statement.declarationList.declarations.some((declaration) => bootstrapLabels.has(declaration.name.getText(mainAst))))
  || (ts.isFunctionDeclaration(statement) && bootstrapRenderers.has(statement.name?.text))
);
assert.equal(bootstrapStatements.length, bootstrapLabels.size + bootstrapRenderers.size + 1);
history.saveTaskHistory([{
  id: "startup-history", task: "local-video", status: "running", startedAt: "2026-10-07T00:00:00Z",
  request: { source: "/media/resume.mp4" }, outputs: [], log: "",
}]);
history.saveQueue([
  { id: "startup-running", profileId: defaultProfile.id, status: "running", addedAt: "2026-10-07T00:00:00Z", request: { task: "local-video", source: "/media/queued.mp4", output_dir: "/notes" } },
  { id: "startup-waiting", profileId: defaultProfile.id, status: "waiting", addedAt: "2026-10-07T00:00:00Z", request: { task: "local-video", source: "/media/waiting.mp4", output_dir: "/notes" } },
]);
const bootstrapFields = new Map();
const bootstrapErrors = [];
const bootstrapContext = vm.createContext({
  document: { querySelector(selector) {
    if (!bootstrapFields.has(selector)) bootstrapFields.set(selector, { innerHTML: "", textContent: "", querySelectorAll: () => [] });
    return bootstrapFields.get(selector);
  } },
  taskHistory: history.loadTaskHistory(), queueEntries: history.loadQueue(),
  desktopProfiles: { profiles: [defaultProfile] }, historyFilter: "all", queueRunning: false,
  filterTaskHistory: history.filterTaskHistory,
  appTabs: { bind() {} }, hydrateRuntimeControls() {}, hydrateTaskControls() {}, hydrateTaskOutput() {}, bindSettingsPersistence() {},
  setState(message) { bootstrapErrors.push(message); },
  setOutput(message) { bootstrapErrors.push(message); }, errorMessage: String,
});
vm.runInContext(ts.transpileModule(bootstrapStatements.map((statement) => statement.getText(mainAst)).join("\n"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText, bootstrapContext);
assert.deepEqual(bootstrapErrors, [], "nonempty history and queue must restore without a startup error");
assert.equal(bootstrapFields.get("#historyCount").textContent, "1/1 条");
assert.match(bootstrapFields.get("#historyList").innerHTML, /已中断.*本地视频\/音频/s);
assert.match(bootstrapFields.get("#historyList").innerHTML, /按历史参数重跑/);
assert.match(bootstrapFields.get("#queueList").innerHTML, /已中断.*queued\.mp4/s);
assert.match(bootstrapFields.get("#queueList").innerHTML, /等待中.*waiting\.mp4/s);
assert.match(bootstrapFields.get("#queueList").innerHTML, /data-queue-action="retry"/);
console.log("desktop startup restores persisted history and queue: ok");
