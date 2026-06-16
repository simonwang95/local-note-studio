import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open, type OpenDialogOptions } from "@tauri-apps/plugin-dialog";
import "./styles.css";

type TaskType =
  | "bilibili-url"
  | "bilibili-favorite"
  | "web-url"
  | "source-file"
  | "paper-quickread"
  | "local-video";

type SubtitleStrategy = "yt-dlp" | "web" | "asr";

type SavedSettings = {
  condaEnv: string;
  pythonBin: string;
  apiBase: string;
  apiKey: string;
  model: string;
  cookies: string;
  outputRoot: string;
  subtitleStrategy: SubtitleStrategy;
  favoriteLimit: string;
  extractKeyframes: boolean;
  stockTerms: boolean;
  enableOcr: boolean;
};

type TauriWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
};

type WorkerLogPayload = {
  line: string;
};

class TauriRuntimeUnavailableError extends Error {}

const settingsKey = "local-note-studio.settings.v1";
let isWorkerRunning = false;
let workerLogListenerReady: Promise<void> | null = null;

const taskLabels: Record<TaskType, string> = {
  "bilibili-url": "B站单链接",
  "bilibili-favorite": "B站收藏夹/系列",
  "web-url": "微信公众号/网页",
  "source-file": "Word/PDF整理",
  "paper-quickread": "论文速读",
  "local-video": "本地视频/音频",
};

const taskHints: Record<TaskType, string> = {
  "bilibili-url": "输入一个 Bilibili 视频链接。Markdown 会直接写入本次输出目录；可选生成关键帧图文笔记。",
  "bilibili-favorite": "使用 worker/env.local 中的 BILIBILI_FAV_MEDIA_ID。需要 cookie 时先在上方配置。",
  "web-url": "输入微信公众号文章或一般网页 URL。Qwen 整理会插入原文之上，并保留完整原文。",
  "source-file": "输入本地 .doc、.docx、.pdf、.pptx、.xlsx/.csv、.html 或图片文件。支持扫描版 PDF 的 OCR 回退；抽取后会调用 Qwen 整理，并在末尾保留原文。",
  "paper-quickread": "输入论文 PDF 路径，生成速读笔记并保留全文翻译。",
  "local-video": "输入本地视频/音频文件路径，或一个媒体目录路径。Markdown 会直接写入本次输出目录；可选生成关键帧图文笔记。",
};

const outputSubdirs: Record<TaskType, string> = {
  "bilibili-url": "Net/BiliBili",
  "bilibili-favorite": "Net/BiliBili",
  "web-url": "Net/WeChat",
  "source-file": "Inbox",
  "paper-quickread": "AI/_quickread/AI_paper",
  "local-video": "Net/BiliBili",
};

const subtitleStrategyLabels: Record<SubtitleStrategy, string> = {
  "yt-dlp": "yt-dlp 字幕优先",
  web: "网页播放器字幕优先",
  asr: "ASR 语音转写优先",
};

const subtitleStrategyOptions: Record<TaskType, Array<{ value: SubtitleStrategy; label: string }>> = {
  "bilibili-url": [
    { value: "yt-dlp", label: "yt-dlp 字幕优先" },
    { value: "web", label: "网页播放器字幕优先" },
    { value: "asr", label: "ASR 语音转写优先" },
  ],
  "bilibili-favorite": [
    { value: "yt-dlp", label: "yt-dlp 字幕优先" },
    { value: "web", label: "网页播放器字幕优先" },
    { value: "asr", label: "ASR 语音转写优先" },
  ],
  "local-video": [
    { value: "yt-dlp", label: "同目录 SRT 字幕优先" },
    { value: "asr", label: "ASR 语音转写优先" },
  ],
  "web-url": [{ value: "yt-dlp", label: "不适用" }],
  "source-file": [{ value: "yt-dlp", label: "不适用" }],
  "paper-quickread": [{ value: "yt-dlp", label: "不适用" }],
};

const defaults: SavedSettings = {
  condaEnv: "course-whisper",
  pythonBin: "python3",
  apiBase: "http://127.0.0.1:1234/v1",
  apiKey: "lm-studio",
  model: "qwen3.6-35b-a3b-nvfp4",
  cookies: "",
  outputRoot: "",
  subtitleStrategy: "yt-dlp",
  favoriteLimit: "1",
  extractKeyframes: false,
  stockTerms: false,
  enableOcr: false,
};

const app = document.querySelector<HTMLDivElement>("#app");

if (!app) {
  throw new Error("missing #app");
}

const savedSettings = loadSettings();

app.innerHTML = `
  <section class="shell">
    <aside class="sidebar">
      <div>
        <h1>Local Note Studio</h1>
        <p>把视频、网页、文档和论文整理成 Obsidian 兼容 Markdown。</p>
      </div>

      <div class="status-card">
        <strong>实用提示</strong>
        <span>本次输出目录就是最终写入目录；B站收藏夹默认只测试 1 条；长任务可以随时取消。</span>
      </div>
    </aside>

    <main class="workspace">
      <section class="panel">
        <div class="panel-header">
          <div>
            <span class="step">1</span>
            <h2>运行环境</h2>
          </div>
          <button id="checkEnv" type="button">检查依赖</button>
        </div>
        <div class="form-grid compact">
          <label>
            Conda 环境
            <input id="condaEnv" value="${escapeHtml(savedSettings.condaEnv)}" placeholder="course-whisper" />
          </label>
          <label>
            Python 命令
            <input id="pythonBin" value="${escapeHtml(savedSettings.pythonBin)}" placeholder="python3" />
          </label>
          <label>
            LLM API Base
            <input id="apiBase" value="${escapeHtml(savedSettings.apiBase)}" />
          </label>
          <label>
            API Key
            <div class="input-row secret-row">
              <input id="apiKey" type="password" value="${escapeHtml(savedSettings.apiKey)}" autocomplete="off" />
              <button id="toggleApiKey" type="button" class="secondary icon-button" title="显示 API Key" aria-label="显示 API Key">
                ${eyeIcon()}
              </button>
            </div>
          </label>
          <label>
            模型
            <input id="model" value="${escapeHtml(savedSettings.model)}" />
          </label>
          <label>
            B站 Cookie 文件
            <input id="cookies" value="${escapeHtml(savedSettings.cookies)}" placeholder="/path/to/bili_cookies.txt" />
            <p id="cookieStatus" class="field-note">Cookie 状态：待检查</p>
          </label>
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <span class="step">2</span>
            <h2>默认输出路径</h2>
          </div>
          <button id="saveSettings" type="button" class="secondary">保存配置</button>
        </div>
        <label>
          输出根目录
          <div class="input-row">
            <input id="outputRoot" value="${escapeHtml(savedSettings.outputRoot)}" placeholder="/Users/xxx/Notes" />
            <button id="chooseOutputRoot" type="button" class="secondary compact-button">选择</button>
          </div>
        </label>
        <p class="field-note">建议填写 Obsidian Vault 或长期笔记目录的绝对路径。切换任务时会帮你带出常用目录；真正写入位置以“本次输出目录”为准。</p>
      </section>

      <section class="panel task-panel">
        <div class="panel-header">
          <div>
            <span class="step">3</span>
            <h2>任务执行</h2>
          </div>
          <div class="actions">
            <button id="runDry" type="button" class="secondary">预览命令</button>
            <button id="runTask" type="button">运行任务</button>
            <button id="cancelTask" type="button" class="danger" disabled>取消任务</button>
          </div>
        </div>

        <div class="form-grid">
          <label>
            任务类型
            <select id="taskType">
              ${Object.entries(taskLabels)
                .map(([value, label]) => `<option value="${value}">${label}</option>`)
                .join("")}
            </select>
          </label>
          <label>
            本次输出目录
            <div class="input-row">
              <input id="outputDir" placeholder="/Users/xxx/Notes/Net/BiliBili" />
              <button id="chooseOutputDir" type="button" class="secondary compact-button">选择</button>
            </div>
          </label>
          <label>
            字幕/转录优先级
            <select id="subtitleStrategy">
              ${Object.entries(subtitleStrategyLabels)
                .map(
                  ([value, label]) =>
                    `<option value="${value}" ${value === savedSettings.subtitleStrategy ? "selected" : ""}>${label}</option>`,
                )
                .join("")}
            </select>
          </label>
          <label id="favoriteLimitField" class="hidden">
            收藏夹测试数量
            <input id="favoriteLimit" type="number" min="0" step="1" value="${escapeHtml(savedSettings.favoriteLimit)}" placeholder="1" />
          </label>
          <label id="extractKeyframesField" class="checkbox-field hidden">
            <span>关键帧图文笔记</span>
            <input id="extractKeyframes" type="checkbox" ${savedSettings.extractKeyframes ? "checked" : ""} />
          </label>
          <label id="stockTermsField" class="checkbox-field">
            <span>A股术语校验</span>
            <input id="stockTerms" type="checkbox" ${savedSettings.stockTerms ? "checked" : ""} />
          </label>
          <label id="enableOcrField" class="checkbox-field hidden">
            <span>启用 OCR</span>
            <input id="enableOcr" type="checkbox" ${savedSettings.enableOcr ? "checked" : ""} />
          </label>
        </div>

        <label>
          输入源 URL、文件路径或目录路径
          <div class="input-row">
            <input id="source" placeholder="https://www.bilibili.com/video/BV... 或 /path/to/file.pdf" />
            <button id="chooseSourceFile" type="button" class="secondary compact-button">文件</button>
            <button id="chooseSourceDir" type="button" class="secondary compact-button">目录</button>
          </div>
        </label>
        <p id="taskHint" class="field-note"></p>
      </section>

      <section class="log-panel">
        <div class="log-header">
          <h2>日志</h2>
          <span id="runState">准备就绪</span>
        </div>
        <pre id="output">先检查依赖，然后预览或运行任务。</pre>
      </section>
    </main>
  </section>
`;

const taskType = document.querySelector<HTMLSelectElement>("#taskType");
const outputRoot = document.querySelector<HTMLInputElement>("#outputRoot");
const outputDir = document.querySelector<HTMLInputElement>("#outputDir");
const taskHint = document.querySelector<HTMLParagraphElement>("#taskHint");

hydrateTaskControls();
hydrateTaskOutput();
bindSettingsPersistence();

taskType?.addEventListener("change", () => {
  hydrateTaskControls();
  hydrateTaskOutput();
  saveSettings();
});

outputRoot?.addEventListener("input", () => {
  hydrateTaskOutput();
  saveSettings();
});

document.querySelector<HTMLButtonElement>("#saveSettings")?.addEventListener("click", () => {
  saveSettings();
  setState("配置已保存");
});
document.querySelector<HTMLInputElement>("#cookies")?.addEventListener("input", () => {
  const el = document.querySelector<HTMLParagraphElement>("#cookieStatus");
  if (el) el.textContent = "Cookie 状态：待检查";
});

document.querySelector<HTMLButtonElement>("#checkEnv")?.addEventListener("click", () => runEnvironmentCheck());
document.querySelector<HTMLButtonElement>("#runDry")?.addEventListener("click", () => runTask(true));
document.querySelector<HTMLButtonElement>("#runTask")?.addEventListener("click", () => runTask(false));
document.querySelector<HTMLButtonElement>("#cancelTask")?.addEventListener("click", () => cancelWorker());
document.querySelector<HTMLButtonElement>("#chooseOutputRoot")?.addEventListener("click", () => chooseDirectory("outputRoot"));
document.querySelector<HTMLButtonElement>("#chooseOutputDir")?.addEventListener("click", () => chooseDirectory("outputDir"));
document.querySelector<HTMLButtonElement>("#chooseSourceFile")?.addEventListener("click", () => chooseSourceFile());
document.querySelector<HTMLButtonElement>("#chooseSourceDir")?.addEventListener("click", () => chooseDirectory("source"));
document.querySelector<HTMLButtonElement>("#toggleApiKey")?.addEventListener("click", () => toggleSecretField("apiKey", "toggleApiKey"));

if (!hasTauriRuntime()) {
  setState("浏览器预览");
  setOutput(tauriRuntimeHint());
} else {
  window.setTimeout(() => {
    void runEnvironmentCheck();
  }, 250);
}

function inputValue(id: string): string {
  return document.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`)?.value.trim() ?? "";
}

function checkboxChecked(id: string): boolean {
  return Boolean(document.querySelector<HTMLInputElement>(`#${id}`)?.checked);
}

function setInputValue(id: string, value: string): void {
  const input = document.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`);
  if (!input) return;
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function toggleSecretField(inputId: string, buttonId: string): void {
  const input = document.querySelector<HTMLInputElement>(`#${inputId}`);
  const button = document.querySelector<HTMLButtonElement>(`#${buttonId}`);
  if (!input || !button) return;
  const willShow = input.type === "password";
  input.type = willShow ? "text" : "password";
  button.title = willShow ? "隐藏 API Key" : "显示 API Key";
  button.setAttribute("aria-label", button.title);
  input.focus();
}

function currentTask(): TaskType {
  return (inputValue("taskType") || "bilibili-url") as TaskType;
}

function payload(dryRun: boolean) {
  return {
    task: currentTask(),
    source: inputValue("source"),
    output_dir: inputValue("outputDir"),
    conda_env: inputValue("condaEnv"),
    python_bin: inputValue("pythonBin"),
    api_base: inputValue("apiBase"),
    api_key: inputValue("apiKey"),
    model: inputValue("model"),
    cookies: inputValue("cookies"),
    subtitle_strategy: inputValue("subtitleStrategy"),
    favorite_limit: inputValue("favoriteLimit"),
    extract_keyframes: checkboxChecked("extractKeyframes"),
    stock_terms: checkboxChecked("stockTerms"),
    enable_ocr: checkboxChecked("enableOcr"),
    dry_run: dryRun,
  };
}

async function runEnvironmentCheck(): Promise<void> {
  if (isWorkerRunning) return;
  setWorkerRunning(true);
  saveSettings();
  setState("检查依赖中...");
  setOutput("");
  appendOutput("正在检查所选 conda/Python 环境...\n");
  try {
    const request = { ...payload(false), task: "env-check", source: "" };
    const result = await invokeWorker(request);
    updateCookieStatus(result);
    if (!currentOutput().trim()) setOutput(result);
    setState(result.includes("[MISSING]") ? "依赖缺失" : "依赖检查完成");
  } catch (error) {
    const message = errorMessage(error);
    if (currentOutput().trim()) {
      appendOutput(`\n检查失败：${message}\n`);
    } else {
      setOutput(message);
    }
    setState(error instanceof TauriRuntimeUnavailableError ? "浏览器预览" : "检查失败");
  } finally {
    setWorkerRunning(false);
  }
}

async function runTask(dryRun: boolean): Promise<void> {
  if (isWorkerRunning) return;
  saveSettings();
  const task = currentTask();
  if (!inputValue("outputDir")) {
    setState("缺少输出目录");
    setOutput("请先填写默认输出根目录，或手动填写本次输出目录。");
    return;
  }
  if (!inputValue("source") && task !== "bilibili-favorite") {
    setState("缺少输入源");
    setOutput("请填写 URL、文件路径或目录路径。");
    return;
  }

  setWorkerRunning(true);
  setState(dryRun ? "生成预览中..." : "任务运行中...");
  setOutput("");
  appendOutput(dryRun ? "正在生成命令预览...\n" : "任务已启动，日志会实时追加到这里。\n");
  try {
    const result = await invokeWorker(payload(dryRun));
    if (!currentOutput().trim()) setOutput(result || "(worker 没有返回输出)");
    setState(dryRun ? "预览完成" : "任务完成");
  } catch (error) {
    const message = errorMessage(error);
    if (message.startsWith("Task cancelled.")) {
      appendOutput("\n任务已取消。\n");
    } else if (currentOutput().trim()) {
      appendOutput(`\n任务失败：${message}\n`);
    } else {
      setOutput(message);
    }
    setState(
      error instanceof TauriRuntimeUnavailableError
        ? "浏览器预览"
        : message.startsWith("Task cancelled.")
          ? "已取消"
          : "任务失败",
    );
  } finally {
    setWorkerRunning(false);
  }
}

async function invokeWorker(request: object): Promise<string> {
  if (!hasTauriRuntime()) {
    throw new TauriRuntimeUnavailableError(tauriRuntimeHint());
  }
  await ensureWorkerLogListener();
  return invoke<string>("run_worker_stream", { request: JSON.stringify(request) });
}

async function cancelWorker(): Promise<void> {
  if (!isWorkerRunning || !hasTauriRuntime()) return;
  const cancelButton = document.querySelector<HTMLButtonElement>("#cancelTask");
  if (cancelButton) cancelButton.disabled = true;
  setState("正在取消...");
  appendOutput("\n正在请求取消当前任务...\n");
  try {
    const didCancel = await invoke<boolean>("cancel_worker");
    if (!didCancel) {
      appendOutput("当前没有正在运行的 worker。\n");
    }
  } catch (error) {
    appendOutput(`取消失败：${errorMessage(error)}\n`);
  }
}

async function chooseDirectory(targetId: "outputRoot" | "outputDir" | "source"): Promise<void> {
  await choosePath(targetId, { directory: true, multiple: false });
}

async function chooseSourceFile(): Promise<void> {
  const task = currentTask();
  let filters: OpenDialogOptions["filters"];
  if (task === "paper-quickread") {
    filters = [{ name: "PDF", extensions: ["pdf"] }];
  } else if (task === "local-video") {
    filters = [{ name: "Media", extensions: ["mp4", "mkv", "mov", "webm", "flv", "mp3", "m4a", "wav"] }];
  } else if (task === "source-file") {
    filters = [
      {
        name: "Supported Sources",
        extensions: ["doc", "docx", "pdf", "pptx", "xlsx", "csv", "tsv", "html", "htm", "png", "jpg", "jpeg", "webp", "heic", "bmp", "gif", "tif", "tiff"],
      },
    ];
  }
  await choosePath("source", { multiple: false, filters });
}

async function choosePath(
  targetId: "outputRoot" | "outputDir" | "source",
  options: OpenDialogOptions,
): Promise<void> {
  if (!hasTauriRuntime()) {
    setState("浏览器预览");
    appendOutput("\n路径选择只能在桌面应用中使用；浏览器预览时请直接手动输入路径。\n");
    return;
  }
  try {
    const selected = await open(options);
    const path = Array.isArray(selected) ? selected[0] : selected;
    if (typeof path === "string" && path) {
      setInputValue(targetId, path);
      if (targetId !== "source") saveSettings();
    }
  } catch (error) {
    appendOutput(`\n路径选择失败：${errorMessage(error)}\n`);
  }
}

async function ensureWorkerLogListener(): Promise<void> {
  if (workerLogListenerReady) return workerLogListenerReady;
  workerLogListenerReady = listen<WorkerLogPayload>("worker-log", (event) => {
    appendOutput(event.payload.line);
  }).then(() => undefined);
  return workerLogListenerReady;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function hasTauriRuntime(): boolean {
  return Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

function tauriRuntimeHint(): string {
  return [
    "当前页面运行在普通浏览器预览环境，无法调用 Tauri worker。",
    "",
    "请用桌面壳启动后再检查依赖、预览命令或运行任务：",
    "  npm run tauri:dev",
    "",
    "如果只想预览界面，可以继续填写表单；这些配置会保存在当前浏览器的 localStorage 中。",
  ].join("\n");
}

function hydrateTaskOutput(): void {
  const task = currentTask();
  if (taskHint) {
    taskHint.textContent = taskHints[task];
  }
  if (!outputDir) return;
  const root = outputRoot?.value.trim() ?? "";
  const derived = joinPath(root, outputSubdirs[task]);
  const previous = outputDir.dataset.derived ?? "";
  if (!outputDir.value || outputDir.value === previous) {
    outputDir.value = derived;
  }
  outputDir.dataset.derived = derived;
}

function hydrateTaskControls(): void {
  const task = currentTask();
  hydrateSubtitleStrategy(task);
  const favoriteLimitField = document.querySelector<HTMLElement>("#favoriteLimitField");
  if (favoriteLimitField) {
    favoriteLimitField.classList.toggle("hidden", task !== "bilibili-favorite");
  }
  const extractKeyframesField = document.querySelector<HTMLElement>("#extractKeyframesField");
  if (extractKeyframesField) {
    extractKeyframesField.classList.toggle("hidden", !["bilibili-url", "bilibili-favorite", "local-video"].includes(task));
  }
  const stockTermsField = document.querySelector<HTMLElement>("#stockTermsField");
  if (stockTermsField) {
    stockTermsField.classList.toggle("hidden", !["bilibili-url", "bilibili-favorite", "local-video", "web-url", "source-file"].includes(task));
  }
  const enableOcrField = document.querySelector<HTMLElement>("#enableOcrField");
  if (enableOcrField) {
    enableOcrField.classList.toggle("hidden", task !== "source-file");
  }
}

function hydrateSubtitleStrategy(task: TaskType): void {
  const select = document.querySelector<HTMLSelectElement>("#subtitleStrategy");
  if (!select) return;
  const options = subtitleStrategyOptions[task];
  const previous = (select.value || savedSettings.subtitleStrategy || defaults.subtitleStrategy) as SubtitleStrategy;
  const selected = options.some((option) => option.value === previous) ? previous : options[0].value;
  select.innerHTML = options
    .map((option) => `<option value="${option.value}" ${option.value === selected ? "selected" : ""}>${option.label}</option>`)
    .join("");
  select.disabled = options.length === 1 && options[0].label === "不适用";
}

function bindSettingsPersistence(): void {
  for (const id of ["condaEnv", "pythonBin", "apiBase", "apiKey", "model", "cookies", "subtitleStrategy", "favoriteLimit"]) {
    document.querySelector<HTMLInputElement>(`#${id}`)?.addEventListener("input", saveSettings);
  }
  for (const id of ["extractKeyframes", "stockTerms", "enableOcr"]) {
    document.querySelector<HTMLInputElement>(`#${id}`)?.addEventListener("change", saveSettings);
  }
}

function loadSettings(): SavedSettings {
  try {
    const raw = localStorage.getItem(settingsKey);
    return { ...defaults, ...(raw ? JSON.parse(raw) : {}) };
  } catch {
    return defaults;
  }
}

function saveSettings(): void {
  const settings: SavedSettings = {
    condaEnv: inputValue("condaEnv") || defaults.condaEnv,
    pythonBin: inputValue("pythonBin") || defaults.pythonBin,
    apiBase: inputValue("apiBase") || defaults.apiBase,
    apiKey: inputValue("apiKey") || defaults.apiKey,
    model: inputValue("model") || defaults.model,
    cookies: inputValue("cookies"),
    outputRoot: inputValue("outputRoot"),
    subtitleStrategy: (inputValue("subtitleStrategy") || defaults.subtitleStrategy) as SubtitleStrategy,
    favoriteLimit: inputValue("favoriteLimit") || defaults.favoriteLimit,
    extractKeyframes: checkboxChecked("extractKeyframes"),
    stockTerms: checkboxChecked("stockTerms"),
    enableOcr: checkboxChecked("enableOcr"),
  };
  localStorage.setItem(settingsKey, JSON.stringify(settings));
}

function updateCookieStatus(result: string): void {
  const el = document.querySelector<HTMLParagraphElement>("#cookieStatus");
  if (!el) return;
  const line = result
    .split("\n")
    .find((entry) => entry.includes("Bilibili cookie file"));
  if (!line) {
    el.textContent = "Cookie 状态：待检查";
    return;
  }
  const status = line.startsWith("[OK]") ? "可用" : line.startsWith("[WARN]") ? "未配置或需关注" : "异常";
  const detail = line.includes(" - ") ? line.split(" - ", 2)[1].split(" Hint:", 1)[0].trim() : "";
  el.textContent = detail ? `Cookie 状态：${status}，${detail}` : `Cookie 状态：${status}`;
}

function setOutput(text: string): void {
  const output = document.querySelector<HTMLPreElement>("#output");
  if (output) output.textContent = text;
}

function appendOutput(text: string): void {
  const output = document.querySelector<HTMLPreElement>("#output");
  if (!output) return;
  output.textContent += text;
  output.scrollTop = output.scrollHeight;
}

function currentOutput(): string {
  return document.querySelector<HTMLPreElement>("#output")?.textContent ?? "";
}

function setState(text: string): void {
  const state = document.querySelector<HTMLSpanElement>("#runState");
  if (state) state.textContent = text;
}

function setWorkerRunning(running: boolean): void {
  isWorkerRunning = running;
  for (const id of ["checkEnv", "runDry", "runTask"]) {
    const button = document.querySelector<HTMLButtonElement>(`#${id}`);
    if (button) button.disabled = running;
  }
  const cancelButton = document.querySelector<HTMLButtonElement>("#cancelTask");
  if (cancelButton) cancelButton.disabled = !running;
}

function joinPath(root: string, subdir: string): string {
  const cleanRoot = root.replace(/\/+$/, "");
  if (!cleanRoot) return "";
  return `${cleanRoot}/${subdir}`;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function eyeIcon(): string {
  return `
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
      <path d="M2.8 12s3.4-6 9.2-6 9.2 6 9.2 6-3.4 6-9.2 6-9.2-6-9.2-6Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
      <circle cx="12" cy="12" r="2.6" fill="none" stroke="currentColor" stroke-width="2" />
    </svg>
  `;
}
