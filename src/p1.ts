export type TaskHistoryStatus = "running" | "completed" | "failed" | "cancelled" | "interrupted";
export type QueueStatus = "waiting" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

export type DesktopProfile = {
  id: string;
  name: string;
  credentialRef: string;
  settings: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

export type DesktopProfiles = { activeId: string; profiles: DesktopProfile[] };

export type QueueEntry = {
  id: string;
  request: Record<string, unknown>;
  profileId: string;
  status: QueueStatus;
  addedAt: string;
  startedAt?: string;
  endedAt?: string;
  error?: string;
  historyId?: string;
};

export type TaskHistoryEntry = {
  id: string;
  task: string;
  request: Record<string, unknown>;
  status: TaskHistoryStatus;
  startedAt: string;
  endedAt?: string;
  log: string;
  outputs: string[];
  error?: string;
  retryOf?: string;
};

export type TaskResult = {
  schema_version?: string;
  run_id?: string;
  caller?: string;
  task: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  source_ref?: string;
  outputs: string[];
  output_dir: string;
  counts?: {
    discovered: number;
    created: number;
    updated: number;
    skipped: number;
    failed: number;
  };
  deliveries?: Array<Record<string, unknown>>;
  manifest_path?: string;
  warnings?: string[];
  details?: Record<string, unknown>;
  error?: { error_code: string; message: string } | null;
  retryable?: boolean;
};

export type ProgressEvent = {
  phase: string;
  current: number;
  total: number;
  label?: string;
  backend?: string;
  resumed?: boolean;
};

export function migrateRuntimePreference(value: unknown): Record<string, unknown> {
  const settings = value && typeof value === "object" && !Array.isArray(value) ? { ...(value as Record<string, unknown>) } : {};
  if (!("runtimePreferenceConfirmed" in settings)) {
    settings.runtimeBackend = "managed";
    settings.runtimePreferenceConfirmed = true;
  }
  if (settings.cookies === "./bili_cookies.txt") settings.cookies = "";
  const usesLegacyLlmDefaults =
    settings.apiBase === "http://127.0.0.1:1234/v1" &&
    settings.apiKey === "lm-studio" &&
    settings.model === "qwen3.6-35b-a3b-nvfp4";
  if (usesLegacyLlmDefaults) {
    settings.apiBase = "http://127.0.0.1:8000/v1";
    settings.apiKey = "mtplx-local";
    settings.model = "mtplx-qwen38-27b-optimized-speed";
  }
  return settings;
}

export function runtimeSelectionPayload(backend: "managed" | "conda", condaEnv: string, condaBin: string) {
  return {
    runtime_backend: backend,
    conda_env: backend === "conda" ? condaEnv : "",
    conda_bin: backend === "conda" ? condaBin : "",
  };
}

const persistentConfigurationKeys = [
  "runtime_backend",
  "conda_env",
  "conda_bin",
  "python_bin",
  "api_base",
  "api_key",
  "model",
  "asr_model",
  "cookies",
  "browser_profile",
  "desktop_profile_id",
];

export function historyReplayRequest(request: Record<string, unknown>): Record<string, unknown> {
  const replay = { ...request };
  for (const key of persistentConfigurationKeys) delete replay[key];
  return replay;
}

const historyKey = "local-note-studio.task-history.v1";
const profilesKey = "local-note-studio.desktop-profiles.v1";
const credentialsKey = "local-note-studio.desktop-credentials.v1";
const queueKey = "local-note-studio.task-queue.v1";
const maxEntries = 100;
const maxLogChars = 200_000;
const recentValueKeys = {
  outputDir: "local-note-studio.recent-output-dirs.v1",
  source: "local-note-studio.recent-sources.v1",
} as const;
const maxRecentValues = 12;

const credentialFields = ["apiKey", "cookies", "chromeProfile"] as const;

export function loadDesktopProfiles(legacySettings: Record<string, unknown>): DesktopProfiles {
  try {
    const raw = localStorage.getItem(profilesKey);
    const parsed = raw ? JSON.parse(raw) as Partial<DesktopProfiles> : null;
    if (parsed && Array.isArray(parsed.profiles) && parsed.profiles.length) {
      const profiles = parsed.profiles.map(normalizeProfile).filter((profile): profile is DesktopProfile => Boolean(profile));
      if (profiles.length) {
        const activeId = profiles.some((item) => item.id === parsed.activeId) ? parsed.activeId! : profiles[0].id;
        const state = { activeId, profiles };
        saveDesktopProfiles(state);
        return state;
      }
    }
    const migrated = migrateRuntimePreference(legacySettings);
    const secrets = pickCredentials(migrated);
    const id = `profile-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const credentialRef = `credential-${id}`;
    saveCredentials(credentialRef, secrets);
    const profile: DesktopProfile = {
      id, name: "默认", credentialRef, settings: stripCredentials(migrated),
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    };
    const state = { activeId: id, profiles: [profile] };
    saveDesktopProfiles(state);
    localStorage.removeItem("local-note-studio.settings.v1");
    return state;
  } catch {
    const id = `profile-${Date.now()}`;
    const profile: DesktopProfile = {
      id, name: "默认", credentialRef: `credential-${id}`, settings: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    };
    const state = { activeId: id, profiles: [profile] };
    saveDesktopProfiles(state);
    return state;
  }
}

export function saveDesktopProfiles(state: DesktopProfiles): void {
  const safe = { activeId: state.activeId, profiles: state.profiles.map((profile) => ({ ...profile, settings: stripCredentials(profile.settings) })) };
  localStorage.setItem(profilesKey, JSON.stringify(safe));
}

export function loadCredentials(ref: string): Record<string, string> {
  try {
    const parsed = JSON.parse(localStorage.getItem(credentialsKey) || "{}");
    const value = parsed && typeof parsed === "object" ? parsed[ref] : null;
    return value && typeof value === "object" ? pickCredentials(value) : {};
  } catch { return {}; }
}

export function hasCredentialReference(ref: string): boolean {
  try {
    const parsed = JSON.parse(localStorage.getItem(credentialsKey) || "{}");
    return Boolean(ref && parsed && typeof parsed === "object" && Object.prototype.hasOwnProperty.call(parsed, ref));
  } catch { return false; }
}

export function saveCredentials(ref: string, value: Record<string, unknown>): void {
  try {
    const parsed = JSON.parse(localStorage.getItem(credentialsKey) || "{}");
    parsed[ref] = pickCredentials(value);
    localStorage.setItem(credentialsKey, JSON.stringify(parsed));
  } catch { localStorage.setItem(credentialsKey, JSON.stringify({ [ref]: pickCredentials(value) })); }
}

export function deleteCredentials(ref: string): void {
  try {
    const parsed = JSON.parse(localStorage.getItem(credentialsKey) || "{}");
    if (parsed && typeof parsed === "object") {
      delete parsed[ref];
      localStorage.setItem(credentialsKey, JSON.stringify(parsed));
    }
  } catch { /* leave malformed local credential storage for manual recovery */ }
}

export function createDesktopProfile(state: DesktopProfiles, name: string, source?: DesktopProfile): DesktopProfiles {
  const id = `profile-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const credentialRef = `credential-${id}`;
  if (source) saveCredentials(credentialRef, loadCredentials(source.credentialRef));
  const now = new Date().toISOString();
  const profile: DesktopProfile = {
    id, name: name.trim(), credentialRef,
    settings: source ? { ...source.settings } : {}, createdAt: now, updatedAt: now,
  };
  const next = { activeId: id, profiles: [...state.profiles, profile] };
  saveDesktopProfiles(next);
  return next;
}

export function renameDesktopProfile(state: DesktopProfiles, id: string, name: string): DesktopProfiles {
  const next = { ...state, profiles: state.profiles.map((profile) => profile.id === id ? { ...profile, name: name.trim(), updatedAt: new Date().toISOString() } : profile) };
  saveDesktopProfiles(next);
  return next;
}

export function deleteDesktopProfile(state: DesktopProfiles, id: string): DesktopProfiles {
  if (state.profiles.length < 2) throw new Error("至少保留一个配置档案");
  const profiles = state.profiles.filter((profile) => profile.id !== id);
  const next = { activeId: state.activeId === id ? profiles[0].id : state.activeId, profiles };
  saveDesktopProfiles(next);
  return next;
}

export function exportDesktopProfile(profile: DesktopProfile): string {
  return JSON.stringify({ schema_version: 1, name: profile.name, settings: stripCredentials(profile.settings) }, null, 2);
}

export function importedDesktopProfile(text: string): { name: string; settings: Record<string, unknown> } {
  const value = JSON.parse(text);
  if (!value || typeof value !== "object" || !value.settings || typeof value.settings !== "object") throw new Error("配置档案文件格式无效");
  return { name: typeof value.name === "string" ? value.name : "导入配置", settings: stripCredentials(value.settings) };
}

export function saveQueue(entries: QueueEntry[]): void {
  localStorage.setItem(queueKey, JSON.stringify(entries.map((entry) => ({ ...entry, request: sanitizeRequest(entry.request) }))));
}

export function loadQueue(): QueueEntry[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(queueKey) || "[]");
    if (!Array.isArray(parsed)) return [];
    let changed = false;
    const entries: QueueEntry[] = parsed.filter((item) => item && typeof item === "object").map((item) => {
      const allowed: QueueStatus[] = ["waiting", "running", "completed", "failed", "cancelled", "interrupted"];
      const status = allowed.includes(item.status) ? item.status as QueueStatus : "failed";
      if (status === "running") changed = true;
      return {
        id: typeof item.id === "string" ? item.id : `queue-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        request: item.request && typeof item.request === "object" ? sanitizeRequest(item.request) : {},
        profileId: typeof item.profileId === "string" ? item.profileId : "",
        status: status === "running" ? "interrupted" : status,
        addedAt: typeof item.addedAt === "string" ? item.addedAt : new Date().toISOString(),
        startedAt: typeof item.startedAt === "string" ? item.startedAt : undefined,
        endedAt: typeof item.endedAt === "string" ? item.endedAt : undefined,
        error: status === "running" ? "应用退出时任务正在运行；请确认后继续或移除。" : typeof item.error === "string" ? item.error : undefined,
        historyId: typeof item.historyId === "string" ? item.historyId : undefined,
      };
    });
    if (changed) saveQueue(entries);
    return entries;
  } catch { return []; }
}

export function moveQueueEntry(entries: QueueEntry[], id: string, direction: -1 | 1): QueueEntry[] {
  const index = entries.findIndex((item) => item.id === id && item.status === "waiting");
  const waitingIndexes = entries.map((item, position) => item.status === "waiting" ? position : -1).filter((position) => position >= 0);
  const slot = waitingIndexes.indexOf(index);
  const targetSlot = slot + direction;
  if (index < 0 || slot < 0 || targetSlot < 0 || targetSlot >= waitingIndexes.length) return entries;
  const next = [...entries];
  const target = waitingIndexes[targetSlot];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function queueDuplicate(entries: QueueEntry[], request: Record<string, unknown>): QueueEntry | undefined {
  const source = String(request.source || request.collection_id || "").trim();
  const output = String(request.output_dir || "").trim();
  if (!source || !output) return undefined;
  const filename = String(request.output_filename || "").trim().toLocaleLowerCase();
  return entries.find((entry) => {
    if (!["waiting", "running", "interrupted"].includes(entry.status)) return false;
    const oldSource = String(entry.request.source || entry.request.collection_id || "").trim();
    const oldOutput = String(entry.request.output_dir || "").trim();
    const oldFilename = String(entry.request.output_filename || "").trim().toLocaleLowerCase();
    return (oldSource === source && oldOutput === output)
      || Boolean(filename && oldFilename && oldOutput === output && oldFilename === filename);
  });
}

function normalizeProfile(value: unknown): DesktopProfile | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (typeof item.id !== "string" || !item.id || typeof item.name !== "string") return null;
  return {
    id: item.id, name: item.name, credentialRef: typeof item.credentialRef === "string" ? item.credentialRef : `credential-${item.id}`,
    settings: item.settings && typeof item.settings === "object" && !Array.isArray(item.settings) ? stripCredentials(item.settings as Record<string, unknown>) : {},
    createdAt: typeof item.createdAt === "string" ? item.createdAt : new Date().toISOString(),
    updatedAt: typeof item.updatedAt === "string" ? item.updatedAt : new Date().toISOString(),
  };
}

function stripCredentials(value: Record<string, unknown>): Record<string, unknown> {
  const safe = { ...value };
  for (const key of credentialFields) delete safe[key];
  return safe;
}

function pickCredentials(value: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(credentialFields.map((key) => [key, typeof value[key] === "string" ? value[key] as string : ""]));
}

export type RecentValueKind = keyof typeof recentValueKeys;

export function createHistoryEntry(task: string, request: Record<string, unknown>, retryOf?: string): TaskHistoryEntry {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    task,
    request: sanitizeRequest(request),
    status: "running",
    startedAt: new Date().toISOString(),
    log: "",
    outputs: [],
    retryOf,
  };
}

export function loadTaskHistory(): TaskHistoryEntry[] {
  try {
    const raw = localStorage.getItem(historyKey);
    const parsed = raw ? JSON.parse(raw) : [];
    const entries = Array.isArray(parsed)
      ? parsed.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")).map(normalizeHistoryEntry)
      : [];
    let changed = raw !== JSON.stringify(entries);
    for (const entry of entries) {
      if (entry.status === "running") {
        entry.status = "interrupted";
        entry.endedAt = new Date().toISOString();
        entry.error = "应用退出或进程中断，可从历史记录重新运行。";
        changed = true;
      }
    }
    if (changed) saveTaskHistory(entries);
    return entries;
  } catch {
    return [];
  }
}

function normalizeHistoryEntry(item: Record<string, unknown>): TaskHistoryEntry {
  const allowedStatuses: TaskHistoryStatus[] = ["running", "completed", "failed", "cancelled", "interrupted"];
  const status = allowedStatuses.includes(item.status as TaskHistoryStatus) ? (item.status as TaskHistoryStatus) : "failed";
  return {
    id: typeof item.id === "string" && item.id ? item.id : `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    task: typeof item.task === "string" ? item.task : "unknown",
    request: item.request && typeof item.request === "object" && !Array.isArray(item.request) ? (item.request as Record<string, unknown>) : {},
    status,
    startedAt: typeof item.startedAt === "string" ? item.startedAt : new Date().toISOString(),
    endedAt: typeof item.endedAt === "string" ? item.endedAt : undefined,
    log: typeof item.log === "string" ? item.log : "",
    outputs: Array.isArray(item.outputs) ? item.outputs.filter((value): value is string => typeof value === "string") : [],
    error: typeof item.error === "string" ? item.error : undefined,
    retryOf: typeof item.retryOf === "string" ? item.retryOf : undefined,
  };
}

export function saveTaskHistory(entries: TaskHistoryEntry[]): void {
  const bounded = entries.slice(0, maxEntries).map((entry) => ({
    ...entry,
    log: (entry.log || "").slice(-maxLogChars),
    outputs: Array.isArray(entry.outputs) ? entry.outputs : [],
  }));
  localStorage.setItem(historyKey, JSON.stringify(bounded));
}

export function upsertHistoryEntry(entries: TaskHistoryEntry[], entry: TaskHistoryEntry): TaskHistoryEntry[] {
  return [entry, ...entries.filter((item) => item.id !== entry.id)].slice(0, maxEntries);
}

export function removeHistoryEntry(entries: TaskHistoryEntry[], id: string): TaskHistoryEntry[] {
  return entries.filter((entry) => entry.id !== id);
}

export function filterTaskHistory(entries: TaskHistoryEntry[], status: TaskHistoryStatus | "all"): TaskHistoryEntry[] {
  return status === "all" ? entries : entries.filter((entry) => entry.status === status);
}

export function normalizeRecentValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function loadRecentValues(kind: RecentValueKind): string[] {
  try {
    const raw = localStorage.getItem(recentValueKeys[kind]);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    const values = dedupeRecentValues(parsed.map(normalizeRecentValue).filter(Boolean));
    if (raw !== JSON.stringify(values)) saveRecentValues(kind, values);
    return values;
  } catch {
    return [];
  }
}

export function saveRecentValues(kind: RecentValueKind, values: string[]): void {
  localStorage.setItem(recentValueKeys[kind], JSON.stringify(dedupeRecentValues(values)));
}

export function rememberRecentValue(kind: RecentValueKind, value: unknown): string[] {
  const normalized = normalizeRecentValue(value);
  const current = loadRecentValues(kind);
  if (!normalized) return current;
  const next = dedupeRecentValues([normalized, ...current]);
  saveRecentValues(kind, next);
  return next;
}

export function pathDialogDefault(kind: RecentValueKind, currentValue: unknown): string {
  const candidates = [normalizeRecentValue(currentValue), ...loadRecentValues(kind)];
  return candidates.find((value) => value && (kind === "outputDir" || !/^[a-z][a-z0-9+.-]*:\/\//i.test(value))) || "";
}

export function removeRecentValue(kind: RecentValueKind, value: unknown): string[] {
  const normalized = normalizeRecentValue(value);
  const next = normalized ? loadRecentValues(kind).filter((item) => item !== normalized) : loadRecentValues(kind);
  saveRecentValues(kind, next);
  return next;
}

export function clearRecentValues(kind: RecentValueKind): void {
  localStorage.removeItem(recentValueKeys[kind]);
}

function dedupeRecentValues(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const normalized = normalizeRecentValue(value);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
    if (result.length >= maxRecentValues) break;
  }
  return result;
}

export function structuredLine<T>(text: string, prefix: string): T | null {
  const lines = text.split("\n").filter((line) => line.startsWith(prefix));
  const line = lines.at(-1);
  if (!line) return null;
  try {
    return JSON.parse(line.slice(prefix.length)) as T;
  } catch {
    return null;
  }
}

export function taskResultFromLog(text: string): TaskResult | null {
  return structuredLine<TaskResult>(text, "TASK_RESULT_JSON:");
}

export function noChangesStatusLabel(result: TaskResult | null): string {
  const skipped = Number(result?.counts?.skipped || 0);
  const existing = Math.min(skipped, Number(result?.details?.existing_complete || 0));
  if (skipped > 0 && existing === skipped) return `无需更新（已有 ${skipped} 项完整内容）`;
  if (skipped > 0) return `无需更新（本次跳过 ${skipped} 项）`;
  return "无需更新（没有新增内容）";
}

export function progressFromLine(text: string): ProgressEvent | null {
  return structuredLine<ProgressEvent>(text, "PROGRESS_JSON:");
}

function sanitizeRequest(request: Record<string, unknown>): Record<string, unknown> {
  const copy = { ...request };
  for (const key of ["api_key", "cookies", "browser_profile"]) delete copy[key];
  return copy;
}
