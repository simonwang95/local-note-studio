import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open, type OpenDialogOptions } from "@tauri-apps/plugin-dialog";
import { createAppTabs } from "./app-shell";
import { ManifestViewStateStore } from "./manifest-state";
import {
  cacheBytes,
  cacheCategoryLabels,
  cacheMetric,
  cachePreviewConfirmation,
  cacheReferences,
  cacheRetentionDays,
  cacheSourceMatches,
  filterCacheItems,
  type CacheCategory,
  type CacheInventory,
  type CachePreview,
} from "./cache-maintenance";
import {
  clearRecentValues,
  createDesktopProfile,
  createHistoryEntry,
  deleteCredentials,
  deleteDesktopProfile,
  exportDesktopProfile,
  importedDesktopProfile,
  historyReplayRequest,
  hasCredentialReference,
  loadCredentials,
  loadDesktopProfiles,
  loadTaskHistory,
  loadQueue,
  loadRecentValues,
  noChangesStatusLabel,
  moveQueueEntry,
  queueDuplicate,
  filterTaskHistory,
  pathDialogDefault,
  rememberRecentValue,
  progressFromLine,
  removeRecentValue,
  removeHistoryEntry,
  runtimeSelectionPayload,
  saveTaskHistory,
  saveCredentials,
  saveDesktopProfiles,
  saveQueue,
  renameDesktopProfile,
  taskResultFromLog,
  upsertHistoryEntry,
  type ProgressEvent,
  type TaskHistoryEntry,
  type TaskHistoryStatus,
  type DesktopProfile,
  type DesktopProfiles,
  type QueueEntry,
  type QueueStatus,
  type RecentValueKind,
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
  runtimePreferenceConfirmed: boolean;
  condaEnv: string;
  condaBin: string;
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
  dateInFilename: boolean;
  enableThinking: boolean;
  videoOutputMode: "full" | "transcription-only";
  proofreadChunkChars: string;
  proofreadTimeoutSeconds: string;
  proofreadRetryCount: string;
  proofreadCooldownDelay: string;
  proofreadEnableThinking: boolean;
  summaryChunkChars: string;
  summaryTimeoutSeconds: string;
  summaryRetryCount: string;
  summaryCooldownDelay: string;
  summaryEnableThinking: boolean;
  continueQueueAfterFailure: boolean;
};

type WorkerLogPayload = {
  line: string;
};

class TauriRuntimeUnavailableError extends Error {}

const settingsKey = "local-note-studio.settings.v1";
const cookiePermissionNoticeKey = "local-note-studio.cookie-permission-notice.v1";
let isWorkerRunning = false;
let workerLogListenerReady: Promise<void> | null = null;
let taskHistory = loadTaskHistory();
let desktopProfiles: DesktopProfiles;
let currentProfile: DesktopProfile;
let queueEntries: QueueEntry[] = loadQueue();
let queueRunning = false;
let queuePaused = false;
let continueQueueAfterFailure = true;
let savedSettings: SavedSettings;
let activeHistoryEntry: TaskHistoryEntry | null = null;
let historyFilter: TaskHistoryStatus | "all" = "all";
const appTabKey = "local-note-studio.active-tab.v1";
const manifestViewState = new ManifestViewStateStore();
let cacheInventory: CacheInventory | null = null;
let cachePreview: CachePreview | null = null;
let cacheBusy = false;
let cacheReferencesReady: Promise<void> = Promise.resolve();
let cacheReferencesError = "";

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

const historyStatusLabels: Record<TaskHistoryStatus, string> = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const queueStatusLabels: Record<QueueStatus, string> = {
  waiting: "等待中", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断",
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
  "epub-export": "输入一个 Markdown 笔记目录，递归导出为单个 EPUB。托管环境会安装 pandoc；高级 Conda/Python 后端需自行提供 pandoc。",
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
  runtimeBackend: "managed",
  runtimePreferenceConfirmed: true,
  condaEnv: "course-whisper",
  condaBin: "",
  pythonBin: "python3",
  apiBase: "http://127.0.0.1:8000/v1",
  apiKey: "mtplx-local",
  model: "mtplx-qwen38-27b-optimized-speed",
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
  keepOriginalSubtitles: false,
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
  dateInFilename: false,
  enableThinking: false,
  videoOutputMode: "full",
  proofreadChunkChars: "8000",
  proofreadTimeoutSeconds: "600",
  proofreadRetryCount: "1",
  proofreadCooldownDelay: "0",
  proofreadEnableThinking: false,
  summaryChunkChars: "",
  summaryTimeoutSeconds: "",
  summaryRetryCount: "",
  summaryCooldownDelay: "",
  summaryEnableThinking: false,
  continueQueueAfterFailure: true,
};

const app = document.querySelector<HTMLDivElement>("#app");

if (!app) {
  throw new Error("missing #app");
}

desktopProfiles = loadDesktopProfiles(readLegacySettings());
currentProfile = desktopProfiles.profiles.find((profile) => profile.id === desktopProfiles.activeId) || desktopProfiles.profiles[0];
savedSettings = { ...defaults, ...currentProfile.settings, ...loadCredentials(currentProfile.credentialRef) } as SavedSettings;

app.innerHTML = `
  <section class="shell">
    <aside class="sidebar">
      <div class="brand">
        <h1>Local Note Studio</h1>
        <p>把视频、网页、文档和论文整理成 Obsidian 兼容 Markdown。</p>
      </div>

      <nav class="tab-nav" role="tablist" aria-label="主工作区">
        <button id="tabConfig" type="button" role="tab" aria-controls="panelConfig" data-app-tab="config">
          <span>配置</span><small>运行环境与路径</small>
        </button>
        <button id="tabTask" type="button" role="tab" aria-controls="panelTask" data-app-tab="task">
          <span>任务</span><small>执行、进度与历史</small>
        </button>
        <button id="tabValidation" type="button" role="tab" aria-controls="panelValidation" data-app-tab="validation">
          <span>校验</span><small>依赖与处理记录</small>
        </button>
      </nav>

      <div class="status-card">
        <strong>本机工作区</strong>
        <span>配置、任务与校验相互独立；右侧始终保留本次输出和实时日志。</span>
      </div>
    </aside>

    <main class="workspace">
      <div class="tab-stage">
      <section id="panelConfig" class="tab-view" role="tabpanel" aria-labelledby="tabConfig" data-tab-panel="config">
      <section class="panel">
        <div class="panel-header">
          <div>
            <span class="step">1</span><h2>运行环境配置</h2>
          </div>
        </div>
        <div class="profile-toolbar">
          <label>配置档案
            <select id="profileSelect">${desktopProfiles.profiles.map((profile) => `<option value="${escapeHtml(profile.id)}" ${profile.id === currentProfile.id ? "selected" : ""}>${escapeHtml(profile.name)}</option>`).join("")}</select>
          </label>
          <div class="actions profile-actions">
            <button id="profileCreate" type="button" class="secondary">新建</button>
            <button id="profileCopy" type="button" class="secondary">复制</button>
            <button id="profileRename" type="button" class="secondary">重命名</button>
            <button id="profileDelete" type="button" class="secondary">删除</button>
            <button id="profileExport" type="button" class="secondary">导出</button>
            <button id="profileImport" type="button" class="secondary">导入</button>
            <input id="profileImportFile" type="file" accept="application/json,.json" hidden />
          </div>
        </div>
        <p class="field-note">档案保存运行时、模型、ASR、Cookie 引用、输出目录与阶段参数。API Key、Cookie 路径和浏览器 Profile 单独存放，导出文件不包含这些字段。</p>
        <div class="form-grid compact">
          <label>
            运行时后端
            <select id="runtimeBackend">
              <option value="managed" ${savedSettings.runtimeBackend === "managed" ? "selected" : ""}>应用托管环境</option>
              <option value="conda" ${savedSettings.runtimeBackend === "conda" ? "selected" : ""}>现有 Conda / Python（高级）</option>
            </select>
          </label>
          <label id="condaEnvField">
            Conda 环境
            <input id="condaEnv" value="${escapeHtml(savedSettings.condaEnv)}" placeholder="course-whisper" />
          </label>
          <label id="condaBinField">
            Conda 可执行文件（可选）
            <input id="condaBin" value="${escapeHtml(savedSettings.condaBin)}" placeholder="自动查找；也可填写 .../bin/conda" />
          </label>
          <label id="pythonBinField">
            Python 命令
            <input id="pythonBin" value="${escapeHtml(savedSettings.pythonBin)}" placeholder="python3" />
          </label>
          <p id="runtimeBackendNote" class="field-note full-row"></p>
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
            <div class="input-row profile-row">
              <input id="asrModel" type="password" value="${escapeHtml(savedSettings.asrModel)}" placeholder="选择已有 Whisper 模型" autocomplete="off" />
              <button id="toggleAsrModel" type="button" class="secondary icon-button" title="显示 ASR 模型目录" aria-label="显示 ASR 模型目录">
                ${eyeIcon()}
              </button>
              <button id="chooseAsrModel" type="button" class="secondary compact-button">选择</button>
              <button id="saveAsrModel" type="button" class="secondary compact-button">保存配置</button>
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
              <input id="cookies" type="password" value="${escapeHtml(savedSettings.cookies)}" placeholder="留空则安全保存到应用数据目录" autocomplete="off" />
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
              <button id="refreshCookies" type="button" class="secondary compact-button">授权并刷新 Cookie</button>
            </div>
            <p class="field-note">在当前登录 B站的 Chrome 窗口打开 <code>chrome://version/</code>，复制“个人资料路径”，再选择末级 Default 或 Profile 1 目录。macOS 只需授权读取该 Profile（可能另有钥匙串确认）。</p>
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
      </section>

      <section id="panelTask" class="tab-view" role="tabpanel" aria-labelledby="tabTask" data-tab-panel="task" hidden>
      <section class="panel task-panel">
        <div class="panel-header">
          <div>
            <span class="step">1</span>
            <h2>任务执行</h2>
          </div>
          <div class="actions">
            <button id="checkBilibiliAccess" type="button" class="secondary hidden">验证B站目标权限</button>
            <button id="renameNotesDate" type="button" class="secondary hidden">补充文件名日期</button>
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
              <input id="outputDir" list="recentOutputDirs" placeholder="/Users/xxx/Notes/Net/BiliBili" />
              <button id="chooseOutputDir" type="button" class="secondary compact-button">选择</button>
            </div>
            <datalist id="recentOutputDirs">${recentOptions("outputDir")}</datalist>
            <div class="recent-actions">
              <button id="deleteOutputDirRecent" type="button" class="secondary link-button">删除当前记录</button>
              <button id="clearOutputDirRecent" type="button" class="secondary link-button">清空输出目录记录</button>
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
          <label id="videoOutputModeField" class="hidden">
            视频输出合同
            <select id="videoOutputMode">
              <option value="full" ${savedSettings.videoOutputMode === "full" ? "selected" : ""}>完整整理笔记</option>
              <option value="transcription-only" ${savedSettings.videoOutputMode === "transcription-only" ? "selected" : ""}>仅转写（保留原文字幕）</option>
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
          <p class="field-note full-row incognito-note">开启后仍会生成笔记并保留任务历史，但不会读取或写入 source/video/quickread Manifest、关键帧 Manifest、B站已处理列表、批量失败状态和最近输入/输出记录。</p>
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
          <label id="dateInFilenameField" class="checkbox-field hidden">
            <span>文件名补充日期</span>
            <input id="dateInFilename" type="checkbox" ${savedSettings.dateInFilename ? "checked" : ""} />
          </label>
          <p id="dateInFilenameNote" class="field-note full-row hidden">开启后，新整理笔记的文件名会以发布时间开头，例如 <code>2026-07-14-BILI-OPUS-标题_动态ID.md</code>；已带日期前缀的文件不会被重复添加。</p>
          <label id="enableThinkingField" class="checkbox-field">
            <span>启用思考</span>
            <input id="enableThinking" type="checkbox" ${savedSettings.enableThinking ? "checked" : ""} />
          </label>
          <p id="enableThinkingNote" class="field-note full-row hidden">开启后，整理笔记时会启用模型思考（reasoning），通常会增加生成时间。思维导图层级由输出校验单独保证。</p>
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
            <input id="source" list="recentSources" placeholder="https://www.bilibili.com/video/BV... 或 /path/to/file.pdf" />
            <button id="chooseSourceFile" type="button" class="secondary compact-button">文件</button>
            <button id="chooseSourceDir" type="button" class="secondary compact-button">目录</button>
          </div>
          <datalist id="recentSources">${recentOptions("source")}</datalist>
          <div class="recent-actions">
            <button id="deleteSourceRecent" type="button" class="secondary link-button">删除当前记录</button>
            <button id="clearSourceRecent" type="button" class="secondary link-button">清空输入源记录</button>
          </div>
        </label>
        <p id="taskHint" class="field-note"></p>
        <details class="advanced-options">
          <summary>长任务参数覆盖</summary>
          <div class="form-grid compact advanced-grid">
            <label>超时（秒）<input id="timeoutSeconds" type="number" min="0" value="${escapeHtml(savedSettings.timeoutSeconds)}" placeholder="使用稳定默认值" /></label>
            <label>重试次数<input id="retryCount" type="number" min="0" value="${escapeHtml(savedSettings.retryCount)}" placeholder="使用稳定默认值" /></label>
            <label>模型冷却（秒）<input id="cooldownDelay" type="number" min="0" value="${escapeHtml(savedSettings.cooldownDelay)}" placeholder="留空用默认值；0 为不等待" /></label>
            <label>分块字符数<input id="chunkChars" type="number" min="0" value="${escapeHtml(savedSettings.chunkChars)}" placeholder="使用稳定默认值" /></label>
          </div>
          <h3 id="videoProofreadTitle">视频校对</h3>
          <div id="videoProofreadFields" class="form-grid compact advanced-grid">
            <label>校对段长（字符，500–8000）<input id="proofreadChunkChars" type="number" min="500" max="8000" value="${escapeHtml(savedSettings.proofreadChunkChars)}" /></label>
            <label>超时（秒，至少 60）<input id="proofreadTimeoutSeconds" type="number" min="60" value="${escapeHtml(savedSettings.proofreadTimeoutSeconds)}" /></label>
            <label>质量重试次数<input id="proofreadRetryCount" type="number" min="0" max="5" value="${escapeHtml(savedSettings.proofreadRetryCount)}" /></label>
            <label>模型冷却（秒）<input id="proofreadCooldownDelay" type="number" min="0" value="${escapeHtml(savedSettings.proofreadCooldownDelay)}" /></label>
            <label class="checkbox-field"><span>校对阶段启用思考</span><input id="proofreadEnableThinking" type="checkbox" ${savedSettings.proofreadEnableThinking ? "checked" : ""} /></label>
          </div>
          <h3 id="videoSummaryTitle">视频摘要</h3>
          <div id="videoSummaryFields" class="form-grid compact advanced-grid">
            <label>段长（字符）<input id="summaryChunkChars" type="number" min="1" value="${escapeHtml(savedSettings.summaryChunkChars)}" placeholder="沿用通用分块；空值用默认" /></label>
            <label>超时（秒）<input id="summaryTimeoutSeconds" type="number" min="60" value="${escapeHtml(savedSettings.summaryTimeoutSeconds)}" placeholder="沿用通用超时；空值用默认" /></label>
            <label>重试次数<input id="summaryRetryCount" type="number" min="0" value="${escapeHtml(savedSettings.summaryRetryCount)}" placeholder="沿用通用重试；0 表示关闭" /></label>
            <label>模型冷却（秒）<input id="summaryCooldownDelay" type="number" min="0" value="${escapeHtml(savedSettings.summaryCooldownDelay)}" placeholder="沿用通用冷却；0 表示不等待" /></label>
            <label class="checkbox-field"><span>摘要阶段启用思考</span><input id="summaryEnableThinking" type="checkbox" ${savedSettings.summaryEnableThinking ? "checked" : ""} /></label>
          </div>
          <p class="field-note">空值沿用通用设置或 Worker 默认值；重试和冷却中的显式 0 表示关闭。校对默认 8000 字符，前后文各最多 240 字符只用于校验，不计入段长；输出 token 预算仍由模型端设置控制。</p>
        </details>
        <section class="queue-panel">
          <div class="panel-header compact-header"><h3>本地串行队列</h3><div class="actions"><button id="queueAdd" type="button" class="secondary">当前任务入队</button><button id="queueStart" type="button">开始/继续</button><button id="queuePause" type="button" class="secondary">暂停调度</button></div></div>
          <label class="checkbox-field"><span>单项失败后继续运行后续项</span><input id="continueQueueAfterFailure" type="checkbox" ${savedSettings.continueQueueAfterFailure ? "checked" : ""} /></label>
          <p id="queueNotice" class="field-note">队列以档案引用凭据，保存入队时的参数快照；同一来源与输出目录不会重复入队。重启后需手动确认恢复中断项。</p>
          <div id="queueList" class="history-list"></div>
        </section>
      </section>

      <section id="progressPanel" class="panel hidden">
        <div class="panel-header compact-header"><h2>任务进度</h2><span id="progressText">等待进度事件</span></div>
        <progress id="taskProgress" max="100" value="0"></progress>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div><span class="step">2</span><h2>任务历史与恢复 <small id="historyCount" class="heading-count"></small></h2></div>
          <button id="clearHistory" type="button" class="secondary">清空全部历史</button>
        </div>
        <div class="history-toolbar">
          <p class="manifest-help">这里显示本机保存的全部任务历史（最多 100 条），包含当前运行任务。清空全部只删除历史记录，不会删除任何输出文件。</p>
          <label class="history-filter">显示
            <select id="historyFilter">
              <option value="all">全部状态</option>
              <option value="running">运行中</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
              <option value="cancelled">已取消</option>
              <option value="interrupted">已中断</option>
            </select>
          </label>
        </div>
        <div id="historyList" class="history-list"></div>
      </section>
      </section>

      <section id="panelValidation" class="tab-view" role="tabpanel" aria-labelledby="tabValidation" data-tab-panel="validation" hidden>
        <section class="panel validation-intro">
          <div class="panel-header">
            <div><span class="step">1</span><h2>运行环境校验</h2></div>
            <button id="checkEnv" type="button">检查依赖</button>
          </div>
          <p class="manifest-help">检查当前所选运行时、Python 包、ffmpeg、yt-dlp、OCR 与可选工具。检查结果会持续显示在右侧日志中。</p>
        </section>

        <section class="panel">
          <div class="panel-header">
            <div><span class="step">2</span><h2>处理记录与文件状态</h2></div>
            <button id="refreshManifests" type="button" class="secondary">重新检查</button>
          </div>
          <p class="manifest-help">这是程序用于避免重复处理的本地记录。可多选后批量标记状态、恢复“自动判断”或删除记录。删除只移除处理记录，不会删除源文件或已经生成的笔记。</p>
          <div id="manifestSummary" class="summary-chips"></div>
          <div id="manifestList" class="manifest-list"><p class="empty-state">点击“重新检查”，查看视频、文档、论文和 B站批次的处理记录。</p></div>
        </section>

        <section class="panel cache-panel">
          <div class="panel-header">
            <div><span class="step">3</span><h2>缓存管理与任务诊断</h2></div>
            <button id="refreshCache" type="button" class="secondary">刷新缓存</button>
          </div>
          <p class="manifest-help">查看原文证据、ASR 诊断、检查点和恢复材料。运行、排队及待恢复任务的引用由 Worker 保护；清理预览会列出具体文件。无法确认来源的旧材料会保留，并标明保护原因。</p>
          <p id="cacheNotice" class="field-note" role="status">自动清理默认关闭；刷新后显示已保存的策略。缺失诊断指标会标为“未知”。</p>
          <p id="cacheReferencesNotice" class="field-note" role="status"></p>
          <div id="cacheSummary" class="summary-chips"></div>
          <div class="form-grid compact cache-controls">
            <label>查看类别<select id="cacheCategoryFilter"><option value="all">全部类别</option>${Object.entries(cacheCategoryLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></label>
            <label>任务 / 来源筛选<input id="cacheSourceFilter" placeholder="来源、任务类型、路径或状态" /></label>
          </div>
          <div id="cacheList" class="cache-list"><p class="empty-state">点击“刷新缓存”查看本机材料和占用。</p></div>
          <details class="cache-maintenance-options" open>
            <summary>保留策略与清理</summary>
            <fieldset class="cache-categories"><legend>参与清理的类别</legend>${Object.entries(cacheCategoryLabels).map(([value, label]) => `<label class="check"><input type="checkbox" data-cache-category="${value}" ${value === "disposable_cache" ? "checked" : ""} /> ${label}</label>`).join("")}</fieldset>
            <div class="form-grid compact">
              <label>保留最近天数<input id="cacheRetentionDays" type="number" min="0" max="3650" step="1" value="30" /></label>
              <label class="check"><input id="cacheAutoEnabled" type="checkbox" /> 启用自动清理（默认关闭）</label>
            </div>
            <p class="field-note">保留期限只作用于勾选的类别。自动清理在新任务开始前检查，运行和恢复引用始终优先。</p>
            <div class="actions"><button id="cacheSavePolicy" type="button" class="secondary">保存保留策略</button><button id="cachePreviewClean" type="button" class="secondary">预览清理</button><button id="cacheClean" type="button" class="secondary danger-outline" disabled>清理预览中的文件</button></div>
            <div id="cachePreviewList" class="cache-preview" aria-live="polite"></div>
          </details>
          <details class="cache-diagnostics" open>
            <summary>任务诊断与导出</summary>
            <div id="cacheDiagnosticList"><p class="empty-state">刷新后查看阶段耗时、模型调用、tokens、重试原因和缓存命中。</p></div>
            <div class="cache-export-options"><label class="check"><input id="cacheIncludeOriginal" type="checkbox" /> 导出包含原文</label><label class="check"><input id="cacheIncludePaths" type="checkbox" /> 导出包含完整路径</label><button id="cacheExport" type="button" class="secondary">导出诊断 JSON</button></div>
            <p class="field-note">默认导出脱敏诊断。原文和完整路径仅在勾选后包含；API Key、Cookie 内容始终移除。</p>
          </details>
        </section>
      </section>
      </div>

      <aside class="output-inspector" aria-label="输出与日志">
        <section id="resultPanel" class="panel inspector-panel result-panel">
          <div class="panel-header compact-header">
            <div><h2 id="resultTitle">本次输出</h2></div>
            <button id="copyOutputDir" type="button" class="secondary">复制目录</button>
          </div>
          <div id="resultList" class="result-list"><p class="empty-state">任务完成后会在这里列出新增或更新的文件。</p></div>
        </section>

        <section class="log-panel inspector-panel">
          <div class="log-header compact-header">
            <h2>日志</h2><span id="runState">准备就绪</span>
          </div>
          <pre id="output">先检查依赖，然后预览或运行任务。</pre>
        </section>
      </aside>
    </main>
  </section>
`;

const taskType = document.querySelector<HTMLSelectElement>("#taskType");
const outputRoot = document.querySelector<HTMLInputElement>("#outputRoot");
const outputDir = document.querySelector<HTMLInputElement>("#outputDir");
const taskHint = document.querySelector<HTMLParagraphElement>("#taskHint");
const appTabs = createAppTabs(document, localStorage, appTabKey);

try {
  appTabs.bind();
  hydrateRuntimeControls();
  hydrateTaskControls();
  hydrateTaskOutput();
  bindSettingsPersistence();
  renderHistory();
  renderQueue();
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
document.querySelector<HTMLButtonElement>("#queueAdd")?.addEventListener("click", enqueueCurrentTask);
document.querySelector<HTMLButtonElement>("#queueStart")?.addEventListener("click", () => void startQueue());
document.querySelector<HTMLButtonElement>("#queuePause")?.addEventListener("click", pauseQueue);
document.querySelector<HTMLInputElement>("#continueQueueAfterFailure")?.addEventListener("change", saveSettings);
document.querySelector<HTMLButtonElement>("#renameNotesDate")?.addEventListener("click", () => runRenameNotesDate());
document.querySelector<HTMLButtonElement>("#cancelTask")?.addEventListener("click", () => cancelWorker());
document.querySelector<HTMLButtonElement>("#copyOutputDir")?.addEventListener("click", () => copyPath(inputValue("outputDir")));
document.querySelector<HTMLButtonElement>("#refreshManifests")?.addEventListener("click", () => refreshManifestStatus());
document.querySelector<HTMLButtonElement>("#clearHistory")?.addEventListener("click", clearHistory);
document.querySelector<HTMLButtonElement>("#refreshCache")?.addEventListener("click", () => void refreshCacheInventory());
document.querySelector<HTMLSelectElement>("#cacheCategoryFilter")?.addEventListener("change", renderCacheInventory);
document.querySelector<HTMLInputElement>("#cacheSourceFilter")?.addEventListener("input", renderCacheInventory);
document.querySelector<HTMLButtonElement>("#cacheSavePolicy")?.addEventListener("click", () => void saveCachePolicy());
document.querySelector<HTMLButtonElement>("#cachePreviewClean")?.addEventListener("click", () => void previewCacheCleanup());
document.querySelector<HTMLButtonElement>("#cacheClean")?.addEventListener("click", () => void cleanCachePreview());
document.querySelector<HTMLButtonElement>("#cacheExport")?.addEventListener("click", () => void exportCacheDiagnostics());
document.querySelectorAll<HTMLInputElement>("[data-cache-category], #cacheRetentionDays, #cacheAutoEnabled").forEach((control) => control.addEventListener("change", invalidateCachePreview));
document.querySelector<HTMLSelectElement>("#historyFilter")?.addEventListener("change", (event) => {
  historyFilter = (event.currentTarget as HTMLSelectElement).value as TaskHistoryStatus | "all";
  renderHistory();
});
document.querySelector<HTMLButtonElement>("#runtimeStatus")?.addEventListener("click", () => manageRuntime("status"));
document.querySelector<HTMLButtonElement>("#runtimeInstall")?.addEventListener("click", () => manageRuntime("install"));
document.querySelector<HTMLButtonElement>("#runtimeRemove")?.addEventListener("click", () => manageRuntime("remove"));
document.querySelector<HTMLButtonElement>("#chooseOutputRoot")?.addEventListener("click", () => chooseDirectory("outputRoot"));
document.querySelector<HTMLButtonElement>("#chooseOutputDir")?.addEventListener("click", () => chooseDirectory("outputDir"));
document.querySelector<HTMLButtonElement>("#chooseSourceFile")?.addEventListener("click", () => chooseSourceFile());
document.querySelector<HTMLButtonElement>("#chooseSourceDir")?.addEventListener("click", () => chooseDirectory("source"));
document.querySelector<HTMLButtonElement>("#chooseChromeProfile")?.addEventListener("click", () => chooseDirectory("chromeProfile"));
document.querySelector<HTMLButtonElement>("#chooseAsrModel")?.addEventListener("click", () => chooseDirectory("asrModel"));
document.querySelector<HTMLButtonElement>("#saveAsrModel")?.addEventListener("click", () => {
  saveSettings();
  setState("ASR 模型配置已保存");
});
document.querySelector<HTMLButtonElement>("#toggleApiKey")?.addEventListener("click", () => toggleSecretField("apiKey", "toggleApiKey", "API Key"));
document.querySelector<HTMLButtonElement>("#toggleAsrModel")?.addEventListener("click", () =>
  toggleSecretField("asrModel", "toggleAsrModel", "ASR 模型目录"),
);
document.querySelector<HTMLButtonElement>("#toggleCookies")?.addEventListener("click", () => toggleSecretField("cookies", "toggleCookies", "Cookie 文件路径"));
document.querySelector<HTMLButtonElement>("#toggleChromeProfile")?.addEventListener("click", () =>
  toggleSecretField("chromeProfile", "toggleChromeProfile", "Chrome 个人资料路径"),
);
document.querySelector<HTMLInputElement>("#outputDir")?.addEventListener("blur", () => rememberTaskPath("outputDir"));
document.querySelector<HTMLInputElement>("#source")?.addEventListener("blur", () => rememberTaskPath("source"));
document.querySelector<HTMLButtonElement>("#deleteOutputDirRecent")?.addEventListener("click", () => deleteCurrentRecentPath("outputDir"));
document.querySelector<HTMLButtonElement>("#clearOutputDirRecent")?.addEventListener("click", () => clearRecentPathList("outputDir"));
document.querySelector<HTMLButtonElement>("#deleteSourceRecent")?.addEventListener("click", () => deleteCurrentRecentPath("source"));
document.querySelector<HTMLButtonElement>("#clearSourceRecent")?.addEventListener("click", () => clearRecentPathList("source"));
document.querySelector<HTMLSelectElement>("#profileSelect")?.addEventListener("change", (event) => switchProfile((event.currentTarget as HTMLSelectElement).value));
document.querySelector<HTMLButtonElement>("#profileCreate")?.addEventListener("click", () => createProfile(false));
document.querySelector<HTMLButtonElement>("#profileCopy")?.addEventListener("click", () => createProfile(true));
document.querySelector<HTMLButtonElement>("#profileRename")?.addEventListener("click", renameProfile);
document.querySelector<HTMLButtonElement>("#profileDelete")?.addEventListener("click", deleteProfile);
document.querySelector<HTMLButtonElement>("#profileExport")?.addEventListener("click", exportProfile);
document.querySelector<HTMLButtonElement>("#profileImport")?.addEventListener("click", () => document.querySelector<HTMLInputElement>("#profileImportFile")?.click());
document.querySelector<HTMLInputElement>("#profileImportFile")?.addEventListener("change", (event) => void importProfile(event.currentTarget as HTMLInputElement));

if (!hasTauriRuntime()) {
  setState("浏览器预览");
  setOutput(tauriRuntimeHint());
} else {
  setState("准备检查依赖...");
  setOutput("应用已启动，正在自动检查运行环境...\n");
  window.setTimeout(() => {
    scheduleCacheReferences();
    void runEnvironmentCheck();
  }, 100);
}

function inputValue(id: string): string {
  return document.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`)?.value.trim() ?? "";
}

function checkboxChecked(id: string): boolean {
  return Boolean(document.querySelector<HTMLInputElement>(`#${id}`)?.checked);
}

function setInputValue(id: string, value: string, notify = true): void {
  const input = document.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`);
  if (!input) return;
  input.value = value;
  if (notify) input.dispatchEvent(new Event("input", { bubbles: true }));
}

function recentOptions(kind: RecentValueKind): string {
  return loadRecentValues(kind)
    .map((value) => `<option value="${escapeHtml(value)}"></option>`)
    .join("");
}

function renderRecentValues(kind: RecentValueKind): void {
  const targetId = kind === "outputDir" ? "recentOutputDirs" : "recentSources";
  const target = document.querySelector<HTMLDataListElement>(`#${targetId}`);
  if (target) target.innerHTML = recentOptions(kind);
}

function recentKindLabel(kind: RecentValueKind): string {
  return kind === "outputDir" ? "输出目录" : "输入源";
}

function rememberTaskPath(kind: RecentValueKind): void {
  if (checkboxChecked("incognitoMode")) return;
  const inputId = kind === "outputDir" ? "outputDir" : "source";
  rememberRecentValue(kind, inputValue(inputId));
  renderRecentValues(kind);
}

function deleteCurrentRecentPath(kind: RecentValueKind): void {
  const inputId = kind === "outputDir" ? "outputDir" : "source";
  const value = inputValue(inputId);
  if (!value) {
    setState(`没有可删除的当前${recentKindLabel(kind)}记录`);
    return;
  }
  const before = loadRecentValues(kind);
  const next = removeRecentValue(kind, value);
  renderRecentValues(kind);
  setState(next.length === before.length ? `当前${recentKindLabel(kind)}不在最近记录中` : `已删除当前${recentKindLabel(kind)}记录`);
}

function clearRecentPathList(kind: RecentValueKind): void {
  const label = recentKindLabel(kind);
  const confirmed = window.confirm(`确定清空最近${label}记录吗？\n\n只清空本机界面记录，不会删除任何文件或任务历史。`);
  if (!confirmed) return;
  clearRecentValues(kind);
  renderRecentValues(kind);
  setState(`最近${label}记录已清空`);
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
  const runtimeBackend = (inputValue("runtimeBackend") || "managed") as "managed" | "conda";
  return {
    task,
    ...runtimeSelectionPayload(runtimeBackend, inputValue("condaEnv"), inputValue("condaBin")),
    source: inputValue("source"),
    output_dir: inputValue("outputDir"),
    output_filename: inputValue("outputFilename"),
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
    date_in_filename: checkboxChecked("dateInFilename"),
    enable_thinking: checkboxChecked("enableThinking"),
    video_output_mode: inputValue("videoOutputMode") || "full",
    proofread_chunk_chars: inputValue("proofreadChunkChars") || "8000",
    proofread_timeout_seconds: inputValue("proofreadTimeoutSeconds") || "600",
    proofread_retry_count: inputValue("proofreadRetryCount") || "1",
    proofread_cooldown_delay: inputValue("proofreadCooldownDelay") || "0",
    proofread_enable_thinking: checkboxChecked("proofreadEnableThinking"),
    summary_chunk_chars: inputValue("summaryChunkChars"),
    summary_timeout_seconds: inputValue("summaryTimeoutSeconds"),
    summary_retry_count: inputValue("summaryRetryCount"),
    summary_cooldown_delay: inputValue("summaryCooldownDelay"),
    summary_enable_thinking: checkboxChecked("summaryEnableThinking"),
    desktop_profile_id: currentProfile.id,
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
  appendOutput("正在检查所选运行环境...\n");
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
    setOutput("请填写或选择 Chrome 个人资料路径。请在当前登录 B站的 Chrome 窗口打开 chrome://version/，复制“个人资料路径”。");
    return;
  }
  if (!localStorage.getItem(cookiePermissionNoticeKey)) {
    const accepted = window.confirm(
      "刷新 B站 Cookie 只会读取所选的具体 Chrome Profile，并默认写入 Local Note Studio 自己的应用数据目录。\n\nmacOS 随后可能询问“访问其他 App 的数据”，以及显示 Chrome 钥匙串确认；不需要授权文稿、下载或可移动宗卷。\n\n继续授权并刷新吗？",
    );
    if (!accepted) {
      setState("已取消 Cookie 授权");
      return;
    }
    localStorage.setItem(cookiePermissionNoticeKey, "acknowledged");
  }
  saveSettings();
  setWorkerRunning(true);
  setState("正在刷新 Cookie...");
  setOutput("正在从指定 Chrome Profile 读取 B站 Cookie...\n");
  try {
    await invokeWorker({ ...payload(false), task: "refresh-bilibili-cookies", source: "", output_dir: "" });
    appendOutput("\nCookie 导出完成，正在校验登录态...\n");
    const checkResult = await invokeWorker({ ...payload(false), task: "bilibili-cookie-status", source: "", output_dir: "" });
    appendOutput(checkResult);
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

async function runTask(dryRun: boolean, retryFailed = false, retryOf?: string, requestOverride?: Record<string, unknown>): Promise<void> {
  if (isWorkerRunning || cacheBusy) return;
  saveSettings();
  const task = (String(requestOverride?.task || currentTask())) as TaskType;
  if (retryFailed && task === "bilibili-favorite" && Boolean(requestOverride?.incognito_mode ?? checkboxChecked("incognitoMode"))) {
    setState("隐身模式不读取失败状态");
    setOutput("“只重试失败项”依赖上次保存的 B站批量失败状态。请关闭隐身模式后再重试。\n");
    return;
  }
  if (!String(requestOverride?.output_dir || inputValue("outputDir"))) {
    setState("缺少输出目录");
    setOutput("请先填写默认输出根目录，或手动填写本次输出目录。");
    return;
  }
  if (!String(requestOverride?.source || inputValue("source")) && task !== "bilibili-favorite") {
    setState("缺少输入源");
    setOutput("请填写 URL、文件路径或目录路径。");
    return;
  }
  if (task === "bilibili-favorite" && !String(requestOverride?.collection_id || (payload(false) as { collection_id?: string }).collection_id || "")) {
    setState("未选择收藏夹/系列");
    setOutput("请先点击“读取列表”，然后选择一个收藏夹或系列。");
    return;
  }

  const request = { ...payload(dryRun, retryFailed), ...(requestOverride || {}) } as Record<string, unknown>;
  const credentialProfileId = String(request.desktop_profile_id || currentProfile.id);
  const credentialProfile = desktopProfiles.profiles.find((profile) => profile.id === credentialProfileId);
  const credentials = credentialProfile ? loadCredentials(credentialProfile.credentialRef) : {};
  request.api_key = credentials.apiKey || "";
  request.cookies = credentials.cookies || "";
  request.browser_profile = credentials.chromeProfile || "";
  rememberTaskPath("outputDir");
  if (inputValue("source")) rememberTaskPath("source");
  if (!dryRun) {
    activeHistoryEntry = createHistoryEntry(task, request as Record<string, unknown>, retryOf);
    request.run_id = activeHistoryEntry.id;
    activeHistoryEntry.request.run_id = activeHistoryEntry.id;
    taskHistory = upsertHistoryEntry(taskHistory, activeHistoryEntry);
    persistTaskHistory();
    renderHistory();
  }
  setWorkerRunning(true);
  setState(dryRun ? "生成预览中..." : "任务运行中...");
  setOutput("");
  appendOutput(dryRun ? "正在生成命令预览...\n" : "任务已启动，日志会实时追加到这里。\n");
  try {
    await syncCacheReferences();
    const result = await invokeWorker(request);
    if (!currentOutput().trim()) setOutput(result || "(worker 没有返回输出)");
    updateBatchResult(result || currentOutput());
    const taskResult = taskResultFromLog(result || currentOutput());
    if (taskResult) renderOutputs(taskResult.outputs);
    const taskFailedPartially = taskResult?.status === "partial_failed" || taskResult?.status === "failed" || Number(taskResult?.counts?.failed || 0) > 0;
    if (activeHistoryEntry) {
      activeHistoryEntry.status = taskFailedPartially ? "failed" : "completed";
      if (taskFailedPartially) activeHistoryEntry.error = `部分失败：${Number(taskResult?.counts?.failed || 0)} 项未完成`;
      activeHistoryEntry.outputs = taskResult?.outputs ?? [];
    }
    setState(
      dryRun
        ? "预览完成"
        : taskFailedPartially
          ? "部分失败"
          : taskResult?.status === "no_changes"
            ? noChangesStatusLabel(taskResult)
            : "任务完成",
    );
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
      persistTaskHistory();
      activeHistoryEntry = null;
      renderHistory();
    }
    setWorkerRunning(false);
  }
}

async function runRenameNotesDate(): Promise<void> {
  if (isWorkerRunning) {
    setState("已有任务正在运行");
    return;
  }
  const outputDir = inputValue("outputDir");
  if (!outputDir) {
    setState("缺少输出目录");
    setOutput("请先填写“本次输出目录”，再补充文件名日期。");
    return;
  }
  if (
    !window.confirm(
      `将扫描输出目录并补充文件名日期：\n${outputDir}\n\n已带日期前缀的文件不会被改动；缺少可解析发布时间的文件会被跳过。确定继续吗？`,
    )
  ) {
    return;
  }
  setWorkerRunning(true);
  setState("正在补充文件名日期...");
  setOutput("");
  appendOutput("正在扫描输出目录并补充文件名日期...\n");
  try {
    const result = await invokeWorker({ task: "rename-notes-date", output_dir: outputDir });
    if (!currentOutput().trim()) setOutput(result || "(worker 没有返回输出)");
    setState("文件名日期补充完成");
  } catch (error) {
    const message = errorMessage(error);
    if (message.startsWith("Task cancelled.")) {
      appendOutput("\n任务已取消。\n");
      setState("已取消");
    } else if (currentOutput().trim()) {
      appendOutput(`\n任务失败：${message}\n`);
      setState("任务失败");
    } else {
      setOutput(message);
      setState(error instanceof TauriRuntimeUnavailableError ? "浏览器预览" : "任务失败");
    }
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

async function invokeWorkerQuiet(request: object): Promise<string> {
  if (!hasTauriRuntime()) {
    throw new TauriRuntimeUnavailableError(tauriRuntimeHint());
  }
  return invoke<string>("run_worker", { request: JSON.stringify(request) });
}

function cacheNotice(message: string): void {
  const notice = document.querySelector<HTMLElement>("#cacheNotice");
  if (notice) notice.textContent = message;
}

async function invokeCache<T>(action: string, options: object = {}): Promise<T> {
  const result = await invokeWorkerQuiet({
    task: "cache-manage",
    ...runtimeSelectionPayload((inputValue("runtimeBackend") || "managed") as "managed" | "conda", inputValue("condaEnv"), inputValue("condaBin")),
    python_bin: inputValue("pythonBin"),
    cache_action: action,
    cache_options: options,
  });
  const data = taskResultFromLog(result);
  if (!data || !data.details?.cache || data.status === "failed") throw new Error(data?.error?.message || "Worker 未返回可识别的缓存结果");
  return data.details.cache as T;
}

function persistTaskQueue(): void {
  saveQueue(queueEntries);
  scheduleCacheReferences();
}

function persistTaskHistory(): void {
  saveTaskHistory(taskHistory);
  scheduleCacheReferences();
}

function scheduleCacheReferences(): void {
  invalidateCachePreview();
  if (!hasTauriRuntime()) return;
  const references = cacheReferences(queueEntries, taskHistory);
  // Keep snapshots in order. Failed synchronization never writes an empty registry:
  // the Worker retains its last references, and a new snapshot can retry later.
  cacheReferencesReady = cacheReferencesReady.catch(() => {}).then(async () => {
    try {
      await invokeCache("references", references);
      cacheReferencesError = "";
    } catch (error) {
      cacheReferencesError = `引用同步未完成：${errorMessage(error)}。Worker 保留上次引用；同步成功前无法清理。`;
      throw error;
    } finally {
      const notice = document.querySelector<HTMLElement>("#cacheReferencesNotice");
      if (notice) notice.textContent = cacheReferencesError;
    }
  });
  void cacheReferencesReady.catch(() => {});
}

async function syncCacheReferences(): Promise<void> {
  scheduleCacheReferences();
  await cacheReferencesReady;
}

function selectedCacheCategories(): CacheCategory[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>("[data-cache-category]:checked"))
    .map((input) => input.dataset.cacheCategory as CacheCategory);
}

function invalidateCachePreview(): void {
  cachePreview = null;
  const preview = document.querySelector<HTMLElement>("#cachePreviewList");
  if (preview?.textContent) preview.textContent = "设置或任务引用已变化，请重新预览后再清理。";
  updateCacheButtons();
}

function updateCacheButtons(): void {
  for (const id of ["refreshCache", "cacheSavePolicy", "cachePreviewClean", "cacheExport"]) {
    const button = document.querySelector<HTMLButtonElement>(`#${id}`);
    if (button) button.disabled = cacheBusy;
  }
  const clean = document.querySelector<HTMLButtonElement>("#cacheClean");
  if (clean) clean.disabled = cacheBusy || isWorkerRunning || queueRunning || Boolean(cacheReferencesError) || !cachePreview?.candidate_count;
}

async function cacheOperation(operation: () => Promise<void>): Promise<void> {
  if (cacheBusy) return;
  cacheBusy = true;
  updateCacheButtons();
  try { await operation(); }
  catch (error) { cacheNotice(`缓存操作未完成：${errorMessage(error)}`); }
  finally { cacheBusy = false; updateCacheButtons(); }
}

function applyCachePolicy(data: CacheInventory): void {
  setInputValue("cacheRetentionDays", String(data.policy.retention_days), false);
  const auto = document.querySelector<HTMLInputElement>("#cacheAutoEnabled");
  if (auto) auto.checked = data.policy.auto_enabled;
  document.querySelectorAll<HTMLInputElement>("[data-cache-category]").forEach((input) => { input.checked = data.policy.categories.includes(input.dataset.cacheCategory as CacheCategory); });
}

async function refreshCacheInventory(): Promise<void> {
  await cacheOperation(async () => {
    cacheNotice("正在读取缓存与诊断...");
    try { await syncCacheReferences(); } catch { /* Inventory remains readable; cleanup stays disabled. */ }
    cacheInventory = await invokeCache<CacheInventory>("inventory");
    applyCachePolicy(cacheInventory);
    renderCacheInventory();
    cacheNotice(`缓存已刷新。自动清理${cacheInventory.policy.auto_enabled ? "已启用" : "关闭"}，保留最近 ${cacheInventory.policy.retention_days} 天。`);
  });
}

function cacheTime(value: string | undefined): string {
  if (!value) return "未知";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString();
}

function renderCacheInventory(): void {
  if (!cacheInventory) return;
  const summary = document.querySelector<HTMLElement>("#cacheSummary");
  if (summary) summary.innerHTML = `<span>缓存材料 ${cacheInventory.summary.total_count} 项 · ${cacheBytes(cacheInventory.summary.total_bytes)}</span><span>引用保护 ${cacheInventory.summary.protected_count} 项 · ${cacheBytes(cacheInventory.summary.protected_bytes)}</span><span>诊断审计保留 ${cacheMetric(cacheInventory.summary.diagnostics_count)} 项 · ${cacheBytes(cacheInventory.summary.diagnostics_bytes)}（暂不支持清理）</span>${Object.entries(cacheInventory.summary.by_category || {}).map(([category, usage]) => `<span>${escapeHtml(cacheCategoryLabels[category as CacheCategory] || category)} ${cacheMetric(usage?.count)} 项 · ${cacheBytes(usage?.size_bytes)}</span>`).join("")}`;
  const items = filterCacheItems(cacheInventory.items, inputValue("cacheCategoryFilter") || "all", inputValue("cacheSourceFilter"));
  const list = document.querySelector<HTMLElement>("#cacheList");
  if (list) {
    list.innerHTML = items.length ? items.map((item) => `<article class="cache-item">
      <div class="cache-item-heading"><strong>${escapeHtml(cacheCategoryLabels[item.category] || item.category)}</strong><span class="status ${item.protected ? "interrupted" : "completed"}">${item.protected ? "引用保护" : "可参与清理"}</span><span>${cacheBytes(item.size_bytes)}</span></div>
      <div class="cache-item-meta"><span>状态：${escapeHtml(item.status || "未知")}</span><span>任务：${escapeHtml(item.task || "未知")}</span><span>创建：${escapeHtml(cacheTime(item.created_at))}</span><span>更新：${escapeHtml(cacheTime(item.modified_at))}</span></div>
      <p>来源：${escapeHtml(item.source_ref || "未知")}</p><p class="cache-path">${escapeHtml(item.path)}</p>
      <p class="field-note">引用：${escapeHtml(item.references?.length ? item.references.map(cacheMetric).join("；") : "无")}${item.protection_reasons?.length ? ` · ${escapeHtml(item.protection_reasons.join("；"))}` : ""}</p>
      <button type="button" class="secondary" data-cache-reveal="${encodeURIComponent(item.path)}">${item.category === "failed_recovery" ? "定位失败恢复材料" : "Finder 中显示"}</button>
    </article>`).join("") : '<p class="empty-state">当前筛选条件下没有缓存材料。</p>';
    list.querySelectorAll<HTMLButtonElement>("[data-cache-reveal]").forEach((button) => button.addEventListener("click", () => {
      const path = decodeURIComponent(button.dataset.cacheReveal || "");
      if (path) void invoke("reveal_path", { path }).catch((error) => cacheNotice(`定位失败：${errorMessage(error)}`));
    }));
  }
  const diagnosticList = document.querySelector<HTMLElement>("#cacheDiagnosticList");
  const sourceFilter = inputValue("cacheSourceFilter");
  const diagnostics = (cacheInventory.diagnostics || []).filter((item) => cacheSourceMatches([item.run_id, item.task, item.source_ref, item.status], sourceFilter));
  if (diagnosticList) diagnosticList.innerHTML = diagnostics.length ? diagnostics.map((item) => `<article class="cache-diagnostic-item">
    <strong>${escapeHtml(taskLabels[item.task as TaskType] || item.task || "任务未知")} · ${escapeHtml(item.status || "状态未知")}</strong><p>${escapeHtml(item.source_ref || "来源未知")}</p>
    <p class="field-note">${escapeHtml(cacheTime(item.started_at))} → ${escapeHtml(cacheTime(item.finished_at))}</p>
    <dl class="cache-metrics">${Object.entries({ "总耗时（秒）": item.duration_seconds, "阶段耗时（秒）": item.stage_durations, "模型调用总次数": item.model_calls, "LLM 调用次数": item.llm_calls, "ASR 调用次数": item.asr_calls, "输入 tokens": item.prompt_tokens, "输出 tokens": item.completion_tokens, "总 tokens": item.total_tokens, "推理 tokens": item.reasoning_tokens, "重试原因": item.retries, "缓存命中": item.cache_hits, "生效配置": item.effective_config }).map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(cacheMetric(value))}</dd></div>`).join("")}</dl>
  </article>`).join("") : '<p class="empty-state">没有可用的任务诊断。阶段耗时、模型调用次数、tokens、重试原因、缓存命中及生效配置：未知。</p>';
}

async function saveCachePolicy(): Promise<void> {
  await cacheOperation(async () => {
    const categories = selectedCacheCategories();
    if (!categories.length) throw new Error("请至少选择一个缓存类别");
    const retentionDays = cacheRetentionDays(inputValue("cacheRetentionDays"));
    const policy = await invokeCache<CacheInventory["policy"]>("policy", { auto_enabled: checkboxChecked("cacheAutoEnabled"), retention_days: retentionDays, categories });
    if (cacheInventory) cacheInventory.policy = policy;
    invalidateCachePreview();
    cacheNotice(`保留策略已保存：自动清理${policy.auto_enabled ? "已启用" : "关闭"}，保留最近 ${policy.retention_days} 天，适用于 ${categories.map((category) => cacheCategoryLabels[category]).join("、")}。`);
  });
}

async function previewCacheCleanup(): Promise<void> {
  await cacheOperation(async () => {
    await syncCacheReferences();
    const categories = selectedCacheCategories();
    if (!categories.length) throw new Error("请至少选择一个缓存类别");
    const olderThanDays = cacheRetentionDays(inputValue("cacheRetentionDays"));
    cachePreview = await invokeCache<CachePreview>("preview", { categories, older_than_days: olderThanDays });
    const preview = document.querySelector<HTMLElement>("#cachePreviewList");
    if (preview) preview.innerHTML = `<p><strong>可清理 ${cachePreview.candidate_count} 项 · ${cacheBytes(cachePreview.total_bytes)}</strong>；跳过 ${cachePreview.skipped.length} 项。预览有效至 ${escapeHtml(cacheTime(cachePreview.expires_at))}。</p>${cachePreview.candidates.length ? `<ul>${cachePreview.candidates.map((item) => `<li>${escapeHtml(item.path)} · ${cacheBytes(item.size_bytes)}</li>`).join("")}</ul>` : ""}`;
    cacheNotice("清理预览已生成；引用保护材料不会删除。确认具体文件后可手动清理。");
  });
}

async function cleanCachePreview(): Promise<void> {
  const preview = cachePreview;
  if (!preview?.candidate_count || isWorkerRunning || queueRunning || cacheBusy) return;
  if (!window.confirm(cachePreviewConfirmation(preview))) return;
  await cacheOperation(async () => {
    await syncCacheReferences();
    const result = await invokeCache<{ deleted: unknown[]; skipped: unknown[]; deleted_bytes: number }>("clean", { preview_id: preview.preview_id });
    cachePreview = null;
    cacheInventory = await invokeCache<CacheInventory>("inventory");
    renderCacheInventory();
    const target = document.querySelector<HTMLElement>("#cachePreviewList");
    if (target) target.textContent = `已删除 ${result.deleted.length} 项（${cacheBytes(result.deleted_bytes)}），跳过 ${result.skipped.length} 项。`;
    cacheNotice("清理完成；占用统计已重新读取，可与预览核对。");
  });
}

async function exportCacheDiagnostics(): Promise<void> {
  await cacheOperation(async () => {
    const result = await invokeCache<{ filename: string; content: string }>("export", { include_original: checkboxChecked("cacheIncludeOriginal"), include_paths: checkboxChecked("cacheIncludePaths") });
    const url = URL.createObjectURL(new Blob([result.content], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = result.filename;
    link.click();
    URL.revokeObjectURL(url);
    cacheNotice("诊断 JSON 已导出。导出内容不会写入任务日志。");
  });
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
    const defaultPath = dialogDefaultPath(targetId);
    const selected = await open(defaultPath ? { ...options, defaultPath } : options);
    const path = Array.isArray(selected) ? selected[0] : selected;
    if (typeof path === "string" && path) {
      setInputValue(targetId, path);
      if (targetId === "outputDir") rememberTaskPath("outputDir");
      if (targetId === "source") rememberTaskPath("source");
      if (targetId !== "source") saveSettings();
    }
  } catch (error) {
    appendOutput(`\n路径选择失败：${errorMessage(error)}\n`);
  }
}

function dialogDefaultPath(targetId: "outputRoot" | "outputDir" | "source" | "chromeProfile" | "asrModel"): string {
  const current = inputValue(targetId);
  if (targetId === "outputDir" || targetId === "source") return pathDialogDefault(targetId, current);
  return current;
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
  const isVideoTask = ["bilibili-url", "bilibili-favorite", "local-video"].includes(task);
  document.querySelector<HTMLElement>("#videoOutputModeField")?.classList.toggle("hidden", !isVideoTask);
  document.querySelector<HTMLElement>("#videoProofreadTitle")?.classList.toggle("hidden", !isVideoTask);
  document.querySelector<HTMLElement>("#videoProofreadFields")?.classList.toggle("hidden", !isVideoTask);
  document.querySelector<HTMLElement>("#videoSummaryTitle")?.classList.toggle("hidden", !isVideoTask);
  document.querySelector<HTMLElement>("#videoSummaryFields")?.classList.toggle("hidden", !isVideoTask);
  const transcriptionOnly = isVideoTask && inputValue("videoOutputMode") === "transcription-only";
  const keepSubtitles = document.querySelector<HTMLInputElement>("#keepOriginalSubtitles");
  if (keepSubtitles) {
    if (transcriptionOnly) keepSubtitles.checked = true;
    keepSubtitles.disabled = transcriptionOnly;
  }
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
  const dateInFilenameField = document.querySelector<HTMLElement>("#dateInFilenameField");
  if (dateInFilenameField) {
    dateInFilenameField.classList.toggle("hidden", !["bilibili-opus", "bilibili-up-opus"].includes(task));
  }
  document.querySelector<HTMLElement>("#dateInFilenameNote")?.classList.toggle(
    "hidden",
    !["bilibili-opus", "bilibili-up-opus"].includes(task) || !checkboxChecked("dateInFilename"),
  );
  const enableThinkingField = document.querySelector<HTMLElement>("#enableThinkingField");
  if (enableThinkingField) {
    enableThinkingField.classList.toggle(
      "hidden",
      !["bilibili-url", "bilibili-favorite", "bilibili-opus", "bilibili-up-opus", "local-video", "web-url", "source-file", "ai-chat"].includes(task),
    );
  }
  document.querySelector<HTMLElement>("#enableThinkingNote")?.classList.toggle(
    "hidden",
    !["bilibili-url", "bilibili-favorite", "bilibili-opus", "bilibili-up-opus", "local-video", "web-url", "source-file", "ai-chat"].includes(task) || !checkboxChecked("enableThinking"),
  );
  document.querySelector<HTMLElement>("#renameNotesDate")?.classList.toggle(
    "hidden",
    !["bilibili-opus", "bilibili-up-opus"].includes(task),
  );
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

function hydrateRuntimeControls(): void {
  const advanced = inputValue("runtimeBackend") === "conda";
  for (const id of ["condaEnvField", "condaBinField", "pythonBinField"]) {
    document.querySelector<HTMLElement>(`#${id}`)?.classList.toggle("hidden", !advanced);
  }
  const note = document.querySelector<HTMLElement>("#runtimeBackendNote");
  if (note) {
    note.textContent = advanced
      ? "Conda 选择会保存在本机并在下次启动继续使用。安装版会自动查找常见位置；找不到时请填写 conda 可执行文件的绝对路径。"
      : "推荐用于安装版。首次使用请点击“安装/修复”，运行环境会安装到 Application Support，不依赖 Homebrew 或 Conda。";
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
    "condaBin",
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
    "videoOutputMode",
    "proofreadChunkChars",
    "proofreadTimeoutSeconds",
    "proofreadRetryCount",
    "proofreadCooldownDelay",
    "summaryChunkChars",
    "summaryTimeoutSeconds",
    "summaryRetryCount",
    "summaryCooldownDelay",
  ]) {
    document.querySelector<HTMLInputElement>(`#${id}`)?.addEventListener("input", saveSettings);
  }
  document.querySelector<HTMLSelectElement>("#runtimeBackend")?.addEventListener("change", () => {
    hydrateRuntimeControls();
    saveSettings();
  });
  document.querySelector<HTMLSelectElement>("#webCaptureMode")?.addEventListener("change", hydrateTaskControls);
  document.querySelector<HTMLSelectElement>("#videoOutputMode")?.addEventListener("change", () => {
    hydrateTaskControls();
    saveSettings();
  });
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
    "proofreadEnableThinking",
    "summaryEnableThinking",
    "continueQueueAfterFailure",
  ]) {
    document.querySelector<HTMLInputElement>(`#${id}`)?.addEventListener("change", saveSettings);
  }
  document.querySelector<HTMLInputElement>("#dateInFilename")?.addEventListener("change", () => {
    hydrateTaskControls();
    saveSettings();
  });
  document.querySelector<HTMLInputElement>("#enableThinking")?.addEventListener("change", () => {
    hydrateTaskControls();
    saveSettings();
  });
}

function readLegacySettings(): Record<string, unknown> {
  try {
    const raw = localStorage.getItem(settingsKey);
    const stored = raw ? JSON.parse(raw) : {};
    return stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
  } catch {
    return {};
  }
}

function saveSettings(): void {
  const collection = document.querySelector<HTMLSelectElement>("#collectionSelect");
  const settings: SavedSettings = {
    runtimeBackend: (inputValue("runtimeBackend") || defaults.runtimeBackend) as "managed" | "conda",
    runtimePreferenceConfirmed: true,
    condaEnv: inputValue("condaEnv") || defaults.condaEnv,
    condaBin: inputValue("condaBin"),
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
    dateInFilename: checkboxChecked("dateInFilename"),
    enableThinking: checkboxChecked("enableThinking"),
    videoOutputMode: (inputValue("videoOutputMode") || defaults.videoOutputMode) as "full" | "transcription-only",
    proofreadChunkChars: inputValue("proofreadChunkChars") || defaults.proofreadChunkChars,
    proofreadTimeoutSeconds: inputValue("proofreadTimeoutSeconds") || defaults.proofreadTimeoutSeconds,
    proofreadRetryCount: inputValue("proofreadRetryCount") || defaults.proofreadRetryCount,
    proofreadCooldownDelay: inputValue("proofreadCooldownDelay") || defaults.proofreadCooldownDelay,
    proofreadEnableThinking: checkboxChecked("proofreadEnableThinking"),
    summaryChunkChars: inputValue("summaryChunkChars"),
    summaryTimeoutSeconds: inputValue("summaryTimeoutSeconds"),
    summaryRetryCount: inputValue("summaryRetryCount"),
    summaryCooldownDelay: inputValue("summaryCooldownDelay"),
    summaryEnableThinking: checkboxChecked("summaryEnableThinking"),
    continueQueueAfterFailure: checkboxChecked("continueQueueAfterFailure"),
  };
  savedSettings = settings;
  currentProfile.settings = { ...settings };
  currentProfile.updatedAt = new Date().toISOString();
  desktopProfiles = { ...desktopProfiles, profiles: desktopProfiles.profiles.map((profile) => profile.id === currentProfile.id ? currentProfile : profile) };
  saveCredentials(currentProfile.credentialRef, { apiKey: settings.apiKey, cookies: settings.cookies, chromeProfile: settings.chromeProfile });
  saveDesktopProfiles(desktopProfiles);
}

function applySavedSettings(settings: SavedSettings): void {
  const textFields: Record<string, keyof SavedSettings> = {
    runtimeBackend: "runtimeBackend", condaEnv: "condaEnv", condaBin: "condaBin", pythonBin: "pythonBin",
    apiBase: "apiBase", apiKey: "apiKey", model: "model", asrModel: "asrModel", cookies: "cookies",
    chromeProfile: "chromeProfile", outputRoot: "outputRoot", favoriteLimit: "favoriteLimit",
    videoOutputMode: "videoOutputMode", timeoutSeconds: "timeoutSeconds", retryCount: "retryCount",
    cooldownDelay: "cooldownDelay", chunkChars: "chunkChars", proofreadChunkChars: "proofreadChunkChars",
    proofreadTimeoutSeconds: "proofreadTimeoutSeconds", proofreadRetryCount: "proofreadRetryCount",
    proofreadCooldownDelay: "proofreadCooldownDelay", summaryChunkChars: "summaryChunkChars",
    summaryTimeoutSeconds: "summaryTimeoutSeconds", summaryRetryCount: "summaryRetryCount",
    summaryCooldownDelay: "summaryCooldownDelay", browserExecutable: "browserExecutable",
  };
  for (const [id, key] of Object.entries(textFields)) setInputValue(id, String(settings[key] ?? ""), false);
  const selects: Record<string, keyof SavedSettings> = {
    subtitleStrategy: "subtitleStrategy", webCaptureMode: "webCaptureMode",
  };
  for (const [id, key] of Object.entries(selects)) setInputValue(id, String(settings[key] ?? ""), false);
  const checks: Record<string, keyof SavedSettings> = {
    extractKeyframes: "extractKeyframes", dialogueDetection: "dialogueDetection", keepOriginalSubtitles: "keepOriginalSubtitles",
    recursiveSearch: "recursiveSearch", overwriteOutputs: "overwriteOutputs", incognitoMode: "incognitoMode",
    stockTerms: "stockTerms", enableOcr: "enableOcr", ocrResume: "ocrResume", dateInFilename: "dateInFilename",
    enableThinking: "enableThinking", proofreadEnableThinking: "proofreadEnableThinking",
    summaryEnableThinking: "summaryEnableThinking", continueQueueAfterFailure: "continueQueueAfterFailure",
  };
  for (const [id, key] of Object.entries(checks)) {
    const input = document.querySelector<HTMLInputElement>(`#${id}`);
    if (input) input.checked = Boolean(settings[key]);
  }
  const select = document.querySelector<HTMLSelectElement>("#collectionSelect");
  if (select) {
    select.innerHTML = settings.collectionId
      ? `<option value="${escapeHtml(settings.collectionId)}" data-type="${escapeHtml(settings.collectionType)}" data-mid="${escapeHtml(settings.collectionMid)}">${escapeHtml(settings.collectionId)}（重新读取列表可查看名称）</option>`
      : '<option value="">请先读取列表</option>';
    select.value = settings.collectionId;
    select.dataset.type = settings.collectionType;
    select.dataset.id = settings.collectionId;
    select.dataset.mid = settings.collectionMid;
  }
  hydrateRuntimeControls();
  hydrateTaskControls();
  hydrateTaskOutput();
  // Persist only after every field is hydrated, never a mixture of two profiles.
  saveSettings();
}

function switchProfile(id: string): void {
  if (isWorkerRunning || queueRunning) {
    setState("任务运行期间不能切换配置档案");
    const selector = document.querySelector<HTMLSelectElement>("#profileSelect");
    if (selector) selector.value = currentProfile.id;
    return;
  }
  saveSettings();
  const next = desktopProfiles.profiles.find((profile) => profile.id === id);
  if (!next) return;
  currentProfile = next;
  desktopProfiles = { ...desktopProfiles, activeId: id };
  saveDesktopProfiles(desktopProfiles);
  savedSettings = { ...defaults, ...currentProfile.settings, ...loadCredentials(currentProfile.credentialRef) } as SavedSettings;
  applySavedSettings(savedSettings);
  setState(`已切换配置档案：${currentProfile.name}`);
}

function renderProfileSelector(): void {
  const selector = document.querySelector<HTMLSelectElement>("#profileSelect");
  if (!selector) return;
  selector.innerHTML = desktopProfiles.profiles.map((profile) => `<option value="${escapeHtml(profile.id)}" ${profile.id === currentProfile.id ? "selected" : ""}>${escapeHtml(profile.name)}</option>`).join("");
}

function createProfile(copy: boolean): void {
  if (isWorkerRunning || queueRunning) return;
  saveSettings();
  const name = window.prompt(copy ? "为副本命名" : "新配置档案名称", copy ? `${currentProfile.name} 副本` : "新配置");
  if (!name?.trim()) return;
  desktopProfiles = createDesktopProfile(desktopProfiles, name, copy ? currentProfile : undefined);
  currentProfile = desktopProfiles.profiles.find((profile) => profile.id === desktopProfiles.activeId)!;
  savedSettings = { ...defaults, ...currentProfile.settings, ...loadCredentials(currentProfile.credentialRef) } as SavedSettings;
  renderProfileSelector();
  applySavedSettings(savedSettings);
  setState(`已${copy ? "复制" : "创建"}配置档案：${currentProfile.name}`);
}

function renameProfile(): void {
  const name = window.prompt("配置档案新名称", currentProfile.name);
  if (!name?.trim()) return;
  desktopProfiles = renameDesktopProfile(desktopProfiles, currentProfile.id, name);
  currentProfile = desktopProfiles.profiles.find((profile) => profile.id === currentProfile.id)!;
  renderProfileSelector();
}

function deleteProfile(): void {
  if (isWorkerRunning || queueRunning) return;
  const referenced = queueEntries.some((entry) => entry.profileId === currentProfile.id && ["waiting", "interrupted", "failed"].includes(entry.status));
  if (referenced) {
    setState("该档案仍被待运行/失败队列项引用；先移除或重试这些队列项");
    return;
  }
  if (!window.confirm(`删除配置档案“${currentProfile.name}”？历史记录会保留，但之后将无法使用它的凭据引用。`)) return;
  try {
    const credentialRef = currentProfile.credentialRef;
    desktopProfiles = deleteDesktopProfile(desktopProfiles, currentProfile.id);
    deleteCredentials(credentialRef);
    currentProfile = desktopProfiles.profiles.find((profile) => profile.id === desktopProfiles.activeId)!;
    savedSettings = { ...defaults, ...currentProfile.settings, ...loadCredentials(currentProfile.credentialRef) } as SavedSettings;
    renderProfileSelector();
    applySavedSettings(savedSettings);
    setState("配置档案已删除");
  } catch (error) { setState(errorMessage(error)); }
}

function exportProfile(): void {
  const blob = new Blob([exportDesktopProfile(currentProfile)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${currentProfile.name.replace(/[^\p{L}\p{N}_-]+/gu, "_") || "profile"}.json`;
  link.click();
  URL.revokeObjectURL(url);
  setState("配置档案已导出（不含凭据）");
}

async function importProfile(input: HTMLInputElement): Promise<void> {
  const file = input.files?.[0];
  if (!file) return;
  try {
    const imported = importedDesktopProfile(await file.text());
    const name = window.prompt("导入档案名称", imported.name) || imported.name;
    if (!name.trim()) return;
    desktopProfiles = createDesktopProfile(desktopProfiles, name);
    currentProfile = desktopProfiles.profiles.find((profile) => profile.id === desktopProfiles.activeId)!;
    currentProfile.settings = imported.settings;
    desktopProfiles.profiles = desktopProfiles.profiles.map((profile) => profile.id === currentProfile.id ? currentProfile : profile);
    saveDesktopProfiles(desktopProfiles);
    savedSettings = { ...defaults, ...currentProfile.settings, ...loadCredentials(currentProfile.credentialRef) } as SavedSettings;
    renderProfileSelector();
    applySavedSettings(savedSettings);
    setState("配置档案已导入；请在本机补齐凭据");
  } catch (error) { setState(`导入失败：${errorMessage(error)}`); }
  finally { input.value = ""; }
}

function enqueueCurrentTask(): void {
  if (isWorkerRunning || queueRunning || cacheBusy) {
    setState("当前 Worker 正在运行；完成后可继续入队");
    return;
  }
  saveSettings();
  const request = payload(false) as Record<string, unknown>;
  const task = String(request.task || "");
  if (!String(request.output_dir || "").trim()) {
    setState("缺少输出目录，无法入队");
    return;
  }
  if (!String(request.source || "").trim() && task !== "bilibili-favorite") {
    setState("缺少输入源，无法入队");
    return;
  }
  if (task === "bilibili-favorite" && !String(request.collection_id || "")) {
    setState("未选择收藏夹/系列，无法入队");
    return;
  }
  const safeRequest = { ...request };
  delete safeRequest.api_key;
  delete safeRequest.cookies;
  delete safeRequest.browser_profile;
  safeRequest.dry_run = false;
  if (queueDuplicate(queueEntries, safeRequest)) {
    setState("该来源与输出目录已在队列中");
    return;
  }
  const entry: QueueEntry = {
    id: `queue-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    request: safeRequest,
    profileId: currentProfile.id,
    status: "waiting",
    addedAt: new Date().toISOString(),
  };
  queueEntries = [...queueEntries, entry];
  persistTaskQueue();
  renderQueue();
  setState(`已加入串行队列（${queueEntries.filter((item) => item.status === "waiting").length} 项等待）`);
}

function renderQueue(): void {
  const target = document.querySelector<HTMLElement>("#queueList");
  if (!target) return;
  target.innerHTML = queueEntries.length ? queueEntries.map((entry, index) => {
    const request = entry.request;
    const source = String(request.source || request.collection_id || "");
    const profile = desktopProfiles.profiles.find((item) => item.id === entry.profileId);
    const task = String(request.task || "unknown");
    const waitingIndexes = queueEntries.flatMap((item, position) => item.status === "waiting" ? [position] : []);
    const waitingSlot = waitingIndexes.indexOf(index);
    const movable = entry.status === "waiting" && !queueRunning;
    return `<article class="history-item">
      <div><span class="status ${entry.status}">${queueStatusLabels[entry.status]}</span><strong>${escapeHtml(taskLabels[task as TaskType] || task)}</strong><small>${escapeHtml(profile?.name || "配置档案缺失")} · ${escapeHtml(source)} · ${escapeHtml(String(request.output_dir || ""))}</small>${entry.error ? `<small class="error-text">${escapeHtml(entry.error)}</small>` : ""}</div>
      <div class="row-actions">
        ${movable ? `<button type="button" class="secondary" data-queue-action="up" data-id="${entry.id}" ${waitingSlot <= 0 ? "disabled" : ""}>上移</button><button type="button" class="secondary" data-queue-action="down" data-id="${entry.id}" ${waitingSlot === waitingIndexes.length - 1 ? "disabled" : ""}>下移</button>` : ""}
        ${entry.status === "failed" || entry.status === "interrupted" ? `<button type="button" class="secondary" data-queue-action="retry" data-id="${entry.id}">重试</button>` : ""}
        ${entry.status !== "running" ? `<button type="button" class="secondary danger-outline" data-queue-action="remove" data-id="${entry.id}">移除</button>` : ""}
      </div>
    </article>`;
  }).join("") : '<p class="empty-state">队列为空。先填写任务，再点击“当前任务入队”。</p>';
  target.querySelectorAll<HTMLButtonElement>("button[data-queue-action]").forEach((button) => button.addEventListener("click", () => {
    if (cacheBusy) return;
    const id = button.dataset.id || "";
    const action = button.dataset.queueAction;
    if (action === "up" || action === "down") queueEntries = moveQueueEntry(queueEntries, id, action === "up" ? -1 : 1);
    else if (action === "retry") queueEntries = queueEntries.map((entry) => entry.id === id && ["failed", "interrupted", "cancelled"].includes(entry.status) ? { ...entry, status: "waiting", error: undefined, endedAt: undefined } : entry);
    else if (action === "remove") queueEntries = queueEntries.filter((entry) => entry.id !== id || entry.status === "running");
    persistTaskQueue();
    renderQueue();
  }));
}

function pauseQueue(): void {
  queuePaused = true;
  const notice = document.querySelector<HTMLElement>("#queueNotice");
  if (notice) notice.textContent = queueRunning ? "已请求暂停：当前任务结束后将停止启动下一项。" : "调度已暂停；点击“开始/继续”启动等待项。";
  if (!queueRunning) setState("队列调度已暂停");
}

async function startQueue(): Promise<void> {
  if (queueRunning || isWorkerRunning || cacheBusy) {
    setState("已有 Worker 或队列正在运行");
    return;
  }
  const interrupted = queueEntries.filter((entry) => entry.status === "interrupted");
  if (interrupted.length) {
    const resume = window.confirm(`发现 ${interrupted.length} 个上次关闭时中断的队列项。\n\n确认后将这些项改为等待并从头重新执行；不会把它们当作已完成。`);
    if (!resume) return;
    queueEntries = queueEntries.map((entry) => entry.status === "interrupted" ? { ...entry, status: "waiting", error: undefined } : entry);
    persistTaskQueue();
  }
  if (!queueEntries.some((entry) => entry.status === "waiting")) {
    setState("队列中没有等待项");
    return;
  }
  queuePaused = false;
  queueRunning = true;
  const continueOnFailure = checkboxChecked("continueQueueAfterFailure");
  const notice = document.querySelector<HTMLElement>("#queueNotice");
  if (notice) notice.textContent = continueOnFailure ? "串行运行中；单项失败后继续后续项。" : "串行运行中；单项失败后暂停后续项。";
  renderQueue();
  try {
    while (!queuePaused) {
      const entry = queueEntries.find((item) => item.status === "waiting");
      if (!entry) break;
      const profile = desktopProfiles.profiles.find((item) => item.id === entry.profileId);
      if (!profile || !hasCredentialReference(profile.credentialRef)) {
        updateQueueEntry(entry.id, { status: "failed", error: "配置档案或凭据引用已失效；请修复档案后重试或移除此项。", endedAt: new Date().toISOString() });
        if (!continueOnFailure) break;
        continue;
      }
      const credentials = loadCredentials(profile.credentialRef);
      const request: Record<string, unknown> = {
        ...entry.request,
        desktop_profile_id: profile.id,
        api_key: credentials.apiKey || "",
        cookies: credentials.cookies || "",
        browser_profile: credentials.chromeProfile || "",
        dry_run: false,
      };
      updateQueueEntry(entry.id, { status: "running", startedAt: new Date().toISOString(), endedAt: undefined, error: undefined });
      const history = createHistoryEntry(String(request.task || "unknown"), request);
      request.run_id = history.id;
      history.request.run_id = history.id;
      updateQueueEntry(entry.id, { historyId: history.id, request: { ...entry.request, run_id: history.id } });
      taskHistory = upsertHistoryEntry(taskHistory, history);
      persistTaskHistory();
      persistTaskQueue();
      activeHistoryEntry = history;
      setWorkerRunning(true);
      setState(`队列运行中：${taskLabels[String(request.task || "") as TaskType] || String(request.task || "")}`);
      setOutput("");
      appendOutput("队列任务已启动，日志会实时追加到这里。\n");
      let failed = false;
      let cancelled = false;
      try {
        await syncCacheReferences();
        const output = await invokeWorker(request);
        if (!currentOutput().trim()) setOutput(output || "(worker 没有返回输出)");
        updateBatchResult(output || currentOutput());
        const result = taskResultFromLog(output || currentOutput());
        if (result) renderOutputs(result.outputs);
        failed = result?.status === "partial_failed" || Number(result?.counts?.failed || 0) > 0 || result?.status === "failed";
        history.status = failed ? "failed" : "completed";
        history.outputs = result?.outputs || [];
        if (failed) history.error = `部分失败：${Number(result?.counts?.failed || 0)} 项未完成`;
        setState(failed ? "队列任务部分失败" : result?.status === "no_changes" ? noChangesStatusLabel(result) : "队列任务完成");
      } catch (error) {
        const message = errorMessage(error);
        cancelled = message.startsWith("Task cancelled.");
        failed = true;
        history.status = cancelled ? "cancelled" : "failed";
        history.error = message;
        if (currentOutput().trim()) appendOutput(`\n队列任务失败：${message}\n`);
        else setOutput(message);
        setState(cancelled ? "队列任务已取消" : "队列任务失败");
      } finally {
        history.log = currentOutput();
        history.endedAt = new Date().toISOString();
        taskHistory = upsertHistoryEntry(taskHistory, history);
        persistTaskHistory();
        activeHistoryEntry = null;
        updateQueueEntry(entry.id, { status: history.status === "completed" ? "completed" : history.status === "cancelled" ? "cancelled" : "failed", error: history.error, historyId: history.id, endedAt: new Date().toISOString() });
        setWorkerRunning(false);
        renderHistory();
      }
      if (cancelled) {
        queuePaused = true;
        break;
      }
      if (failed && !continueOnFailure) {
        queuePaused = true;
        break;
      }
    }
  } finally {
    queueRunning = false;
    if (isWorkerRunning) setWorkerRunning(false);
    persistTaskQueue();
    renderQueue();
    if (notice) notice.textContent = queuePaused ? "调度已暂停；等待项会保留。" : "队列运行结束。已完成项不会在重启后重复执行。";
  }
}

function updateQueueEntry(id: string, patch: Partial<QueueEntry>): void {
  queueEntries = queueEntries.map((entry) => entry.id === id ? { ...entry, ...patch } : entry);
  persistTaskQueue();
  renderQueue();
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

function managedAsrModelPath(text: string): string {
  const match = text.match(/^默认 ASR 模型：(.+)$/m);
  return match?.[1]?.trim() ?? "";
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
    "renameNotesDate",
    "refreshManifests",
    "clearHistory",
    "runtimeInstall",
    "runtimeRemove",
  ]) {
    const button = document.querySelector<HTMLButtonElement>(`#${id}`);
    if (button) button.disabled = running;
  }
  const cancelButton = document.querySelector<HTMLButtonElement>("#cancelTask");
  if (cancelButton) cancelButton.disabled = !running;
  document
    .querySelectorAll<HTMLButtonElement | HTMLSelectElement | HTMLInputElement>(
      "button[data-manifest-action], select[data-manifest-status], select[data-manifest-batch-status], input[data-manifest-record-select], input[data-manifest-select-all], button[data-history-action='rerun'], button[data-history-action='delete']",
    )
    .forEach((control) => {
      control.disabled = running;
    });
  document.querySelectorAll<HTMLElement>(".manifest-card").forEach(updateManifestSelectionState);
  updateCacheButtons();
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
    items: Array<{
      source: string;
      output: string;
      status: string;
      error: string;
      reason?: string;
      record_index: number;
      record_kind: "manifest-json" | "processed-text" | "bilibili-failures";
      manual_status?: string;
    }>;
    error?: string;
  }>;
  totals: Record<"processed" | "skipped" | "failed" | "rebuild", number>;
};

async function refreshManifestStatus(manageBusy = true): Promise<void> {
  if (manageBusy && isWorkerRunning) return;
  if (manageBusy) setWorkerRunning(true);
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
    if (manageBusy) setWorkerRunning(false);
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
  captureManifestViewState(list);
  list.innerHTML = data.manifests.length
    ? [...data.manifests]
        .sort((left, right) => right.counts.failed + right.counts.rebuild - (left.counts.failed + left.counts.rebuild))
        .map((manifest) => {
          const label = manifestLabel(manifest.name);
          const total = Object.values(manifest.counts).reduce((sum, count) => sum + count, 0);
          const attention = manifest.counts.failed + manifest.counts.rebuild;
          const health = attention > 0 ? `${attention} 条需要处理` : "状态正常";
          const view = manifestViewState.get(manifest.path);
          const filter = view?.filter || (attention > 0 ? "attention" : "all");
          const kind = manifest.items[0]?.record_kind || "manifest-json";
          const supportsStatus = kind !== "processed-text";
          return `<details class="manifest-card ${attention > 0 ? "has-issues" : ""}" data-manifest-path="${escapeHtml(manifest.path)}" ${view?.open ? "open" : ""}>
            <summary>
              <span class="manifest-title"><strong>${escapeHtml(label.title)}</strong><small>${escapeHtml(label.description)}</small></span>
              <span class="manifest-health ${attention > 0 ? "warning" : "ok"}">${total} 条记录 · ${health}</span>
            </summary>
            <div class="manifest-detail">
              <p class="manifest-counts">正常 ${manifest.counts.processed} · 本次跳过 ${manifest.counts.skipped} · 失败 ${manifest.counts.failed} · 输出缺失 ${manifest.counts.rebuild}</p>
              ${manifest.error ? `<p class="error-text">读取记录失败：${escapeHtml(manifest.error)}</p>` : ""}
              ${manifest.items.length
                ? `<label class="manifest-filter">显示
                    <select data-manifest-filter>
                      <option value="attention" ${filter === "attention" ? "selected" : ""}>需要处理</option>
                      <option value="all" ${filter === "all" ? "selected" : ""}>全部记录</option>
                      <option value="processed" ${filter === "processed" ? "selected" : ""}>正常</option>
                      <option value="skipped" ${filter === "skipped" ? "selected" : ""}>已跳过</option>
                      <option value="failed" ${filter === "failed" ? "selected" : ""}>失败</option>
                      <option value="rebuild" ${filter === "rebuild" ? "selected" : ""}>输出缺失</option>
                    </select>
                  </label>
                  <div class="manifest-batch-toolbar">
                    <label class="manifest-select-all"><input type="checkbox" data-manifest-select-all />选择当前显示</label>
                    <span data-manifest-selected-count>已选择 0 条</span>
                    ${supportsStatus
                      ? `<select data-manifest-batch-status aria-label="批量状态">
                          <option value="auto">恢复自动判断</option>
                          <option value="processed">标记为正常</option>
                          <option value="skipped">标记为已跳过</option>
                          <option value="failed">标记为失败</option>
                          <option value="rebuild">标记为输出缺失</option>
                        </select>
                        <button type="button" class="secondary" data-manifest-batch-action="set-status" data-path="${escapeHtml(manifest.path)}" data-kind="${escapeHtml(kind)}" disabled>批量应用</button>`
                      : ""}
                    <button type="button" class="secondary danger-outline" data-manifest-batch-action="delete" data-path="${escapeHtml(manifest.path)}" data-kind="${escapeHtml(kind)}" disabled>批量删除</button>
                  </div>
                  <div class="manifest-items">${manifest.items.map((item) => manifestItemHtml(manifest.path, item)).join("")}</div>`
                : '<p class="empty-state">没有可编辑的记录。</p>'}
              <small class="manifest-path">记录文件：${escapeHtml(manifest.path)}</small>
            </div>
          </details>`;
        })
        .join("")
    : '<p class="empty-state">尚未找到 Manifest。运行一次支持增量状态的任务后再刷新。</p>';

  list.querySelectorAll<HTMLDetailsElement>("details[data-manifest-path]").forEach((details) => {
    details.addEventListener("toggle", () => {
      const path = details.dataset.manifestPath || "";
      const filter = details.querySelector<HTMLSelectElement>("select[data-manifest-filter]")?.value || "all";
      manifestViewState.remember(path, details.open, filter);
    });
  });
  list.querySelectorAll<HTMLSelectElement>("select[data-manifest-filter]").forEach((select) => {
    const card = select.closest<HTMLElement>(".manifest-card");
    const applyFilter = () => {
      const filter = select.value;
      card?.querySelectorAll<HTMLElement>(".manifest-record").forEach((record) => {
        const status = record.dataset.status || "";
        record.classList.toggle("hidden", filter !== "all" && (filter === "attention" ? !["failed", "rebuild"].includes(status) : status !== filter));
      });
      if (card) {
        const path = card.dataset.manifestPath || "";
        manifestViewState.remember(path, (card as HTMLDetailsElement).open, filter);
        updateManifestSelectionState(card);
      }
    };
    select.addEventListener("change", applyFilter);
    applyFilter();
  });
  list.querySelectorAll<HTMLButtonElement>("button[data-manifest-action]").forEach((button) => {
    button.addEventListener("click", () => void handleManifestAction(button));
  });
  list.querySelectorAll<HTMLInputElement>("input[data-manifest-record-select]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const card = checkbox.closest<HTMLElement>(".manifest-card");
      if (card) updateManifestSelectionState(card);
    });
  });
  list.querySelectorAll<HTMLInputElement>("input[data-manifest-select-all]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const card = checkbox.closest<HTMLElement>(".manifest-card");
      if (!card) return;
      card.querySelectorAll<HTMLInputElement>(".manifest-record:not(.hidden) input[data-manifest-record-select]").forEach((item) => {
        item.checked = checkbox.checked;
      });
      updateManifestSelectionState(card);
    });
  });
  list.querySelectorAll<HTMLButtonElement>("button[data-manifest-batch-action]").forEach((button) => {
    button.addEventListener("click", () => void handleManifestBatchAction(button));
  });
}

function captureManifestViewState(list: HTMLElement): void {
  list.querySelectorAll<HTMLDetailsElement>("details[data-manifest-path]").forEach((details) => {
    const path = details.dataset.manifestPath || "";
    const filter = details.querySelector<HTMLSelectElement>("select[data-manifest-filter]")?.value || "all";
    manifestViewState.remember(path, details.open, filter);
  });
}

function updateManifestSelectionState(card: HTMLElement): void {
  const visible = [...card.querySelectorAll<HTMLInputElement>(".manifest-record:not(.hidden) input[data-manifest-record-select]")];
  const selected = [...card.querySelectorAll<HTMLInputElement>("input[data-manifest-record-select]:checked")];
  const selectAll = card.querySelector<HTMLInputElement>("input[data-manifest-select-all]");
  if (selectAll) {
    const visibleSelected = visible.filter((item) => item.checked).length;
    selectAll.checked = visible.length > 0 && visibleSelected === visible.length;
    selectAll.indeterminate = visibleSelected > 0 && visibleSelected < visible.length;
    selectAll.disabled = isWorkerRunning || visible.length === 0;
  }
  const count = card.querySelector<HTMLElement>("[data-manifest-selected-count]");
  if (count) count.textContent = `已选择 ${selected.length} 条`;
  card.querySelectorAll<HTMLButtonElement>("button[data-manifest-batch-action]").forEach((button) => {
    button.disabled = isWorkerRunning || selected.length === 0;
  });
}

function manifestItemHtml(path: string, item: ManifestStatus["manifests"][number]["items"][number]): string {
  const label = item.source || item.output || "未命名条目";
  const detail = item.reason || item.error;
  const statusControls = item.record_kind === "processed-text"
    ? '<small class="manifest-auto-note">该列表只记录“已处理”；如需重新处理，请删除记录。</small>'
    : `<select data-manifest-status aria-label="手动状态">
        <option value="auto" ${!item.manual_status ? "selected" : ""}>自动判断</option>
        <option value="processed" ${item.manual_status === "processed" ? "selected" : ""}>正常</option>
        <option value="skipped" ${item.manual_status === "skipped" ? "selected" : ""}>已跳过</option>
        <option value="failed" ${item.manual_status === "failed" ? "selected" : ""}>失败</option>
        <option value="rebuild" ${item.manual_status === "rebuild" ? "selected" : ""}>输出缺失</option>
      </select>
      <button type="button" class="secondary" data-manifest-action="set-status">保存状态</button>`;
  return `<article class="manifest-record" data-status="${escapeHtml(item.status)}" data-path="${escapeHtml(path)}" data-kind="${escapeHtml(item.record_kind)}" data-index="${item.record_index}">
    <div class="manifest-record-main">
      <input type="checkbox" data-manifest-record-select aria-label="选择 ${escapeHtml(label)}" />
      <span class="status ${escapeHtml(item.status)}">${manifestStatusLabel(item.status)}</span>
      <code>${escapeHtml(label)}</code>
      ${item.output && item.output !== label ? `<small class="manifest-output">输出：${escapeHtml(item.output)}</small>` : ""}
      ${detail ? `<small class="manifest-reason">${escapeHtml(detail)}</small>` : ""}
    </div>
    <div class="row-actions manifest-actions">
      ${statusControls}
      <button type="button" class="secondary danger-outline" data-manifest-action="delete">删除记录</button>
    </div>
  </article>`;
}

async function handleManifestAction(button: HTMLButtonElement): Promise<void> {
  if (isWorkerRunning) return;
  const action = button.dataset.manifestAction;
  const record = button.closest<HTMLElement>(".manifest-record");
  if (!record) return;
  const label = record?.querySelector("code")?.textContent || "这条记录";
  if (action === "delete" && !window.confirm(`确定删除“${label}”的处理记录吗？\n\n只删除记录，不会删除源文件或输出文件。`)) return;
  const status = record?.querySelector<HTMLSelectElement>("select[data-manifest-status]")?.value || "auto";
  await mutateManifestRecords(record.dataset.path || "", record.dataset.kind || "", [Number(record.dataset.index)], action || "", status);
}

async function handleManifestBatchAction(button: HTMLButtonElement): Promise<void> {
  if (isWorkerRunning) return;
  const card = button.closest<HTMLElement>(".manifest-card");
  if (!card) return;
  const selected = [...card.querySelectorAll<HTMLInputElement>("input[data-manifest-record-select]:checked")];
  const indexes = selected.map((checkbox) => Number(checkbox.closest<HTMLElement>(".manifest-record")?.dataset.index)).filter(Number.isInteger);
  if (!indexes.length) return;
  const action = button.dataset.manifestBatchAction || "";
  if (action === "delete" && !window.confirm(`确定删除选中的 ${indexes.length} 条处理记录吗？\n\n只删除记录，不会删除源文件或输出文件。`)) return;
  const status = card.querySelector<HTMLSelectElement>("select[data-manifest-batch-status]")?.value || "auto";
  await mutateManifestRecords(button.dataset.path || "", button.dataset.kind || "", indexes, action, status);
}

async function mutateManifestRecords(path: string, kind: string, indexes: number[], action: string, status: string): Promise<void> {
  const card = [...document.querySelectorAll<HTMLDetailsElement>("details[data-manifest-path]")].find((details) => details.dataset.manifestPath === path);
  const filter = card?.querySelector<HTMLSelectElement>("select[data-manifest-filter]")?.value || "all";
  manifestViewState.keepOpen(path, filter);
  setWorkerRunning(true);
  setState(action === "delete" ? `删除 ${indexes.length} 条处理记录...` : `更新 ${indexes.length} 条记录状态...`);
  try {
    await invokeWorkerQuiet({
      ...payload(false),
      task: "manifest-update",
      source: inputValue("outputRoot"),
      manifest_path: path,
      manifest_kind: kind,
      manifest_indexes: indexes,
      manifest_action: action,
      manifest_status: status,
    });
    await refreshManifestStatus(false);
    setState(action === "delete" ? `已删除 ${indexes.length} 条处理记录` : status === "auto" ? `已恢复 ${indexes.length} 条记录的自动判断` : `已更新 ${indexes.length} 条记录状态`);
  } catch (error) {
    setState("处理记录修改失败");
    appendOutput(`\n处理记录修改失败：${errorMessage(error)}\n`);
  } finally {
    setWorkerRunning(false);
  }
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

function renderHistory(): void {
  const target = document.querySelector<HTMLElement>("#historyList");
  if (!target) return;
  const visibleHistory = filterTaskHistory(taskHistory, historyFilter);
  const count = document.querySelector<HTMLElement>("#historyCount");
  if (count) count.textContent = `${visibleHistory.length}/${taskHistory.length} 条`;
  target.innerHTML = visibleHistory.length
    ? visibleHistory
        .map((entry) => {
          const source = String(entry.request.source || "");
          return `<article class="history-item">
            <div><span class="status ${entry.status}">${historyStatusLabels[entry.status]}</span><strong>${escapeHtml(taskLabels[entry.task as TaskType] || entry.task)}</strong><small>${escapeHtml(new Date(entry.startedAt).toLocaleString())}${source ? ` · ${escapeHtml(source)}` : ""}</small></div>
            <div class="row-actions">
              <button type="button" class="secondary" data-history-action="log" data-id="${entry.id}">查看日志</button>
              ${["failed", "cancelled", "interrupted"].includes(entry.status) ? `<button type="button" class="secondary" data-history-action="diagnostics" data-id="${entry.id}">定位诊断与恢复材料</button>` : ""}
              ${entry.outputs.length ? `<button type="button" class="secondary" data-history-action="outputs" data-id="${entry.id}">查看 ${entry.outputs.length} 个输出</button>` : ""}
              <button type="button" class="secondary" data-history-action="rerun-current" data-id="${entry.id}">用当前配置重跑</button>
              <button type="button" class="secondary" data-history-action="rerun-snapshot" data-id="${entry.id}">按历史参数重跑</button>
              <button type="button" class="secondary danger-outline" data-history-action="delete" data-id="${entry.id}">删除记录</button>
            </div>
          </article>`;
        })
        .join("")
    : `<p class="empty-state">${taskHistory.length ? "当前筛选条件下没有任务。" : "还没有任务历史。运行中的任务若遇到应用退出，会在下次启动时标记为“已中断”并可重新运行。"}</p>`;
  target.querySelectorAll<HTMLButtonElement>("button[data-history-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const entry = taskHistory.find((item) => item.id === button.dataset.id);
      if (!entry) return;
      if (button.dataset.historyAction === "log") {
        setOutput(entry.log || entry.error || "该任务没有保存日志。");
        setState(`历史：${historyStatusLabels[entry.status]}`);
      } else if (button.dataset.historyAction === "diagnostics") {
        appTabs.activate("validation");
        setInputValue("cacheSourceFilter", String(entry.request.run_id || taskResultFromLog(entry.log)?.run_id || entry.task), false);
        setInputValue("cacheCategoryFilter", "all", false);
        void refreshCacheInventory();
      } else if (button.dataset.historyAction === "outputs") {
        renderOutputs(entry.outputs, `历史输出 · ${taskLabels[entry.task as TaskType] || entry.task}`, true);
        setState(`已显示历史输出（${entry.outputs.length} 个文件）`);
      } else if (button.dataset.historyAction === "delete") {
        deleteHistoryEntry(entry);
      } else if (button.dataset.historyAction === "rerun-current") {
        const replay = historyReplayRequest(entry.request);
        applyHistoryRequest(replay);
        void runTask(false, entry.status === "failed" || Boolean(entry.request.retry_failed), entry.id, replay);
      } else {
        const profileId = String(entry.request.desktop_profile_id || "");
        const historicalProfile = desktopProfiles.profiles.find((profile) => profile.id === profileId);
        if (!historicalProfile || !hasCredentialReference(historicalProfile.credentialRef)) {
          setState("历史配置或凭据引用已失效；请先修复档案，或使用当前配置重跑");
          return;
        }
        void runTask(false, entry.status === "failed" || Boolean(entry.request.retry_failed), entry.id, entry.request);
      }
    });
  });
}

function deleteHistoryEntry(entry: TaskHistoryEntry): void {
  if (isWorkerRunning || cacheBusy) return;
  const taskName = taskLabels[entry.task as TaskType] || entry.task;
  if (!window.confirm(`确定删除“${taskName}”这条任务历史吗？\n\n只删除历史记录，不会删除输出文件。`)) return;
  taskHistory = removeHistoryEntry(taskHistory, entry.id);
  persistTaskHistory();
  renderHistory();
  setState("任务历史已删除");
}

function applyHistoryRequest(request: Record<string, unknown>): void {
  appTabs.activate("task");
  const replayRequest = historyReplayRequest(request);
  const mappings: Record<string, string> = {
    task: "taskType",
    source: "source",
    output_dir: "outputDir",
    output_filename: "outputFilename",
    subtitle_strategy: "subtitleStrategy",
    favorite_limit: "favoriteLimit",
    web_capture_mode: "webCaptureMode",
    browser_executable: "browserExecutable",
    timeout_seconds: "timeoutSeconds",
    retry_count: "retryCount",
    cooldown_delay: "cooldownDelay",
    chunk_chars: "chunkChars",
    video_output_mode: "videoOutputMode",
    proofread_chunk_chars: "proofreadChunkChars",
    proofread_timeout_seconds: "proofreadTimeoutSeconds",
    proofread_retry_count: "proofreadRetryCount",
    proofread_cooldown_delay: "proofreadCooldownDelay",
    summary_chunk_chars: "summaryChunkChars",
    summary_timeout_seconds: "summaryTimeoutSeconds",
    summary_retry_count: "summaryRetryCount",
    summary_cooldown_delay: "summaryCooldownDelay",
  };
  for (const [key, id] of Object.entries(mappings)) {
    if (replayRequest[key] !== undefined) setInputValue(id, String(replayRequest[key]));
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
    date_in_filename: "dateInFilename",
    enable_thinking: "enableThinking",
    proofread_enable_thinking: "proofreadEnableThinking",
    summary_enable_thinking: "summaryEnableThinking",
  };
  for (const [key, id] of Object.entries(booleans)) {
    const input = document.querySelector<HTMLInputElement>(`#${id}`);
    if (input && replayRequest[key] !== undefined) input.checked = Boolean(replayRequest[key]);
  }
  hydrateRuntimeControls();
  hydrateTaskControls();
  saveSettings();
}

function clearHistory(): void {
  if (isWorkerRunning || cacheBusy) {
    setState("任务运行中，不能清空历史");
    return;
  }
  if (!taskHistory.length) {
    setState("没有可清空的任务历史");
    return;
  }
  if (!window.confirm(`确定清空本机保存的全部 ${taskHistory.length} 条任务历史吗？\n\n不会删除任何输出文件。`)) return;
  taskHistory = [];
  persistTaskHistory();
  renderHistory();
  setState("全部任务历史已清空");
}

async function manageRuntime(action: "status" | "install" | "remove"): Promise<void> {
  if (!hasTauriRuntime()) {
    setOutput(tauriRuntimeHint());
    return;
  }
  await ensureWorkerLogListener();
  setWorkerRunning(true);
  setState(action === "status" ? "读取托管环境..." : action === "install" ? "安装/修复托管环境..." : "卸载托管环境...");
  setOutput(action === "install" ? "正在安装/修复托管环境...\n" : "");
  try {
    const result = await invoke<string>("manage_runtime", { action });
    if (currentOutput().trim()) {
      appendOutput(`\n${result}`);
    } else {
      setOutput(result);
    }
    const managedModel = managedAsrModelPath(result);
    if (managedModel && !inputValue("asrModel").trim()) {
      setInputValue("asrModel", managedModel);
      saveSettings();
    }
    if (action === "remove") {
      setState("托管环境已卸载");
    } else if (result.includes("[MISSING]") || result.includes("状态：需要修复")) {
      setState("托管环境需要修复");
    } else {
      setState("托管环境就绪");
    }
  } catch (error) {
    const message = errorMessage(error);
    if (currentOutput().trim()) {
      appendOutput(`\n${message}`);
    } else {
      setOutput(message);
    }
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
