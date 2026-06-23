import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open, type OpenDialogOptions } from "@tauri-apps/plugin-dialog";
import {
  createHistoryEntry,
  loadTaskHistory,
  progressFromLine,
  saveTaskHistory,
  taskResultFromLog,
  upsertHistoryEntry,
  type ProgressEvent,
  type TaskHistoryEntry,
  type TaskHistoryStatus,
} from "./p1";
import "./styles.css";

type TaskType =
  | "bilibili-url"
  | "bilibili-favorite"
  | "bilibili-opus"
  | "bilibili-up-opus"
  | "web-url"
  | "source-file"
  | "ai-chat"
  | "paper-quickread"
  | "local-video"
  | "epub-export";

type SubtitleStrategy = "yt-dlp" | "web" | "asr";

type SavedSettings = {
  runtimeBackend: "managed" | "conda";
  condaEnv: string;
  pythonBin: string;
  apiBase: string;
  apiKey: string;
  model: string;
  asrModel: string;
  cookies: string;
  chromeProfile: string;
  outputRoot: string;
  subtitleStrategy: SubtitleStrategy;
  favoriteLimit: string;
  collectionType: "favorite" | "series";
  collectionId: string;
  collectionMid: string;
  extractKeyframes: boolean;
  dialogueDetection: boolean;
  keepOriginalSubtitles: boolean;
  recursiveSearch: boolean;
  overwriteOutputs: boolean;
  incognitoMode: boolean;
  stockTerms: boolean;
  enableOcr: boolean;
  webCaptureMode: "static" | "browser";
  browserExecutable: string;
  timeoutSeconds: string;
  retryCount: string;
  cooldownDelay: string;
  chunkChars: string;
  ocrResume: boolean;
};

type WorkerLogPayload = {
  line: string;
};

class TauriRuntimeUnavailableError extends Error {}

const settingsKey = "local-note-studio.settings.v1";
let isWorkerRunning = false;
let workerLogListenerReady: Promise<void> | null = null;
let taskHistory = loadTaskHistory();
let activeHistoryEntry: TaskHistoryEntry | null = null;

const taskLabels: Record<TaskType, string> = {
  "bilibili-url": "B站单链接",
  "bilibili-favorite": "B站收藏夹/系列",
  "bilibili-opus": "B站动态/充电动态",
  "bilibili-up-opus": "B站 UP 主图文批量",
  "web-url": "微信公众号/网页",
  "source-file": "Word/PDF整理",
  "ai-chat": "AI-Chat JSON",
  "paper-quickread": "论文速读",
  "local-video": "本地视频/音频",
  "epub-export": "目录导出 EPUB",
};

const taskHints: Record<TaskType, string> = {
  "bilibili-url": "输入一个 Bilibili 视频链接。Markdown 会直接写入本次输出目录；可选生成关键帧图文笔记，也可不保留原始字幕。",
  "bilibili-favorite": "先读取当前登录账号的收藏夹/系列并选择目标。批量中单条失败不会阻断其余条目，结束后可只重试失败项。",
  "bilibili-opus": "输入 B站动态或充电动态链接。会使用 B站 Cookie 调接口抓取正文，账号无权限时会明确报错。",
  "bilibili-up-opus": "输入 UP 主空间图文页链接或 UID。程序会分页读取、过滤视频动态，并逐篇下载图片、调用 Qwen 整理；处理数量为 0 时读取全部图文。",
  "web-url": "输入微信公众号文章或普通网页 URL。Qwen 整理会插入原文之上，并保留完整原文。",
  "source-file": "输入本地 .doc、.docx、.pdf、.pptx、.xlsx/.csv、.html、图片文件或包含这些文件的目录。批量图片和扫描 PDF 会显示文件/页进度，并可从检查点续跑；抽取后调用 Qwen 整理并保留原文。",
  "ai-chat": "输入 LM Studio 导出的 .conversation.json 文件，转换为 Markdown 对话笔记。",
  "paper-quickread": "输入论文 PDF 路径，生成速读笔记并保留全文翻译。",
  "local-video": "输入本地视频/音频文件路径，或一个媒体目录路径。Markdown 会直接写入本次输出目录；可选生成关键帧图文笔记，也可不保留原始字幕。",
  "epub-export": "输入一个 Markdown 笔记目录，递归导出为单个 EPUB。需要本机安装 pandoc，图片资源会按 Markdown 相对路径打包。",
};

const outputSubdirs: Record<TaskType, string> = {
  "bilibili-url": "Net/BiliBili",
  "bilibili-favorite": "Net/BiliBili",
  "bilibili-opus": "Net/BiliBili",
  "bilibili-up-opus": "Net/BiliBili",
  "web-url": "Net/WeChat",
  "source-file": "Inbox",
  "ai-chat": "AI/AI-Chat",
  "paper-quickread": "AI/_quickread/AI_paper",
  "local-video": "Net/BiliBili",
  "epub-export": "Exports/EPUB",
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
  "bilibili-opus": [{ value: "yt-dlp", label: "不适用" }],
  "bilibili-up-opus": [{ value: "yt-dlp", label: "不适用" }],
  "local-video": [
    { value: "yt-dlp", label: "同目录 SRT 字幕优先" },
    { value: "asr", label: "ASR 语音转写优先" },
  ],
  "web-url": [{ value: "yt-dlp", label: "不适用" }],
  "source-file": [{ value: "yt-dlp", label: "不适用" }],
  "ai-chat": [{ value: "yt-dlp", label: "不适用" }],
  "paper-quickread": [{ value: "yt-dlp", label: "不适用" }],
  "epub-export": [{ value: "yt-dlp", label: "不适用" }],
};

const defaults: SavedSettings = {
  runtimeBackend: "conda",
  condaEnv: "course-whisper",
  pythonBin: "python3",
  apiBase: "http://127.0.0.1:1234/v1",
  apiKey: "lm-studio",
  model: "qwen3.6-35b-a3b-nvfp4",
  asrModel: "",
  cookies: "",
  chromeProfile: "",
  outputRoot: "",
  subtitleStrategy: "yt-dlp",
  favoriteLimit: "1",
  collectionType: "favorite",
  collectionId: "",
  collectionMid: "",
  extractKeyframes: false,
  dialogueDetection: false,
  keepOriginalSubtitles: true,
  recursiveSearch: false,
  overwriteOutputs: false,
  incognitoMode: false,
  stockTerms: false,
  enableOcr: false,
  webCaptureMode: "static",
  browserExecutable: "",
  timeoutSeconds: "",
  retryCount: "",
  cooldownDelay: "",
  chunkChars: "",
  ocrResume: true,
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
            运行时后端
            <select id="runtimeBackend">
              <option value="managed" ${savedSettings.runtimeBackend === "managed" ? "selected" : ""}>应用托管环境</option>
              <option value="conda" ${savedSettings.runtimeBackend === "conda" ? "selected" : ""}>现有 Conda / Python（高级）</option>
            </select>
          </label>
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
            ASR 模型目录（可选）
            <div class="input-row">
              <input id="asrModel" value="${escapeHtml(savedSettings.asrModel)}" placeholder="选择已有 Whisper 模型" />
              <button id="chooseAsrModel" type="button" class="secondary compact-button">选择</button>
            </div>
          </label>
          <label>
            托管环境
            <div class="input-row runtime-row">
              <button id="runtimeStatus" type="button" class="secondary compact-button">查看状态</button>
              <button id="runtimeInstall" type="button" class="secondary compact-button">安装/修复</button>
              <button id="runtimeRemove" type="button" class="secondary compact-button">卸载</button>
            </div>
          </label>
          <label>
            B站 Cookie 文件
            <div class="input-row secret-row">
              <input id="cookies" type="password" value="${escapeHtml(savedSettings.cookies)}" placeholder="/path/to/bili_cookies.txt" autocomplete="off" />
              <button id="toggleCookies" type="button" class="secondary icon-button" title="显示 Cookie 文件路径" aria-label="显示 Cookie 文件路径">
                ${eyeIcon()}
              </button>
            </div>
          </label>
          <label class="full-row">
            Chrome 个人资料路径
            <div class="input-row profile-row">
              <input id="chromeProfile" type="password" value="${escapeHtml(savedSettings.chromeProfile)}" placeholder=".../Google/Chrome/Default" autocomplete="off" />
              <button id="toggleChromeProfile" type="button" class="secondary icon-button" title="显示 Chrome 个人资料路径" aria-label="显示 Chrome 个人资料路径">
                ${eyeIcon()}
              </button>
              <button id="chooseChromeProfile" type="button" class="secondary compact-button">选择</button>
              <button id="refreshCookies" type="button" class="secondary compact-button">刷新 Cookie</button>
            </div>
            <p class="field-note">填写当前登录 B站账号对应的 Chrome Profile，可在 chrome://version 查看“个人资料路径”。</p>
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
            <button id="checkBilibiliAccess" type="button" class="secondary hidden">验证B站目标权限</button>
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
          <label id="outputFilenameField" class="hidden">
            输出文件名（可选）
            <input id="outputFilename" placeholder="留空使用默认命名；可省略 .md/.epub" />
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
            <span id="batchLimitLabel">批量处理数量（0=全部）</span>
            <input id="favoriteLimit" type="number" min="0" step="1" value="${escapeHtml(savedSettings.favoriteLimit)}" placeholder="1" />
          </label>
          <label id="collectionField" class="full-row hidden">
            收藏夹/系列
            <div class="input-row collection-row">
              <select id="collectionSelect" data-type="${escapeHtml(savedSettings.collectionType)}" data-id="${escapeHtml(savedSettings.collectionId)}" data-mid="${escapeHtml(savedSettings.collectionMid)}">
                <option value="">点击“读取列表”获取当前账号的收藏夹/系列</option>
              </select>
              <button id="loadCollections" type="button" class="secondary compact-button">读取列表</button>
              <button id="retryFailed" type="button" class="secondary compact-button hidden">只重试失败项</button>
            </div>
            <p id="batchResult" class="field-note">尚未运行批量任务。</p>
          </label>
          <label id="extractKeyframesField" class="checkbox-field hidden">
            <span>关键帧图文笔记</span>
            <input id="extractKeyframes" type="checkbox" ${savedSettings.extractKeyframes ? "checked" : ""} />
          </label>
          <label id="dialogueDetectionField" class="checkbox-field hidden">
            <span>对话检测与角色标注</span>
            <input id="dialogueDetection" type="checkbox" ${savedSettings.dialogueDetection ? "checked" : ""} />
          </label>
          <label id="keepOriginalSubtitlesField" class="checkbox-field hidden">
            <span>保留原始字幕</span>
            <input id="keepOriginalSubtitles" type="checkbox" ${savedSettings.keepOriginalSubtitles ? "checked" : ""} />
          </label>
          <label id="recursiveSearchField" class="checkbox-field hidden">
            <span>递归扫描目录</span>
            <input id="recursiveSearch" type="checkbox" ${savedSettings.recursiveSearch ? "checked" : ""} />
          </label>
          <label class="checkbox-field">
            <span>覆盖同名文件</span>
            <input id="overwriteOutputs" type="checkbox" ${savedSettings.overwriteOutputs ? "checked" : ""} />
          </label>
          <label class="checkbox-field incognito-field">
            <span>隐身模式</span>
            <input id="incognitoMode" type="checkbox" ${savedSettings.incognitoMode ? "checked" : ""} />
          </label>
          <p class="field-note full-row incognito-note">开启后仍会生成笔记并保留任务历史，但不会读取或写入 source/video/quickread Manifest、关键帧 Manifest、B站已处理列表和批量失败状态。</p>
          <label id="stockTermsField" class="checkbox-field">
            <span>A股术语校验</span>
            <input id="stockTerms" type="checkbox" ${savedSettings.stockTerms ? "checked" : ""} />
          </label>
          <label id="enableOcrField" class="checkbox-field hidden">
            <span>启用 OCR</span>
            <input id="enableOcr" type="checkbox" ${savedSettings.enableOcr ? "checked" : ""} />
          </label>
          <label id="ocrResumeField" class="checkbox-field hidden">
            <span>OCR 中断后续跑</span>
            <input id="ocrResume" type="checkbox" ${savedSettings.ocrResume ? "checked" : ""} />
          </label>
          <label id="webCaptureModeField" class="hidden">
            网页采集方式
            <select id="webCaptureMode">
              <option value="static" ${savedSettings.webCaptureMode === "static" ? "selected" : ""}>静态 HTTP（不读取浏览器 Cookie）</option>
              <option value="browser" ${savedSettings.webCaptureMode === "browser" ? "selected" : ""}>指定浏览器会话（登录/JS 页面）</option>
            </select>
          </label>
          <label id="browserExecutableField" class="hidden">
            Chrome / Chromium 可执行文件（可选）
            <input id="browserExecutable" value="${escapeHtml(savedSettings.browserExecutable)}" placeholder="留空自动查找 Google Chrome" />
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
        <details class="advanced-options">
          <summary>长任务参数覆盖</summary>
          <div class="form-grid compact advanced-grid">
            <label>超时（秒）<input id="timeoutSeconds" type="number" min="0" value="${escapeHtml(savedSettings.timeoutSeconds)}" placeholder="使用稳定默认值" /></label>
            <label>重试次数<input id="retryCount" type="number" min="0" value="${escapeHtml(savedSettings.retryCount)}" placeholder="使用稳定默认值" /></label>
            <label>模型冷却（秒）<input id="cooldownDelay" type="number" min="0" value="${escapeHtml(savedSettings.cooldownDelay)}" placeholder="使用稳定默认值" /></label>
            <label>分块字符数<input id="chunkChars" type="number" min="0" value="${escapeHtml(savedSettings.chunkChars)}" placeholder="使用稳定默认值" /></label>
          </div>
        </details>
      </section>

      <section id="progressPanel" class="panel hidden">
        <div class="panel-header compact-header"><h2>任务进度</h2><span id="progressText">等待进度事件</span></div>
        <progress id="taskProgress" max="100" value="0"></progress>
      </section>

      <section id="resultPanel" class="panel">
        <div class="panel-header">
          <div><span class="step">4</span><h2 id="resultTitle">本次输出</h2></div>
          <button id="copyOutputDir" type="button" class="secondary">复制输出目录</button>
        </div>
        <div id="resultList" class="result-list"><p class="empty-state">任务完成后会在这里列出所有新增或更新的文件。</p></div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div><span class="step">5</span><h2>处理记录与文件状态</h2></div>
          <button id="refreshManifests" type="button" class="secondary">重新检查</button>
        </div>
        <p class="manifest-help">这是程序用于避免重复处理的本地记录。通常只需关注“处理失败”和“输出缺失”；“本次跳过”表示文件已经存在，因此没有重复生成。</p>
        <div id="manifestSummary" class="summary-chips"></div>
        <div id="manifestList" class="manifest-list"><p class="empty-state">点击“重新检查”，查看视频、文档、论文和 B站批次的处理记录。</p></div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div><span class="step">6</span><h2>任务历史与恢复</h2></div>
          <button id="clearHistory" type="button" class="secondary">清空历史</button>
        </div>
        <div id="historyList" class="history-list"></div>
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

try {
  hydrateTaskControls();
  hydrateTaskOutput();
  bindSettingsPersistence();
  renderHistory();
} catch (error) {
  setState("界面数据恢复失败");
  setOutput(`本地界面数据恢复失败，但依赖检查和任务按钮仍可使用。\n${errorMessage(error)}\n`);
}

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
document.querySelector<HTMLButtonElement>("#checkEnv")?.addEventListener("click", () => runEnvironmentCheck());
document.querySelector<HTMLButtonElement>("#refreshCookies")?.addEventListener("click", () => refreshBilibiliCookies());
document.querySelector<HTMLButtonElement>("#loadCollections")?.addEventListener("click", () => loadBilibiliCollections());
document.querySelector<HTMLButtonElement>("#checkBilibiliAccess")?.addEventListener("click", () => checkBilibiliTargetAccess());
document.querySelector<HTMLButtonElement>("#retryFailed")?.addEventListener("click", () => runTask(false, true));
document.querySelector<HTMLSelectElement>("#collectionSelect")?.addEventListener("change", syncSelectedCollection);
document.querySelector<HTMLButtonElement>("#runDry")?.addEventListener("click", () => runTask(true));
document.querySelector<HTMLButtonElement>("#runTask")?.addEventListener("click", () => runTask(false));
document.querySelector<HTMLButtonElement>("#cancelTask")?.addEventListener("click", () => cancelWorker());
document.querySelector<HTMLButtonElement>("#copyOutputDir")?.addEventListener("click", () => copyPath(inputValue("outputDir")));
document.querySelector<HTMLButtonElement>("#refreshManifests")?.addEventListener("click", () => refreshManifestStatus());
document.querySelector<HTMLButtonElement>("#clearHistory")?.addEventListener("click", clearHistory);
document.querySelector<HTMLButtonElement>("#runtimeStatus")?.addEventListener("click", () => manageRuntime("status"));
document.querySelector<HTMLButtonElement>("#runtimeInstall")?.addEventListener("click", () => manageRuntime("install"));
document.querySelector<HTMLButtonElement>("#runtimeRemove")?.addEventListener("click", () => manageRuntime("remove"));
document.querySelector<HTMLButtonElement>("#chooseOutputRoot")?.addEventListener("click", () => chooseDirectory("outputRoot"));
document.querySelector<HTMLButtonElement>("#chooseOutputDir")?.addEventListener("click", () => chooseDirectory("outputDir"));
document.querySelector<HTMLButtonElement>("#chooseSourceFile")?.addEventListener("click", () => chooseSourceFile());
document.querySelector<HTMLButtonElement>("#chooseSourceDir")?.addEventListener("click", () => chooseDirectory("source"));
document.querySelector<HTMLButtonElement>("#chooseChromeProfile")?.addEventListener("click", () => chooseDirectory("chromeProfile"));
document.querySelector<HTMLButtonElement>("#chooseAsrModel")?.addEventListener("click", () => chooseDirectory("asrModel"));
document.querySelector<HTMLButtonElement>("#toggleApiKey")?.addEventListener("click", () => toggleSecretField("apiKey", "toggleApiKey", "API Key"));
document.querySelector<HTMLButtonElement>("#toggleCookies")?.addEventListener("click", () => toggleSecretField("cookies", "toggleCookies", "Cookie 文件路径"));
document.querySelector<HTMLButtonElement>("#toggleChromeProfile")?.addEventListener("click", () =>
  toggleSecretField("chromeProfile", "toggleChromeProfile", "Chrome 个人资料路径"),
);

if (!hasTauriRuntime()) {
  setState("浏览器预览");
  setOutput(tauriRuntimeHint());
} else {
  setState("准备检查依赖...");
  setOutput("应用已启动，正在自动检查运行环境...\n");
  window.setTimeout(() => {
    void runEnvironmentCheck();
  }, 100);
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

function toggleSecretField(inputId: string, buttonId: string, label: string): void {
  const input = document.querySelector<HTMLInputElement>(`#${inputId}`);
  const button = document.querySelector<HTMLButtonElement>(`#${buttonId}`);
  if (!input || !button) return;
  const willShow = input.type === "password";
  input.type = willShow ? "text" : "password";
  button.title = willShow ? `隐藏 ${label}` : `显示 ${label}`;
  button.setAttribute("aria-label", button.title);
  input.focus();
}

function currentTask(): TaskType {
  return (inputValue("taskType") || "bilibili-url") as TaskType;
}

function payload(dryRun: boolean, retryFailed = false) {
  const collection = document.querySelector<HTMLSelectElement>("#collectionSelect");
  const task = currentTask();
  return {
    task,
    runtime_backend: inputValue("runtimeBackend"),
    source: inputValue("source"),
    output_dir: inputValue("outputDir"),
    output_filename: inputValue("outputFilename"),
    conda_env: inputValue("condaEnv"),
    python_bin: inputValue("pythonBin"),
    api_base: inputValue("apiBase"),
    api_key: inputValue("apiKey"),
    model: inputValue("model"),
    asr_model: inputValue("asrModel"),
    cookies: inputValue("cookies"),
    browser_profile: inputValue("chromeProfile"),
    subtitle_strategy: inputValue("subtitleStrategy"),
    favorite_limit: retryFailed ? "0" : inputValue("favoriteLimit"),
    collection_type: collection?.selectedOptions[0]?.dataset.type || collection?.dataset.type || "favorite",
    collection_id: task === "bilibili-favorite" ? collection?.value || collection?.dataset.id || "" : "",
    collection_mid: task === "bilibili-favorite" ? collection?.selectedOptions[0]?.dataset.mid || collection?.dataset.mid || "" : "",
    retry_failed: retryFailed,
    extract_keyframes: checkboxChecked("extractKeyframes"),
    dialogue_detection: checkboxChecked("dialogueDetection"),
    keep_original_subtitles: checkboxChecked("keepOriginalSubtitles"),
    recursive_search: checkboxChecked("recursiveSearch"),
    overwrite_outputs: checkboxChecked("overwriteOutputs"),
    incognito_mode: checkboxChecked("incognitoMode"),
    stock_terms: checkboxChecked("stockTerms"),
    enable_ocr: checkboxChecked("enableOcr"),
    web_capture_mode: inputValue("webCaptureMode") || "static",
    browser_executable: inputValue("browserExecutable"),
    timeout_seconds: inputValue("timeoutSeconds"),
    retry_count: inputValue("retryCount"),
    cooldown_delay: inputValue("cooldownDelay"),
    chunk_chars: inputValue("chunkChars"),
    ocr_resume: checkboxChecked("ocrResume"),
    dry_run: dryRun,
  };
}

async function runEnvironmentCheck(): Promise<void> {
  if (isWorkerRunning) {
    setState("已有任务正在运行");
    return;
  }
  setWorkerRunning(true);
  saveSettings();
  setState("检查依赖中...");
  setOutput("");
  appendOutput("正在检查所选 conda/Python 环境...\n");
  try {
    const request = { ...payload(false), task: "env-check", source: "" };
    const result = await invokeWorker(request);
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

async function refreshBilibiliCookies(): Promise<void> {
  if (isWorkerRunning) return;
  const profile = inputValue("chromeProfile");
  if (!profile) {
    setState("缺少 Chrome Profile");
    setOutput("请填写或选择 Chrome 个人资料路径。可在 chrome://version 查看“个人资料路径”。");
    return;
  }
  if (!inputValue("cookies")) {
    setInputValue("cookies", "./bili_cookies.txt");
  }
  saveSettings();
  setWorkerRunning(true);
  setState("正在刷新 Cookie...");
  setOutput("正在从指定 Chrome Profile 读取 B站 Cookie...\n");
  try {
    await invokeWorker({ ...payload(false), task: "refresh-bilibili-cookies", source: "", output_dir: "" });
    appendOutput("\nCookie 导出完成，正在校验登录态...\n");
    const checkResult = await invokeWorker({ ...payload(false), task: "bilibili-cookie-status", source: "", output_dir: "" });
    setState(checkResult.includes("[OK] Bilibili cookie file") ? "Cookie 刷新完成" : "Cookie 需要关注");
  } catch (error) {
    appendOutput(`\n刷新失败：${errorMessage(error)}\n`);
    setState(error instanceof TauriRuntimeUnavailableError ? "浏览器预览" : "Cookie 刷新失败");
  } finally {
    setWorkerRunning(false);
  }
}

type BilibiliCollection = { type: "favorite" | "series"; id: string; mid: string; title: string; count: number };
type CollectionResponse = { mid: string; name: string; items: BilibiliCollection[]; warnings?: string[] };

function structuredJson<T>(text: string, prefix: string): T | null {
  const line = text.split("\n").find((item) => item.startsWith(prefix));
  if (!line) return null;
  try {
    return JSON.parse(line.slice(prefix.length)) as T;
  } catch {
    return null;
  }
}

async function loadBilibiliCollections(): Promise<void> {
  if (isWorkerRunning) return;
  setWorkerRunning(true);
  setState("读取收藏夹/系列中...");
  setOutput("正在验证登录态并读取收藏夹/系列...\n");
  try {
    const result = await invokeWorker({ ...payload(false), task: "bilibili-collections", source: "", output_dir: "" });
    const data = structuredJson<CollectionResponse>(result, "COLLECTIONS_JSON:");
    if (!data) throw new Error("worker 未返回可识别的收藏夹列表");
    const select = document.querySelector<HTMLSelectElement>("#collectionSelect");
    if (!select) return;
    const previous = select.dataset.id || savedSettings.collectionId;
    select.innerHTML = data.items.length
      ? data.items.map((item) => {
          const label = `${item.type === "series" ? "系列" : "收藏夹"} · ${item.title}（${item.count}）`;
          return `<option value="${escapeHtml(item.id)}" data-type="${item.type}" data-mid="${escapeHtml(item.mid)}" ${item.id === previous ? "selected" : ""}>${escapeHtml(label)}</option>`;
        }).join("")
      : '<option value="">当前账号没有可读取的收藏夹/系列</option>';
    syncSelectedCollection();
    setOutput(`账号：${data.name || data.mid}\n读取到 ${data.items.length} 个收藏夹/系列。${data.warnings?.length ? `\n${data.warnings.join("\n")}` : ""}`);
    setState("列表读取完成");
  } catch (error) {
    appendOutput(`\n读取失败：${errorMessage(error)}\n`);
    setState("列表读取失败");
  } finally {
    setWorkerRunning(false);
  }
}

function syncSelectedCollection(): void {
  const select = document.querySelector<HTMLSelectElement>("#collectionSelect");
  const option = select?.selectedOptions[0];
  if (!select || !option) return;
  select.dataset.type = option.dataset.type || "favorite";
  select.dataset.id = select.value;
  select.dataset.mid = option.dataset.mid || "";
  saveSettings();
}

async function checkBilibiliTargetAccess(): Promise<void> {
  if (isWorkerRunning) return;
  setWorkerRunning(true);
  setState("验证目标权限中...");
  setOutput("正在分别检查登录态和目标内容权限...\n");
  try {
    const result = await invokeWorker({ ...payload(false), task: "bilibili-access-check", output_dir: "" });
    if (!currentOutput().trim()) setOutput(result);
    setState("目标权限可用");
  } catch (error) {
    appendOutput(`\n权限验证失败：${errorMessage(error)}\n`);
    setState("目标权限不可用");
  } finally {
    setWorkerRunning(false);
  }
}

function updateBatchResult(text: string): void {
  const data = structuredJson<{ total: number; processed?: number; success: number; failed: number; current: string }>(text, "BATCH_RESULT_JSON:");
  if (!data) return;
  const summary = document.querySelector<HTMLParagraphElement>("#batchResult");
  if (summary) summary.textContent = `总数 ${data.total} · 已处理 ${data.processed ?? data.success + data.failed} · 成功 ${data.success} · 失败 ${data.failed}${data.current ? ` · 当前/最后：${data.current}` : ""}`;
  document.querySelector<HTMLButtonElement>("#retryFailed")?.classList.toggle("hidden", data.failed <= 0);
}

async function runTask(dryRun: boolean, retryFailed = false, retryOf?: string): Promise<void> {
  if (isWorkerRunning) return;
  saveSettings();
  const task = currentTask();
  if (retryFailed && task === "bilibili-favorite" && checkboxChecked("incognitoMode")) {
    setState("隐身模式不读取失败状态");
    setOutput("“只重试失败项”依赖上次保存的 B站批量失败状态。请关闭隐身模式后再重试。\n");
    return;
  }
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
  if (task === "bilibili-favorite" && !String((payload(false) as { collection_id?: string }).collection_id || "")) {
    setState("未选择收藏夹/系列");
    setOutput("请先点击“读取列表”，然后选择一个收藏夹或系列。");
    return;
  }

  const request = payload(dryRun, retryFailed);
  if (!dryRun) {
    activeHistoryEntry = createHistoryEntry(task, request as Record<string, unknown>, retryOf);
    taskHistory = upsertHistoryEntry(taskHistory, activeHistoryEntry);
    saveTaskHistory(taskHistory);
    renderHistory();
  }
  setWorkerRunning(true);
  setState(dryRun ? "生成预览中..." : "任务运行中...");
  setOutput("");
  appendOutput(dryRun ? "正在生成命令预览...\n" : "任务已启动，日志会实时追加到这里。\n");
  try {
    const result = await invokeWorker(request);
    if (!currentOutput().trim()) setOutput(result || "(worker 没有返回输出)");
    updateBatchResult(result || currentOutput());
    const taskResult = taskResultFromLog(result || currentOutput());
    if (taskResult) renderOutputs(taskResult.outputs);
    if (activeHistoryEntry) {
      activeHistoryEntry.status = "completed";
      activeHistoryEntry.outputs = taskResult?.outputs ?? [];
    }
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
    if (activeHistoryEntry) {
      activeHistoryEntry.status = message.startsWith("Task cancelled.") ? "cancelled" : "failed";
      activeHistoryEntry.error = message;
    }
  } finally {
    updateBatchResult(currentOutput());
    if (activeHistoryEntry) {
      activeHistoryEntry.log = currentOutput();
      activeHistoryEntry.endedAt = new Date().toISOString();
      taskHistory = upsertHistoryEntry(taskHistory, activeHistoryEntry);
      saveTaskHistory(taskHistory);
      activeHistoryEntry = null;
      renderHistory();
    }
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

async function invokeWorkerQuiet(request: object): Promise<string> {
  if (!hasTauriRuntime()) {
    throw new TauriRuntimeUnavailableError(tauriRuntimeHint());
  }
  return invoke<string>("run_worker", { request: JSON.stringify(request) });
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

async function chooseDirectory(targetId: "outputRoot" | "outputDir" | "source" | "chromeProfile" | "asrModel"): Promise<void> {
  await choosePath(targetId, { directory: true, multiple: false });
}

async function chooseSourceFile(): Promise<void> {
  const task = currentTask();
  let filters: OpenDialogOptions["filters"];
  if (task === "paper-quickread") {
    filters = [{ name: "PDF", extensions: ["pdf"] }];
  } else if (task === "local-video") {
    filters = [{ name: "Media", extensions: ["mp4", "mkv", "mov", "webm", "flv", "mp3", "m4a", "wav"] }];
  } else if (task === "ai-chat") {
    filters = [{ name: "AI Chat JSON", extensions: ["json"] }];
  } else if (task === "source-file") {
    filters = [
      {
        name: "Supported Sources",
        extensions: ["doc", "docx", "pdf", "pptx", "xlsx", "csv", "tsv", "html", "htm", "json", "png", "jpg", "jpeg", "webp", "heic", "bmp", "gif", "tif", "tiff"],
      },
    ];
  }
  await choosePath("source", { multiple: false, filters });
}

async function choosePath(
  targetId: "outputRoot" | "outputDir" | "source" | "chromeProfile" | "asrModel",
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
    const progress = progressFromLine(event.payload.line);
    if (progress) renderProgress(progress);
  }).then(() => undefined);
  return workerLogListenerReady;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function hasTauriRuntime(): boolean {
  return isTauri();
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
    favoriteLimitField.classList.toggle("hidden", !["bilibili-favorite", "bilibili-up-opus"].includes(task));
    const label = document.querySelector<HTMLElement>("#batchLimitLabel");
    if (label) {
      label.textContent = task === "bilibili-favorite" ? "收藏夹处理数量（0=全部）" : "图文处理数量（0=全部）";
    }
  }
  document.querySelector<HTMLElement>("#collectionField")?.classList.toggle("hidden", task !== "bilibili-favorite");
  document.querySelector<HTMLElement>("#checkBilibiliAccess")?.classList.toggle(
    "hidden",
    !["bilibili-url", "bilibili-favorite", "bilibili-opus"].includes(task),
  );
  const extractKeyframesField = document.querySelector<HTMLElement>("#extractKeyframesField");
  if (extractKeyframesField) {
    extractKeyframesField.classList.toggle("hidden", !["bilibili-url", "bilibili-favorite", "local-video"].includes(task));
  }
  const dialogueDetectionField = document.querySelector<HTMLElement>("#dialogueDetectionField");
  if (dialogueDetectionField) {
    dialogueDetectionField.classList.toggle("hidden", !["bilibili-url", "bilibili-favorite", "local-video"].includes(task));
  }
  const keepOriginalSubtitlesField = document.querySelector<HTMLElement>("#keepOriginalSubtitlesField");
  if (keepOriginalSubtitlesField) {
    keepOriginalSubtitlesField.classList.toggle("hidden", !["bilibili-url", "bilibili-favorite", "local-video"].includes(task));
  }
  const recursiveSearchField = document.querySelector<HTMLElement>("#recursiveSearchField");
  if (recursiveSearchField) {
    recursiveSearchField.classList.toggle("hidden", task !== "local-video");
  }
  const stockTermsField = document.querySelector<HTMLElement>("#stockTermsField");
  if (stockTermsField) {
    stockTermsField.classList.toggle(
      "hidden",
      !["bilibili-url", "bilibili-favorite", "bilibili-up-opus", "local-video", "web-url", "source-file"].includes(task),
    );
  }
  const enableOcrField = document.querySelector<HTMLElement>("#enableOcrField");
  if (enableOcrField) {
    enableOcrField.classList.toggle("hidden", task !== "source-file");
  }
  document.querySelector<HTMLElement>("#ocrResumeField")?.classList.toggle("hidden", task !== "source-file");
  document.querySelector<HTMLElement>("#webCaptureModeField")?.classList.toggle("hidden", task !== "web-url");
  document.querySelector<HTMLElement>("#browserExecutableField")?.classList.toggle(
    "hidden",
    task !== "web-url" || inputValue("webCaptureMode") !== "browser",
  );
  const outputFilenameField = document.querySelector<HTMLElement>("#outputFilenameField");
  if (outputFilenameField) {
    outputFilenameField.classList.toggle(
      "hidden",
      !["bilibili-url", "bilibili-opus", "web-url", "source-file", "ai-chat", "paper-quickread", "local-video", "epub-export"].includes(task),
    );
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
  for (const id of [
    "runtimeBackend",
    "condaEnv",
    "pythonBin",
    "apiBase",
    "apiKey",
    "model",
    "asrModel",
    "cookies",
    "chromeProfile",
    "subtitleStrategy",
    "favoriteLimit",
    "webCaptureMode",
    "browserExecutable",
    "timeoutSeconds",
    "retryCount",
    "cooldownDelay",
    "chunkChars",
  ]) {
    document.querySelector<HTMLInputElement>(`#${id}`)?.addEventListener("input", saveSettings);
  }
  document.querySelector<HTMLSelectElement>("#webCaptureMode")?.addEventListener("change", hydrateTaskControls);
  for (const id of [
    "extractKeyframes",
    "dialogueDetection",
    "keepOriginalSubtitles",
    "recursiveSearch",
    "overwriteOutputs",
    "incognitoMode",
    "stockTerms",
    "enableOcr",
    "ocrResume",
  ]) {
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
  const collection = document.querySelector<HTMLSelectElement>("#collectionSelect");
  const settings: SavedSettings = {
    runtimeBackend: (inputValue("runtimeBackend") || defaults.runtimeBackend) as "managed" | "conda",
    condaEnv: inputValue("condaEnv") || defaults.condaEnv,
    pythonBin: inputValue("pythonBin") || defaults.pythonBin,
    apiBase: inputValue("apiBase") || defaults.apiBase,
    apiKey: inputValue("apiKey") || defaults.apiKey,
    model: inputValue("model") || defaults.model,
    asrModel: inputValue("asrModel"),
    cookies: inputValue("cookies"),
    chromeProfile: inputValue("chromeProfile"),
    outputRoot: inputValue("outputRoot"),
    subtitleStrategy: (inputValue("subtitleStrategy") || defaults.subtitleStrategy) as SubtitleStrategy,
    favoriteLimit: inputValue("favoriteLimit") || defaults.favoriteLimit,
    collectionType: (collection?.dataset.type || defaults.collectionType) as "favorite" | "series",
    collectionId: collection?.value || collection?.dataset.id || "",
    collectionMid: collection?.selectedOptions[0]?.dataset.mid || collection?.dataset.mid || "",
    extractKeyframes: checkboxChecked("extractKeyframes"),
    dialogueDetection: checkboxChecked("dialogueDetection"),
    keepOriginalSubtitles: checkboxChecked("keepOriginalSubtitles"),
    recursiveSearch: checkboxChecked("recursiveSearch"),
    overwriteOutputs: checkboxChecked("overwriteOutputs"),
    incognitoMode: checkboxChecked("incognitoMode"),
    stockTerms: checkboxChecked("stockTerms"),
    enableOcr: checkboxChecked("enableOcr"),
    webCaptureMode: (inputValue("webCaptureMode") || defaults.webCaptureMode) as "static" | "browser",
    browserExecutable: inputValue("browserExecutable"),
    timeoutSeconds: inputValue("timeoutSeconds"),
    retryCount: inputValue("retryCount"),
    cooldownDelay: inputValue("cooldownDelay"),
    chunkChars: inputValue("chunkChars"),
    ocrResume: checkboxChecked("ocrResume"),
  };
  localStorage.setItem(settingsKey, JSON.stringify(settings));
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
  for (const id of [
    "checkEnv",
    "refreshCookies",
    "loadCollections",
    "checkBilibiliAccess",
    "retryFailed",
    "runDry",
    "runTask",
    "refreshManifests",
    "runtimeInstall",
    "runtimeRemove",
  ]) {
    const button = document.querySelector<HTMLButtonElement>(`#${id}`);
    if (button) button.disabled = running;
  }
  const cancelButton = document.querySelector<HTMLButtonElement>("#cancelTask");
  if (cancelButton) cancelButton.disabled = !running;
}

function renderProgress(progress: ProgressEvent): void {
  document.querySelector<HTMLElement>("#progressPanel")?.classList.remove("hidden");
  const bar = document.querySelector<HTMLProgressElement>("#taskProgress");
  const current = Math.max(0, Number(progress.current) || 0);
  const total = Math.max(1, Number(progress.total) || 1);
  if (bar) bar.value = Math.min(100, (current / total) * 100);
  const details = [progress.phase, `${current}/${total}`, progress.backend, progress.resumed ? "从检查点续跑" : "", progress.label]
    .filter(Boolean)
    .join(" · ");
  const text = document.querySelector<HTMLElement>("#progressText");
  if (text) text.textContent = details;
}

function renderOutputs(paths: string[], title = "本次输出", focus = false): void {
  const target = document.querySelector<HTMLElement>("#resultList");
  if (!target) return;
  const heading = document.querySelector<HTMLElement>("#resultTitle");
  if (heading) heading.textContent = title;
  if (!paths.length) {
    target.innerHTML = '<p class="empty-state">本次没有新增或更新文件，可能命中了跳过策略。</p>';
  } else {
    target.innerHTML = paths
      .map(
        (path) => `<article class="result-item">
          <div><strong>${escapeHtml(path.split("/").at(-1) || path)}</strong><small>${escapeHtml(path)}</small></div>
          <div class="row-actions">
            ${path.toLowerCase().endsWith(".md") ? `<button type="button" class="secondary" data-output-action="open" data-path="${encodeURIComponent(path)}">打开 Markdown</button>` : ""}
            <button type="button" class="secondary" data-output-action="reveal" data-path="${encodeURIComponent(path)}">Finder 中显示</button>
            <button type="button" class="secondary" data-output-action="copy" data-path="${encodeURIComponent(path)}">复制路径</button>
          </div>
        </article>`,
      )
      .join("");
  }
  target.querySelectorAll<HTMLButtonElement>("button[data-output-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const path = decodeURIComponent(button.dataset.path || "");
      const action = button.dataset.outputAction;
      if (action === "copy") void copyPath(path);
      else void invoke(action === "open" ? "open_path" : "reveal_path", { path });
    });
  });
  if (focus) {
    const panel = document.querySelector<HTMLElement>("#resultPanel");
    panel?.classList.remove("result-highlight");
    window.requestAnimationFrame(() => {
      panel?.classList.add("result-highlight");
      panel?.scrollIntoView({ behavior: "smooth", block: "start" });
      window.setTimeout(() => panel?.classList.remove("result-highlight"), 1400);
    });
  }
}

async function copyPath(path: string): Promise<void> {
  if (!path) return;
  try {
    await navigator.clipboard.writeText(path);
    setState("路径已复制");
  } catch {
    setOutput(path);
    setState("请从日志区复制路径");
  }
}

type ManifestStatus = {
  manifests: Array<{
    path: string;
    name: string;
    counts: Record<"processed" | "skipped" | "failed" | "rebuild", number>;
    items: Array<{ source: string; output: string; status: string; error: string; reason?: string }>;
    error?: string;
  }>;
  totals: Record<"processed" | "skipped" | "failed" | "rebuild", number>;
};

async function refreshManifestStatus(): Promise<void> {
  if (isWorkerRunning) return;
  setWorkerRunning(true);
  setState("读取 Manifest...");
  try {
    const result = await invokeWorkerQuiet({ ...payload(false), task: "manifest-status", source: inputValue("outputRoot") });
    const data = structuredJson<ManifestStatus>(result, "MANIFEST_STATUS_JSON:");
    if (!data) throw new Error("worker 未返回 Manifest 状态");
    renderManifestStatus(data);
    setState("Manifest 已刷新");
  } catch (error) {
    setState("Manifest 读取失败");
    appendOutput(`\nManifest 读取失败：${errorMessage(error)}\n`);
  } finally {
    setWorkerRunning(false);
  }
}

function renderManifestStatus(data: ManifestStatus): void {
  const summary = document.querySelector<HTMLElement>("#manifestSummary");
  if (summary) {
    summary.innerHTML = [
      ["记录正常", data.totals.processed, "ok"],
      ["本次跳过", data.totals.skipped, "muted"],
      ["处理失败", data.totals.failed, "danger"],
      ["输出缺失", data.totals.rebuild, "warning"],
    ]
      .map(([label, count, tone]) => `<span class="chip ${tone}">${label} ${count}</span>`)
      .join("");
  }
  const list = document.querySelector<HTMLElement>("#manifestList");
  if (!list) return;
  list.innerHTML = data.manifests.length
    ? [...data.manifests]
        .sort((left, right) => right.counts.failed + right.counts.rebuild - (left.counts.failed + left.counts.rebuild))
        .map((manifest) => {
          const label = manifestLabel(manifest.name);
          const total = Object.values(manifest.counts).reduce((sum, count) => sum + count, 0);
          const attention = manifest.counts.failed + manifest.counts.rebuild;
          const notableItems = manifest.items.filter((item) => item.status !== "processed").slice(0, 100);
          const health = attention > 0 ? `${attention} 条需要处理` : "状态正常";
          return `<details class="manifest-card ${attention > 0 ? "has-issues" : ""}">
            <summary>
              <span class="manifest-title"><strong>${escapeHtml(label.title)}</strong><small>${escapeHtml(label.description)}</small></span>
              <span class="manifest-health ${attention > 0 ? "warning" : "ok"}">${total} 条记录 · ${health}</span>
            </summary>
            <div class="manifest-detail">
              <p class="manifest-counts">正常 ${manifest.counts.processed} · 本次跳过 ${manifest.counts.skipped} · 失败 ${manifest.counts.failed} · 输出缺失 ${manifest.counts.rebuild}</p>
              ${manifest.error ? `<p class="error-text">读取记录失败：${escapeHtml(manifest.error)}</p>` : ""}
              ${notableItems.length
                ? `<div class="manifest-items">${notableItems
                    .map((item) => `<div><span class="status ${escapeHtml(item.status)}">${manifestStatusLabel(item.status)}</span><code>${escapeHtml(item.source || item.output || "未命名条目")}</code>${item.reason || item.error ? `<small>${escapeHtml(item.reason || item.error)}</small>` : ""}</div>`)
                    .join("")}</div>`
                : '<p class="empty-state">没有需要处理的条目。</p>'}
              <small class="manifest-path">记录文件：${escapeHtml(manifest.path)}</small>
            </div>
          </details>`;
        })
        .join("")
    : '<p class="empty-state">尚未找到 Manifest。运行一次支持增量状态的任务后再刷新。</p>';
}

function manifestLabel(name: string): { title: string; description: string } {
  const labels: Record<string, { title: string; description: string }> = {
    "quickread-manifest.json": { title: "论文速读", description: "论文 PDF 的速读与翻译记录" },
    "source-manifest.json": { title: "文档与网页", description: "文档、图片、网页转换及 Qwen 整理记录" },
    "video-manifest.json": { title: "视频笔记", description: "B站与本地媒体的笔记和关键帧记录" },
    "B站增量状态": { title: "B站已处理列表", description: "批量任务用它避免重复处理同一视频" },
    "B站批量失败状态": { title: "B站失败列表", description: "可用于“只重试失败项”的批次记录" },
  };
  return labels[name] ?? { title: name, description: "本地增量处理记录" };
}

function manifestStatusLabel(status: string): string {
  return {
    processed: "正常",
    skipped: "已跳过",
    failed: "失败",
    rebuild: "输出缺失",
  }[status] ?? status;
}

const historyStatusLabels: Record<TaskHistoryStatus, string> = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

function renderHistory(): void {
  const target = document.querySelector<HTMLElement>("#historyList");
  if (!target) return;
  target.innerHTML = taskHistory.length
    ? taskHistory
        .map((entry) => {
          const source = String(entry.request.source || "");
          return `<article class="history-item">
            <div><span class="status ${entry.status}">${historyStatusLabels[entry.status]}</span><strong>${escapeHtml(taskLabels[entry.task as TaskType] || entry.task)}</strong><small>${escapeHtml(new Date(entry.startedAt).toLocaleString())}${source ? ` · ${escapeHtml(source)}` : ""}</small></div>
            <div class="row-actions">
              <button type="button" class="secondary" data-history-action="log" data-id="${entry.id}">查看日志</button>
              ${entry.outputs.length ? `<button type="button" class="secondary" data-history-action="outputs" data-id="${entry.id}">查看 ${entry.outputs.length} 个输出</button>` : ""}
              <button type="button" class="secondary" data-history-action="rerun" data-id="${entry.id}">${entry.status === "failed" ? "重试" : "重新运行"}</button>
            </div>
          </article>`;
        })
        .join("")
    : '<p class="empty-state">还没有任务历史。运行中的任务若遇到应用退出，会在下次启动时标记为“已中断”并可重新运行。</p>';
  target.querySelectorAll<HTMLButtonElement>("button[data-history-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const entry = taskHistory.find((item) => item.id === button.dataset.id);
      if (!entry) return;
      if (button.dataset.historyAction === "log") {
        setOutput(entry.log || entry.error || "该任务没有保存日志。");
        setState(`历史：${historyStatusLabels[entry.status]}`);
      } else if (button.dataset.historyAction === "outputs") {
        renderOutputs(entry.outputs, `历史输出 · ${taskLabels[entry.task as TaskType] || entry.task}`, true);
        setState(`已显示历史输出（${entry.outputs.length} 个文件）`);
      } else {
        applyHistoryRequest(entry.request);
        void runTask(false, entry.status === "failed" || Boolean(entry.request.retry_failed), entry.id);
      }
    });
  });
}

function applyHistoryRequest(request: Record<string, unknown>): void {
  const mappings: Record<string, string> = {
    task: "taskType",
    source: "source",
    output_dir: "outputDir",
    output_filename: "outputFilename",
    conda_env: "condaEnv",
    python_bin: "pythonBin",
    api_base: "apiBase",
    model: "model",
    asr_model: "asrModel",
    subtitle_strategy: "subtitleStrategy",
    favorite_limit: "favoriteLimit",
    runtime_backend: "runtimeBackend",
    web_capture_mode: "webCaptureMode",
    browser_executable: "browserExecutable",
    timeout_seconds: "timeoutSeconds",
    retry_count: "retryCount",
    cooldown_delay: "cooldownDelay",
    chunk_chars: "chunkChars",
  };
  for (const [key, id] of Object.entries(mappings)) {
    if (request[key] !== undefined) setInputValue(id, String(request[key]));
  }
  const booleans: Record<string, string> = {
    extract_keyframes: "extractKeyframes",
    dialogue_detection: "dialogueDetection",
    keep_original_subtitles: "keepOriginalSubtitles",
    recursive_search: "recursiveSearch",
    overwrite_outputs: "overwriteOutputs",
    incognito_mode: "incognitoMode",
    stock_terms: "stockTerms",
    enable_ocr: "enableOcr",
    ocr_resume: "ocrResume",
  };
  for (const [key, id] of Object.entries(booleans)) {
    const input = document.querySelector<HTMLInputElement>(`#${id}`);
    if (input && request[key] !== undefined) input.checked = Boolean(request[key]);
  }
  hydrateTaskControls();
  saveSettings();
}

function clearHistory(): void {
  if (isWorkerRunning) return;
  taskHistory = [];
  saveTaskHistory(taskHistory);
  renderHistory();
}

async function manageRuntime(action: "status" | "install" | "remove"): Promise<void> {
  if (!hasTauriRuntime()) {
    setOutput(tauriRuntimeHint());
    return;
  }
  setWorkerRunning(true);
  setState(action === "status" ? "读取托管环境..." : action === "install" ? "安装/修复托管环境..." : "卸载托管环境...");
  try {
    const result = await invoke<string>("manage_runtime", { action });
    setOutput(result);
    setState(action === "remove" ? "托管环境已卸载" : "托管环境就绪");
  } catch (error) {
    setOutput(errorMessage(error));
    setState("托管环境操作失败");
  } finally {
    setWorkerRunning(false);
  }
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
