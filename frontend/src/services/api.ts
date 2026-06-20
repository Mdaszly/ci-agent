import createClient from "openapi-fetch";

import type { components, paths } from "./openapi";

const IS_DEV = import.meta.env.MODE === "development";
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? (IS_DEV ? "" : "http://localhost:8000");

// ========== 认证令牌管理 ==========

const ACCESS_TOKEN_KEY = "ci_agent_access_token";
const REFRESH_TOKEN_KEY = "ci_agent_refresh_token";

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}

/**
 * 获取 API Key（用于服务间调用场景，优先级低于 JWT）
 */
export function getApiKey(): string | null {
  return localStorage.getItem("ci_agent_api_key");
}

export function setApiKey(key: string): void {
  localStorage.setItem("ci_agent_api_key", key);
}

// ========== 认证请求头注入 ==========

function buildAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  } else {
    const apiKey = getApiKey();
    if (apiKey) {
      headers["X-API-Key"] = apiKey;
    }
  }
  return headers;
}

// ========== 401 自动刷新机制 ==========

let refreshPromise: Promise<boolean> | null = null;

async function tryRefreshToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  if (!getRefreshToken()) return false;
  refreshPromise = (async () => {
    try {
      const refresh = getRefreshToken()!;
      const response = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) {
        clearTokens();
        return false;
      }
      const tokens = await response.json();
      setTokens(tokens.access_token, tokens.refresh_token);
      return true;
    } catch {
      clearTokens();
      return false;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

/**
 * 带认证头的 fetch 封装：自动注入 Authorization / X-API-Key，
 * 收到 401 时尝试用 refresh token 刷新并重试一次。
 */
export async function authedFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  for (const [key, value] of Object.entries(buildAuthHeaders())) {
    headers.set(key, value);
  }

  const response = await fetch(input, { ...init, headers });

  if (response.status === 401 && getRefreshToken()) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      const retryHeaders = new Headers(init.headers);
      for (const [key, value] of Object.entries(buildAuthHeaders())) {
        retryHeaders.set(key, value);
      }
      return fetch(input, { ...init, headers: retryHeaders });
    }
  }

  return response;
}

const client = createClient<paths>({
  baseUrl: API_BASE,
  headers: {
    ...(IS_DEV
      ? {
          // 开发环境：放宽请求头限制
          "X-Development-Mode": "true",
          "X-Loose-Validation": "true",
        }
      : {}),
  },
});

// 中间件：每次请求注入认证头
client.use({
  async onRequest({ request }) {
    const authHeaders = buildAuthHeaders();
    for (const [key, value] of Object.entries(authHeaders)) {
      request.headers.set(key, value);
    }
    return request;
  },
});

export type ApiSchema<Name extends keyof components["schemas"]> =
  components["schemas"][Name];
export type TaskStatus = ApiSchema<"TaskStatus">;
export type TaskCreateRequest = ApiSchema<"TaskCreateRequest">;
export type TaskEvent = ApiSchema<"TaskEvent">;
export type Evidence = ApiSchema<"Evidence">;
export type CoverageGateResult = ApiSchema<"CoverageGateResult">;
export type DecisionAction = ApiSchema<"DecisionAction">;
export type DecisionPack = ApiSchema<"DecisionPack">;
export type ReviewScore = ApiSchema<"ReviewScore">;
export type BudgetUsage = ApiSchema<"BudgetUsage">;
export type TaskRecord = ApiSchema<"TaskRecord"> & {
  observable_id?: string;
  last_checkpoint_observable_id?: string;
};
export type TaskMetrics = ApiSchema<"TaskMetrics">;
export type InterventionRequest = ApiSchema<"InterventionRequest">;
export type WorkflowCheckpoint = {
  checkpoint_id: string;
  observable_checkpoint_id?: string;
  run_id: string;
  task_id: string;
  kind: string;
  stage?: string | null;
  thread_id?: string | null;
  request_id?: string | null;
  status: string;
  created_at: string;
  payload: Record<string, unknown>;
};
export interface ReflowControls {
  enable_memory_recall?: boolean;
  max_repair_rounds?: number;
  auto_publish_final?: boolean;
}

export type RichTaskCreateRequest = TaskCreateRequest & {
  reflow_controls?: ReflowControls;
};

export type RichTaskRecord = TaskRecord & {
  decision_history?: Array<{
    pack_id?: string;
    version?: number;
    parent_pack_id?: string | null;
    superseded_by?: string | null;
    status?: string;
    summary?: string;
    created_at?: string;
  }>;
  memory_state?: {
    task_id?: string;
    stage?: string;
    iteration?: number;
    max_iterations?: number;
    source_refs?: string[];
    risk_level?: string;
    current_pack_version?: number;
    last_reviewer_status?: string;
    last_recall_count?: number;
    last_recall_summary?: string;
    retry_reason?: string;
  } | null;
  memory_items?: Array<{
    id?: string;
    task_id?: string;
    pack_id?: string;
    version?: number;
    chunk_type?: string;
    text?: string;
    source_refs?: string[];
    similarity?: number;
  }>;
  review_history?: Array<{
    score?: number;
    hallucination_risk?: string;
    notes?: string[];
    created_at?: string;
  }>;
};

export interface DecisionPackResponse {
  decision_pack: DecisionPack;
  review?: ReviewScore | null;
}

function stringifyDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const items = detail
      .map(
        (item) =>
          stringifyDetail(item) ??
          (typeof item === "object" && item !== null
            ? JSON.stringify(item)
            : String(item)),
      )
      .filter(Boolean);
    return items.length > 0 ? items.join("；") : null;
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.msg === "string") return record.msg;
    if (typeof record.message === "string") return record.message;
    if ("loc" in record || "type" in record) {
      const loc = Array.isArray(record.loc) ? record.loc.join(".") : undefined;
      const message =
        typeof record.msg === "string"
          ? record.msg
          : typeof record.message === "string"
            ? record.message
            : null;
      return [loc, message].filter(Boolean).join("：") || null;
    }
    if ("detail" in record) return stringifyDetail(record.detail);
  }
  return null;
}

function getErrorMessage(error: unknown): string {
  if (typeof error === "string") {
    return error;
  }

  if (error && typeof error === "object") {
    const record = error as Record<string, unknown>;
    const candidates = [
      record.detail,
      record.message,
      record.error,
      record.errors,
      record.response && typeof record.response === "object"
        ? ((record.response as Record<string, unknown>).data ??
          (record.response as Record<string, unknown>).detail)
        : undefined,
    ];

    for (const candidate of candidates) {
      const message = stringifyDetail(candidate);
      if (message) {
        return message;
      }
    }
  }

  return "请求失败";
}

export async function createTask(
  payload: RichTaskCreateRequest,
): Promise<RichTaskRecord> {
  try {
    const { data, error } = await client.POST("/api/tasks", {
      body: payload,
    });
    if (error) {
      if (IS_DEV) {
        console.debug("[API] createTask error:", error);
      }
      throw new Error(getErrorMessage(error));
    }
    if (!data) {
      throw new Error("后端未返回任务数据");
    }
    return data;
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] createTask failed:", err);
    }
    throw err;
  }
}

export async function getTask(taskId: string): Promise<RichTaskRecord> {
  try {
    const { data, error } = await client.GET("/api/tasks/{task_id}", {
      params: { path: { task_id: taskId } },
    });
    if (error) {
      if (IS_DEV) {
        console.debug("[API] getTask error:", error);
      }
      throw new Error(getErrorMessage(error));
    }
    if (!data) {
      throw new Error("后端未返回任务数据");
    }
    return data;
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] getTask failed (development mode):", err);
      return {
        id: taskId,
        request: {} as TaskCreateRequest,
        status: "running",
        events: [],
        evidence: [],
        claims: [],
        conflicts: [],
        coverage: {
          passed: true,
          gap_queries: [],
          score: 1,
          covered_dimensions: [],
          missing_dimensions: [],
        },
        budget_usage: {
          estimated_sources: 0,
          estimated_tokens: 0,
          estimated_cost_usd: 0,
          within_budget: true,
        },
      } as RichTaskRecord;
    }
    throw err;
  }
}

export interface DecisionPackResponse {
  decision_pack: DecisionPack;
  review?: ReviewScore | null;
}

export async function getDecisionPack(
  taskId: string,
): Promise<DecisionPackResponse> {
  try {
    const { data, error } = await client.GET(
      "/api/tasks/{task_id}/decision-pack",
      {
        params: { path: { task_id: taskId } },
      },
    );
    if (error) {
      if (IS_DEV) {
        console.debug("[API] getDecisionPack error:", error);
      }
      throw new Error(getErrorMessage(error));
    }
    if (!data) {
      throw new Error("后端未返回决策包数据");
    }
    return data as DecisionPackResponse;
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] getDecisionPack failed:", err);
    }
    throw err;
  }
}

export interface SubscribeCallbacks {
  onEvent: (event: TaskEvent) => void;
  onComplete: (task: TaskRecord) => void;
  onError: (error: Error) => void;
}

export interface SubscribeOptions {
  sseTimeout?: number;
  pollingInterval?: number;
  forcePolling?: boolean;
}

export interface SubscribeResult {
  unsubscribe: () => void;
}

export function subscribeTaskEvents(
  taskId: string,
  callbacks: SubscribeCallbacks,
  options?: SubscribeOptions,
): SubscribeResult {
  const {
    sseTimeout = 10000,
    pollingInterval = 2000,
    forcePolling = IS_DEV,
  } = options ?? {};

  let abortController: AbortController | null = null;
  let pollingTimer: ReturnType<typeof setInterval> | null = null;
  let isClosed = false;

  const cleanup = () => {
    if (isClosed) return;
    isClosed = true;
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    if (pollingTimer) {
      clearInterval(pollingTimer);
      pollingTimer = null;
    }
  };

  const handleTaskStatus = (task: TaskRecord) => {
    if (task.status === "completed") {
      callbacks.onComplete(task);
      cleanup();
    } else if (task.status === "failed") {
      callbacks.onError(new Error("任务执行失败"));
      cleanup();
    }
  };

  let lastEventCount = 0;

  const startPolling = () => {
    if (IS_DEV) {
      console.debug("[SSE] Using polling mode (development)");
    }
    pollingTimer = setInterval(async () => {
      try {
        const task = await getTask(taskId);
        const events = task.events ?? [];

        if (events.length > lastEventCount) {
          const newEvents = events.slice(lastEventCount);
          newEvents.forEach((event) => callbacks.onEvent(event));
          lastEventCount = events.length;
        }

        handleTaskStatus(task);
      } catch (err) {
        if (IS_DEV) {
          console.debug("[Polling] Error (continuing):", err);
          return;
        }
        callbacks.onError(err instanceof Error ? err : new Error(String(err)));
        cleanup();
      }
    }, pollingInterval);
  };

  const startSSE = async () => {
    const url = `${API_BASE}/api/tasks/${taskId}/events/stream`;
    abortController = new AbortController();
    let buffer = "";

    const timeoutId = setTimeout(() => {
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      if (IS_DEV) {
        console.debug("[SSE] Timeout, falling back to polling");
      }
      startPolling();
    }, sseTimeout);

    try {
      const response = await authedFetch(url, {
        headers: { Accept: "text/event-stream" },
        signal: abortController.signal,
      });

      if (!response.ok || !response.body) {
        clearTimeout(timeoutId);
        if (IS_DEV) {
          console.debug("[SSE] Non-OK response, falling back to polling");
        }
        startPolling();
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      const pump = async (): Promise<void> => {
        const { done, value } = await reader.read();
        if (done) {
          clearTimeout(timeoutId);
          return;
        }

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const rawEvent of events) {
          const dataLine = rawEvent
            .split("\n")
            .find((line) => line.startsWith("data:"));
          if (!dataLine) continue;

          clearTimeout(timeoutId);
          try {
            const taskEvent: TaskEvent = JSON.parse(dataLine.slice(5).trim());
            callbacks.onEvent(taskEvent);
            if (
              taskEvent.status === "completed" ||
              taskEvent.status === "failed"
            ) {
              getTask(taskId)
                .then((task) => {
                  if (taskEvent.status === "completed") {
                    callbacks.onComplete(task);
                  } else {
                    callbacks.onError(
                      new Error(taskEvent.message || "任务执行失败"),
                    );
                  }
                  cleanup();
                })
                .catch((err) => {
                  if (IS_DEV) {
                    console.warn("[SSE] Error fetching complete task:", err);
                  }
                  callbacks.onError(
                    err instanceof Error ? err : new Error(String(err)),
                  );
                  cleanup();
                });
              return;
            }
          } catch (parseErr) {
            if (IS_DEV) {
              console.debug("[SSE] Parse error (continuing):", parseErr);
            }
          }
        }

        return pump();
      };

      await pump();
    } catch (err) {
      clearTimeout(timeoutId);
      if (IS_DEV) {
        console.debug("[SSE] Error, falling back to polling:", err);
      }
      startPolling();
    }
  };

  if (forcePolling || IS_DEV) {
    startPolling();
  } else {
    void startSSE();
  }

  return { unsubscribe: cleanup };
}

export async function cancelTask(
  taskId: string,
): Promise<{ status: string; task: TaskRecord }> {
  const response = await authedFetch(`${API_BASE}/api/tasks/${taskId}/cancel`, {
    method: "POST",
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "停止任务失败");
  }

  return response.json();
}

export interface InterventionResponse {
  status: string;
  task_id: string;
  intervention: InterventionRequest;
}

export async function createIntervention(
  taskId: string,
  payload: InterventionRequest,
): Promise<InterventionResponse> {
  try {
    const { data, error } = await client.POST(
      "/api/tasks/{task_id}/interventions",
      {
        params: { path: { task_id: taskId } },
        body: payload,
      },
    );
    if (error) {
      if (IS_DEV) {
        console.debug("[API] createIntervention error:", error);
      }
      throw new Error(getErrorMessage(error));
    }
    if (!data) {
      throw new Error("后端未返回干预响应");
    }
    return data as InterventionResponse;
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] createIntervention failed (development mode):", err);
      return {
        status: "recorded",
        task_id: taskId,
        intervention: payload,
      };
    }
    throw err;
  }
}

export interface UploadFileResult {
  success: boolean;
  filename: string;
  content_type: string;
  size: number;
  is_image: boolean;
  message: string;
}

export interface UploadFilesResult {
  success: boolean;
  success_count: number;
  total_count: number;
  files: UploadFileResult[];
}

export async function uploadFiles(files: FileList): Promise<UploadFilesResult> {
  try {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append("files", files[i]);
    }

    const response = await authedFetch(`${API_BASE}/api/uploads/files`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "文件上传失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] uploadFiles failed:", err);
    }
    throw err;
  }
}

export async function uploadFile(file: File): Promise<UploadFileResult> {
  try {
    const formData = new FormData();
    formData.append("file", file);

    const response = await authedFetch(`${API_BASE}/api/uploads/file`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "文件上传失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] uploadFile failed:", err);
    }
    throw err;
  }
}

// #region 召回率测试 API

export interface RecallTestDataset {
  dataset_id: string;
  dataset_version?: string;
  description?: string;
  baseline_config?: {
    top_k?: number;
    vector_weight?: number;
    lexical_weight?: number;
    fusion_strategy?: string;
    candidate_multiplier?: number;
    hnsw_ef_search?: number;
  };
  memory_items: Array<{
    id: string;
    task_id: string;
    pack_id?: string;
    version?: number;
    chunk_type?: string;
    stage?: string;
    iteration?: number;
    source_refs?: string[];
    summary: string;
    embedding_text: string;
    payload?: Record<string, unknown>;
    status?: string;
  }>;
  test_cases: Array<{
    case_id: string;
    category: string;
    bucket?: string;
    query: string;
    expected_ids: string[];
    expected_relevance?: number[];
    description?: string;
  }>;
  clear_existing?: boolean;
}

export interface RecallTestRequest {
  dataset_id: string;
  modes?: string[];
  top_k?: number;
  vector_weight?: number;
  lexical_weight?: number;
  fusion_strategy?: string;
  candidate_multiplier?: number;
  hnsw_ef_search?: number;
  allow_degraded_mode?: boolean;
  categories?: string[];
  detailed?: boolean;
}

export interface RecallTestResult {
  test_id: string;
  dataset_id: string;
  modes: string[];
  config: {
    modes: string[];
    top_k: number;
    total_cases: number;
    vector_weight: number;
    lexical_weight: number;
    fusion_strategy: string;
    recall_top_k: number;
    candidate_multiplier: number;
    hnsw_m: number;
    hnsw_ef_construction: number;
    hnsw_ef_search: number;
    allow_degraded_mode: boolean;
    dataset_version: string;
    baseline_config: Record<string, unknown>;
    description: string;
  };
  summary: Record<string, Record<string, number>>;
  summary_by_mode: Record<string, Record<string, number>>;
  by_category: Record<string, Record<string, Record<string, number>>>;
  details: Array<{
    case_id: string;
    category: string;
    bucket: string;
    query: string;
    expected_ids: string[];
    expected_relevance?: number[] | null;
    results: Record<
      string,
      {
        retrieved: Array<{ id: string; score: number }>;
        hit: boolean;
        rank: number | null;
        metrics: Record<string, number>;
        raw_metrics: Record<string, number>;
      }
    >;
  }>;
  results: Array<{
    mode: string;
    case_id: string;
    query: string;
    retrieved_ids: string[];
    expected_ids: string[];
    metrics: {
      recall_at_k: number;
      precision_at_k: number;
      mrr_at_k: number;
      ndcg_at_k: number;
      f1_at_k?: number;
      avg_latency_ms?: number;
    };
  }>;
  aggregated_metrics: {
    recall_at_k: number;
    precision_at_k: number;
    mrr_at_k: number;
    ndcg_at_k: number;
    f1_at_k: number;
    avg_latency_ms: number;
  };
  primary_mode: string | null;
  started_at: string;
  completed_at: string;
  duration_ms: number;
}

export interface RecallTestHistory {
  test_id: string;
  dataset_id: string;
  dataset_version: string;
  modes: string[];
  top_k: number;
  "hybrid_recall@5": number;
  "hybrid_ndcg@5": number;
  hybrid_mrr: number;
  hybrid_degraded_rate: number;
  hybrid_vector_presence_rate: number;
  avg_latency_ms: number;
  vector_weight: number;
  lexical_weight: number;
  fusion_strategy: string;
  baseline_config: Record<string, unknown>;
  created_at: string;
  duration_ms: number;
}

export async function createRecallDataset(
  dataset: RecallTestDataset,
): Promise<{ success: boolean; message: string }> {
  try {
    const response = await authedFetch(`${API_BASE}/api/tests/recall/dataset`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(dataset),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "创建数据集失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] createRecallDataset failed:", err);
    }
    throw err;
  }
}

export async function runRecallTest(
  request: RecallTestRequest,
): Promise<RecallTestResult> {
  try {
    const response = await authedFetch(`${API_BASE}/api/tests/recall/run`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "执行测试失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] runRecallTest failed:", err);
    }
    throw err;
  }
}

export interface RecallHistoryResponse {
  history: RecallTestHistory[];
  total: number;
}

export async function getRecallHistory(
  limit = 20,
  datasetId?: string,
): Promise<RecallTestHistory[]> {
  try {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (datasetId) {
      params.set("dataset_id", datasetId);
    }

    const response = await authedFetch(
      `${API_BASE}/api/tests/recall/history?${params.toString()}`,
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "获取历史记录失败");
    }

    const data = await response.json();
    return Array.isArray(data) ? data : (data.history ?? []);
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] getRecallHistory failed:", err);
    }
    throw err;
  }
}

export async function getRecallTestResult(
  testId: string,
): Promise<RecallTestResult> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/tests/recall/history/${encodeURIComponent(testId)}`,
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "获取测试结果失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] getRecallTestResult failed:", err);
    }
    throw err;
  }
}

export async function deleteRecallTest(testId: string): Promise<{
  test_id: string;
  deleted: boolean;
  summary: Record<string, Record<string, number>>;
}> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/tests/recall/history/${encodeURIComponent(testId)}`,
      {
        method: "DELETE",
      },
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "删除测试记录失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] deleteRecallTest failed:", err);
    }
    throw err;
  }
}

export interface RecallCompareMetricDelta {
  a: number;
  b: number;
  delta: number;
}

export interface RecallCompareModeDiff {
  [metric: string]: RecallCompareMetricDelta;
}

export interface RecallCompareResult {
  test_a: {
    test_id: string;
    config: Record<string, unknown>;
    summary: Record<string, Record<string, number>>;
  };
  test_b: {
    test_id: string;
    config: Record<string, unknown>;
    summary: Record<string, Record<string, number>>;
  };
  diff: Record<string, RecallCompareModeDiff>;
  by_category_diff?: Record<string, Record<string, RecallCompareModeDiff>>;
}

export async function compareRecallTests(
  testA: string,
  testB: string,
): Promise<RecallCompareResult> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/tests/recall/compare?test_a=${encodeURIComponent(testA)}&test_b=${encodeURIComponent(testB)}`,
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "对比测试失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] compareRecallTests failed:", err);
    }
    throw err;
  }
}

// #endregion

// #region Bad Case API

export interface BadCaseType {
  recall_failure: string;
  hallucination: string;
  coverage_gap: string;
  evidence_conflict: string;
  low_quality: string;
  user_complaint: string;
}

export interface BadCaseSeverity {
  critical: string;
  high: string;
  medium: string;
  low: string;
}

export interface BadCaseStatus {
  pending: string;
  analyzed: string;
  fixed: string;
  wont_fix: string;
}

export interface BadCase {
  id: string;
  task_id?: string;
  type: keyof BadCaseType;
  severity: keyof BadCaseSeverity;
  status: keyof BadCaseStatus;
  description: string;
  context: Record<string, unknown>;
  metrics: Record<string, unknown>;
  analysis?: string;
  fix_plan?: string;
  fixed_by?: string;
  created_at: string;
  updated_at: string;
}

export interface BadCaseSummary {
  total: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
}

export interface CreateBadCaseRequest {
  type: keyof BadCaseType;
  description: string;
  task_id?: string;
  severity?: keyof BadCaseSeverity;
  context?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
}

export async function createBadCase(
  request: CreateBadCaseRequest,
): Promise<BadCase> {
  try {
    const response = await authedFetch(`${API_BASE}/api/bad-cases`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "创建 Bad Case 失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] createBadCase failed:", err);
    }
    throw err;
  }
}

export async function getBadCase(badCaseId: string): Promise<BadCase> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/bad-cases/${badCaseId}`,
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "获取 Bad Case 失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] getBadCase failed:", err);
    }
    throw err;
  }
}

export async function listBadCases(
  type?: keyof BadCaseType,
  status?: keyof BadCaseStatus,
  severity?: keyof BadCaseSeverity,
): Promise<BadCase[]> {
  try {
    const params = new URLSearchParams();
    if (type) params.append("type", type);
    if (status) params.append("status", status);
    if (severity) params.append("severity", severity);

    const response = await authedFetch(
      `${API_BASE}/api/bad-cases?${params.toString()}`,
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "获取 Bad Case 列表失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] listBadCases failed:", err);
    }
    throw err;
  }
}

export async function getBadCasesByTask(taskId: string): Promise<BadCase[]> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/bad-cases/task/${taskId}`,
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "获取任务的 Bad Case 失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] getBadCasesByTask failed:", err);
    }
    throw err;
  }
}

export async function updateBadCaseStatus(
  badCaseId: string,
  status: keyof BadCaseStatus,
): Promise<BadCase> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/bad-cases/${badCaseId}/status`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ status }),
      },
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "更新状态失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] updateBadCaseStatus failed:", err);
    }
    throw err;
  }
}

export async function updateBadCaseAnalysis(
  badCaseId: string,
  analysis: string,
  fix_plan?: string,
): Promise<BadCase> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/bad-cases/${badCaseId}/analysis`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ analysis, fix_plan }),
      },
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "更新分析失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] updateBadCaseAnalysis failed:", err);
    }
    throw err;
  }
}

export async function markBadCaseFixed(
  badCaseId: string,
  fixed_by?: string,
): Promise<BadCase> {
  try {
    const response = await authedFetch(
      `${API_BASE}/api/bad-cases/${badCaseId}/fixed`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ fixed_by: fixed_by || "system" }),
      },
    );

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "标记修复失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] markBadCaseFixed failed:", err);
    }
    throw err;
  }
}

export async function getBadCaseSummary(): Promise<BadCaseSummary> {
  try {
    const response = await authedFetch(`${API_BASE}/api/bad-cases/summary`);

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "获取汇总统计失败");
    }

    return await response.json();
  } catch (err) {
    if (IS_DEV) {
      console.warn("[API] getBadCaseSummary failed:", err);
    }
    throw err;
  }
}

// #endregion

// #region Auth API

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserInfo {
  id: string;
  username: string;
  tenant_id: string;
  role: string;
  is_active: boolean;
  auth_method: string;
}

export interface ApiKeyInfo {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  expires_at: string | null;
  is_revoked: boolean;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreated extends ApiKeyInfo {
  plaintext_key: string;
}

/**
 * 用户登录，获取 JWT 令牌对
 */
export async function login(
  username: string,
  password: string,
): Promise<TokenPair> {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "登录失败");
  }

  const tokens = await response.json();
  setTokens(tokens.access_token, tokens.refresh_token);
  return tokens;
}

/**
 * 刷新 access token
 */
export async function refreshToken(): Promise<TokenPair> {
  const refresh = getRefreshToken();
  if (!refresh) {
    throw new Error("无 refresh token，请重新登录");
  }

  const response = await fetch(`${API_BASE}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });

  if (!response.ok) {
    clearTokens();
    const error = await response.json();
    throw new Error(error.detail || "刷新令牌失败，请重新登录");
  }

  const tokens = await response.json();
  setTokens(tokens.access_token, tokens.refresh_token);
  return tokens;
}

/**
 * 获取当前用户信息
 */
export async function getCurrentUser(): Promise<UserInfo> {
  const response = await authedFetch(`${API_BASE}/api/auth/me`);

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "获取用户信息失败");
  }

  return await response.json();
}

/**
 * 登出：清除本地令牌
 */
export function logout(): void {
  clearTokens();
}

/**
 * 注册新用户，注册成功后自动登录
 */
export async function register(
  username: string,
  password: string,
): Promise<UserInfo> {
  const response = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "注册失败");
  }

  return await response.json();
}

/**
 * 创建 API Key（明文仅返回一次）
 */
export async function createApiKey(
  name: string,
  scopes: string[] = [],
  expiresInDays?: number,
): Promise<ApiKeyCreated> {
  const response = await authedFetch(`${API_BASE}/api/api-keys`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      name,
      scopes,
      expires_in_days: expiresInDays,
    }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "创建 API Key 失败");
  }

  return await response.json();
}

/**
 * 列出当前租户的 API Key
 */
export async function listApiKeys(): Promise<ApiKeyInfo[]> {
  const response = await authedFetch(`${API_BASE}/api/api-keys`);

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "获取 API Key 列表失败");
  }

  return await response.json();
}

/**
 * 撤销 API Key
 */
export async function revokeApiKey(keyId: string): Promise<void> {
  const response = await authedFetch(`${API_BASE}/api/api-keys/${keyId}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "撤销 API Key 失败");
  }
}

// #endregion

// #region Memory & Context & Checkpoint

// ===== 类型定义 =====

export type DecisionPackStatus =
  | "draft"
  | "approved"
  | "rejected"
  | "superseded";

export type DecisionChunkType =
  | "decision"
  | "evidence"
  | "conflict"
  | "repair"
  | "reviewer_feedback"
  | "competitor_analysis"
  | "market_intelligence"
  | "pricing_strategy";

export type ReviewStatus = "pending" | "approved" | "rejected" | "needs_retry";

export interface DecisionMemoryItem {
  id: string;
  task_id: string;
  pack_id: string;
  version: number;
  chunk_type: DecisionChunkType;
  stage: string | null;
  iteration: number;
  source_refs: string[];
  summary: string;
  embedding_text: string;
  payload: Record<string, unknown>;
  status: DecisionPackStatus;
  created_at: string;
}

export interface WorkflowMemoryState {
  current_pack_version: number;
  max_iterations: number;
  current_iteration: number;
  current_run_id: string | null;
  last_checkpoint_id: string | null;
  last_stage: string | null;
  last_error_stage: string | null;
  last_error_message: string | null;
  latest_memory_ids: string[];
  last_recall_count: number;
  last_recall_summary: string | null;
  last_reviewer_status: ReviewStatus;
  retry_reason: string | null;
}

export interface TaskMemoryResponse {
  task_id: string;
  memory_state: WorkflowMemoryState | null;
  decision_history: unknown[];
  memory_items: DecisionMemoryItem[];
}

export interface MemoryStats {
  total_items: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  recent_checkpoints: number;
  total_checkpoints: number;
}

export interface ContextNodeUsage {
  node: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  context_limit: number;
  utilization: number;
}

export interface ContextUsage {
  task_id: string;
  nodes: ContextNodeUsage[];
  total_tokens: number;
  context_limit: number;
}

// ===== API 函数 =====

/** 获取任务的记忆状态与记忆块（已有端点） */
export async function getTaskMemory(
  taskId: string,
): Promise<TaskMemoryResponse> {
  const response = await authedFetch(`${API_BASE}/api/tasks/${taskId}/memory`);
  if (!response.ok) throw new Error("获取任务记忆失败");
  return await response.json();
}

/** 获取任务历史（已有端点） */
export async function getTaskHistory(
  taskId: string,
): Promise<Record<string, unknown>> {
  const response = await authedFetch(`${API_BASE}/api/tasks/${taskId}/history`);
  if (!response.ok) throw new Error("获取任务历史失败");
  return await response.json();
}

/** 语义搜索决策记忆（依赖后端 B2） */
export async function searchDecisionMemory(
  query: string,
  limit: number = 10,
  sortOrder: 'asc' | 'desc' = 'desc',
): Promise<DecisionMemoryItem[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit), sort_order: sortOrder });
  const response = await authedFetch(`${API_BASE}/api/memory/search?${params}`);
  if (!response.ok) throw new Error("搜索决策记忆失败");
  return await response.json();
}

/** 列出决策记忆块，分页（依赖后端 B3） */
export async function listMemoryItems(
  page: number = 1,
  pageSize: number = 20,
  chunkType?: DecisionChunkType,
  sortOrder: 'asc' | 'desc' = 'desc',
): Promise<{ items: DecisionMemoryItem[]; total: number }> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort_order: sortOrder,
  });
  if (chunkType) params.set("chunk_type", chunkType);
  const response = await authedFetch(`${API_BASE}/api/memory/items?${params}`);
  if (!response.ok) throw new Error("获取记忆块列表失败");
  return await response.json();
}

/** 获取任务的检查点列表（依赖后端 B4） */
export async function getTaskCheckpoints(
  taskId: string,
): Promise<WorkflowCheckpoint[]> {
  const response = await authedFetch(
    `${API_BASE}/api/tasks/${taskId}/checkpoints`,
  );
  if (!response.ok) throw new Error("获取检查点列表失败");
  return await response.json();
}

/** 获取记忆统计（依赖后端 B5） */
export async function getMemoryStats(): Promise<MemoryStats> {
  const response = await authedFetch(`${API_BASE}/api/memory/stats`);
  if (!response.ok) throw new Error("获取记忆统计失败");
  return await response.json();
}

/** 获取任务的 context window 使用情况（依赖后端 B6） */
export async function getTaskContext(taskId: string): Promise<ContextUsage> {
  const response = await authedFetch(`${API_BASE}/api/tasks/${taskId}/context`);
  if (!response.ok) throw new Error("获取上下文使用情况失败");
  return await response.json();
}

// #endregion
