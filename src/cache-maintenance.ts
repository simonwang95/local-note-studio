export const cacheCategoryLabels = {
  source_evidence: "原文证据",
  asr_diagnostics: "ASR 诊断",
  success_checkpoints: "成功检查点",
  failed_recovery: "失败恢复材料",
  disposable_cache: "可清理缓存",
} as const;

export type CacheCategory = keyof typeof cacheCategoryLabels;
export type CachePolicy = { auto_enabled: boolean; retention_days: number; categories: CacheCategory[] };
export type CacheReference = { type?: string; id?: string; run_id?: string; status?: string; protects?: boolean };
export type CacheItem = {
  id: string;
  category: CacheCategory;
  path: string;
  size_bytes: number;
  created_at?: string;
  modified_at?: string;
  status: string;
  task?: string;
  run_id?: string;
  source_ref?: string;
  references: Array<string | CacheReference>;
  protected: boolean;
  protection_reasons: string[];
};
export type CacheDiagnostic = {
  run_id?: string;
  task?: string;
  source_ref?: string;
  status?: string;
  started_at?: string;
  finished_at?: string;
  duration_seconds?: number | null;
  stage_durations?: Record<string, unknown> | null;
  model_calls?: number | null;
  llm_calls?: number | null;
  asr_calls?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  reasoning_tokens?: number | null;
  retries?: Array<{ stage?: string; reason?: string; count?: number }> | null;
  cache_hits?: number | null;
  effective_config?: Record<string, unknown> | null;
};
export type CacheInventory = {
  items: CacheItem[];
  summary: {
    total_bytes: number;
    total_count: number;
    protected_bytes: number;
    protected_count: number;
    diagnostics_bytes?: number | null;
    diagnostics_count?: number | null;
    by_category?: Partial<Record<CacheCategory, { count?: number | null; size_bytes?: number | null; protected_count?: number | null }>>;
  };
  policy: CachePolicy;
  diagnostics: CacheDiagnostic[];
};
export type CachePreview = {
  preview_id: string;
  expires_at: string;
  candidates: CacheItem[];
  skipped: Array<CacheItem | Record<string, unknown>>;
  total_bytes: number;
  candidate_count: number;
  selection: { categories: CacheCategory[]; older_than_days: number };
};
export type CacheReferences = { entries: Array<{ id: string; status: string; request: Record<string, unknown>; outputs?: string[] }> };

// Send only the fields required to find task materials. Neither logs nor credentials
// belong in the Worker's persistent reference registry.
const referenceRequestFields = ["task", "run_id", "source", "output_dir", "output_filename", "incognito_mode", "collection_id", "collection_type", "collection_mid", "ocr_resume"];

export function cacheReferences(
  queue: Array<{ id: string; status: string; request: Record<string, unknown> }>,
  history: Array<{ id: string; status: string; request: Record<string, unknown>; outputs: string[] }>,
): CacheReferences {
  return {
    entries: [...queue.map((entry) => ({ ...entry, id: `queue:${entry.id}` })), ...history.map((entry) => ({ ...entry, id: `history:${entry.id}` }))]
      .map((entry) => ({
        id: entry.id,
        status: entry.status,
        request: Object.fromEntries(referenceRequestFields.flatMap((key) => {
          const value = entry.request[key];
          return typeof value === "string" || typeof value === "boolean" || typeof value === "number" ? [[key, value]] : [];
        })),
        ...("outputs" in entry && Array.isArray(entry.outputs) ? { outputs: entry.outputs.filter((path): path is string => typeof path === "string") } : {}),
      })),
  };
}

export function cacheBytes(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "未知";
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(1)} ${units[unit]}`;
}

export function cacheMetric(value: unknown): string {
  if (value === undefined || value === null) return "未知";
  if (typeof value === "object") return JSON.stringify(value, (_key, item) => item === null ? "未知" : item);
  return String(value);
}

export function filterCacheItems(items: CacheItem[], category: string, source: string): CacheItem[] {
  return items.filter((item) => (category === "all" || item.category === category)
    && cacheSourceMatches([item.run_id, item.task, item.source_ref, item.path, item.status, ...(item.references || []).flatMap((reference) => typeof reference === "string" ? [reference] : [reference.id, reference.run_id, reference.status])], source));
}

export function cacheSourceMatches(values: unknown[], source: string): boolean {
  const needle = source.trim().toLocaleLowerCase();
  if (!needle) return true;
  // Worker source references for Bilibili use the BV identifier rather than a
  // full URL. Match either form in the local source filter.
  const bv = needle.match(/bv[0-9a-z]+/)?.[0];
  return values.some((value) => {
    const text = String(value || "").toLocaleLowerCase();
    return text.includes(needle) || Boolean(bv && text.includes(bv));
  });
}

export function cacheRetentionDays(value: string): number {
  const days = Number(value);
  if (!value.trim() || !Number.isInteger(days) || days < 0 || days > 3650) throw new Error("保留期限应为 0–3650 的整数天数");
  return days;
}

export function cachePreviewConfirmation(preview: CachePreview): string {
  return `确定清理这次预览中的 ${preview.candidate_count} 项（${cacheBytes(preview.total_bytes)}）吗？\n\n类别：${preview.selection.categories.map((category) => cacheCategoryLabels[category] || category).join("、")}\n保留最近 ${preview.selection.older_than_days} 天的材料\n\n${preview.candidates.map((item) => `${item.path}（${cacheBytes(item.size_bytes)}）`).join("\n")}\n\n清理前 Worker 会再次核对引用和文件状态；预览失效时需重新预览。`;
}
