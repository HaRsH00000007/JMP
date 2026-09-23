// Typed client for the JMP API (/api/v1). All requests go through `request`, which attaches the optional
// bearer token (Settings page) and turns the API's error envelope into an ApiError.

export const API_BASE = "/api/v1";
const TOKEN_KEY = "jmp.apiToken";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — token lives for this page only */
  }
}

export interface ErrorDetail {
  field?: string;
  issue?: string;
  row?: number;
  route_id?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

function headers(extra?: HeadersInit): HeadersInit {
  const token = getToken();
  return { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(extra ?? {}) };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(API_BASE + path, { ...init, headers: headers(init.headers) });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const body = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const err = body?.error ?? {};
    throw new ApiError(res.status, err.code ?? "HTTP_ERROR", err.message ?? res.statusText, err.details);
  }
  return body as T;
}

export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(API_BASE + path, { headers: headers() });
  if (!res.ok) throw new ApiError(res.status, "DOWNLOAD_FAILED", `Download failed (${res.status})`);
  const blob = await res.blob();
  const disp = res.headers.get("content-disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disp);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export async function openPdf(path: string): Promise<void> {
  const res = await fetch(API_BASE + path, { headers: headers() });
  if (!res.ok) throw new ApiError(res.status, "PREVIEW_FAILED", `Preview failed (${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// ------------------------------------------------------------------------------------------ types
export type JobStatus = "queued" | "processing" | "completed" | "failed" | "cancelled";

export interface JourneyRequest {
  start_location: string;
  stops: string[];
  end_location: string;
  vehicle_type?: "2W" | "4W";
  travel_date?: string;
  depart_time?: string;
  manager_name?: string;
  emergency_contact?: string;
  nearest_hospital?: string;
  nearest_police?: string;
}

export interface Job {
  job_id: string;
  kind: string;
  status: JobStatus;
  stage: string;
  attempt: number;
  journey_id: string;
  journey_code: string | null;
  route: string[];
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  document_id: string | null;
  bulk_job_id: string | null;
  error: { code: string; message: string } | null;
}

export interface Page<T> {
  total: number;
  page: number;
  size: number;
  items: T[];
}

export interface DocumentSummary {
  document_id: string;
  document_code: string;
  journey_id: string;
  route_name: string | null;
  region: string | null;
  risk_level: string | null;
  decision: string | null;
  journey_score: number | null;
  page_count: number | null;
  pdf_bytes: number | null;
  narrative_source: string;
  model: string;
  created_at: string;
  demo_data: boolean;
}

export interface DocumentDetail extends DocumentSummary {
  versions: Record<string, string>;
  providers: Record<string, string>;
  pdf_sha256: string;
  usage: {
    calls: number;
    input_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
    estimated_cost_inr: number;
  };
}

export interface CsvValidation {
  upload_id: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  columns_detected: string[];
  errors: { row: number; route_id: string; field: string; issue: string }[];
  preview: { row: number; route_id: string; start: string; stops: string[]; end: string; valid: boolean }[];
}

export interface BulkJob {
  job_id: string;
  status: string;
  llm_mode: string;
  filename: string;
  total: number;
  valid: number;
  invalid: number;
  processed: number;
  successful: number;
  failed: number;
  percentage: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number | null;
  eta_seconds: number | null;
  error: string | null;
  downloads: { zip: string | null; manifest_csv: string | null; manifest_xlsx: string | null; summary_json: string | null };
  usage: { input_tokens: number; cache_read_input_tokens: number; output_tokens: number; estimated_cost_usd: number; estimated_cost_inr: number };
}

export interface BulkItem {
  row_number: number;
  route_id: string;
  status: string;
  document_id: string | null;
  job_id: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface AppSettings {
  app_version: string;
  template_version: string;
  hazard_library_version: string;
  prompt_version: string;
  schema_version: string;
  scoring_version: string;
  rules_version: string;
  providers: Record<string, string>;
  llm: {
    provider: string;
    model: string;
    effort: string;
    cache_ttl: string;
    max_attempts: number;
    concurrency: number;
    bulk_mode: string;
    fallbacks_enabled: boolean;
    api_key_configured: boolean;
  };
  report: { hazard_pointer_count: number; max_stops: number; display_band_method: string; hazard_display_text: string; short_controls_approved: boolean };
  bulk: { max_rows: number; max_bytes: number };
  execution: string;
  storage: string;
  usd_to_inr: number;
  auth_required: boolean;
}

export interface UsageSummary {
  documents: number;
  by_model: {
    model: string;
    calls: number;
    input_tokens: number;
    cache_creation_input_tokens: number;
    cache_read_input_tokens: number;
    output_tokens: number;
    cache_hit_ratio: number;
    estimated_cost_usd: number;
    estimated_cost_inr: number;
    avg_latency_ms: number;
  }[];
  totals: { estimated_cost_usd: number; estimated_cost_inr: number };
  actual_cost_per_jmp_usd: number;
  actual_cost_per_100_jmp_usd: number;
  actual_cost_per_1000_jmp_usd: number;
  actual_cost_per_jmp_inr: number;
}

export interface CostProjection {
  model: string;
  tier: string;
  cache_ttl: string;
  usd: { per_jmp: number; per_100: number; per_1000: number };
  inr: { per_jmp: number; per_100: number; per_1000: number };
}

export interface Hazard {
  code: string;
  sr_no: number;
  name: string;
  severity: number;
  probability: string;
  rpn: string;
  severity_band: string;
  matrix_zone: string;
  controls_2w: string[];
  controls_4w: string[];
  detection: { kind?: string };
}

// ------------------------------------------------------------------------------------------ calls
export const api = {
  health: () => request<{ status: string; version: string }>("/health"),
  generate: (body: JourneyRequest) =>
    request<{ job_id: string; journey_id: string; status: string }>("/journeys/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  jobs: (params: { status?: string; kind?: string; page?: number; size?: number } = {}) =>
    request<Page<Job>>(`/jobs?${qs(params)}`),
  retryJob: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  documents: (params: { q?: string; risk?: string; region?: string; from?: string; to?: string; page?: number; size?: number; bulk_job_id?: string } = {}) =>
    request<Page<DocumentSummary>>(`/documents?${qs(params)}`),
  document: (id: string) => request<DocumentDetail>(`/documents/${id}`),
  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  validateCsv: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<CsvValidation>("/bulk-jobs/validate", { method: "POST", body: fd });
  },
  createBulk: (uploadId: string, llmMode: string) => {
    const fd = new FormData();
    fd.append("upload_id", uploadId);
    fd.append("llm_mode", llmMode);
    return request<{ job_id: string; total_routes: number; status: string }>("/bulk-jobs", { method: "POST", body: fd });
  },
  bulkJobs: (page = 1) => request<Page<BulkJob>>(`/bulk-jobs?page=${page}&size=20`),
  bulkJob: (id: string) => request<BulkJob>(`/bulk-jobs/${id}`),
  bulkItems: (id: string, status?: string) => request<Page<BulkItem>>(`/bulk-jobs/${id}/items?${qs({ status, size: 500 })}`),
  retryFailed: (id: string) => request<BulkJob & { requeued: number }>(`/bulk-jobs/${id}/retry-failed`, { method: "POST" }),
  cancelBulk: (id: string) => request<BulkJob & { cancelled: number }>(`/bulk-jobs/${id}/cancel`, { method: "POST" }),
  settings: () => request<AppSettings>("/settings"),
  usage: () => request<UsageSummary>("/usage/summary"),
  costModel: () => request<{ configured_model: string; projections: CostProjection[] }>("/usage/cost-model"),
  hazards: () => request<{ version: string; hazards: Hazard[] }>("/hazards"),
};

function qs(params: Record<string, string | number | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") u.set(k, String(v));
  return u.toString();
}
