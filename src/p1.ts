export type TaskHistoryStatus = "running" | "completed" | "failed" | "cancelled" | "interrupted";

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
  task: string;
  status: string;
  outputs: string[];
  output_dir: string;
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
  return settings;
}

export function runtimeSelectionPayload(backend: "managed" | "conda", condaEnv: string, condaBin: string) {
  return {
    runtime_backend: backend,
    conda_env: backend === "conda" ? condaEnv : "",
    conda_bin: backend === "conda" ? condaBin : "",
  };
}

const historyKey = "local-note-studio.task-history.v1";
const maxEntries = 100;
const maxLogChars = 200_000;

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

export function progressFromLine(text: string): ProgressEvent | null {
  return structuredLine<ProgressEvent>(text, "PROGRESS_JSON:");
}

function sanitizeRequest(request: Record<string, unknown>): Record<string, unknown> {
  const copy = { ...request };
  for (const key of ["api_key", "cookies", "browser_profile"]) delete copy[key];
  return copy;
}
