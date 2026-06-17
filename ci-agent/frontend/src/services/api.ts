import createClient from "openapi-fetch";

import type { components, paths } from "./openapi";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const IS_DEV = import.meta.env.MODE === "development";

// #region agent log
const DEBUG_SESSION_ID = "43496882-51bc-41f7-948d-6fd94772f027";
const DEBUG_LOG_ENDPOINT = "http://127.0.0.1:7337/ingest/43496882-51bc-41f7-948d-6fd94772f027";
function debugLog(location: string, message: string, data: Record<string, unknown>, hypothesisId: string, runId = "run1") {
  fetch(DEBUG_LOG_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Debug-Session-Id": DEBUG_SESSION_ID,
    },
    body: JSON.stringify({
      sessionId: DEBUG_SESSION_ID,
      location,
      message,
      data,
      timestamp: Date.now(),
      runId,
      hypothesisId,
    }),
  }).catch(() => {});
}
// #endregion

const client = createClient<paths>({
  baseUrl: API_BASE,
  headers: IS_DEV
    ? {
        // 开发环境：放宽请求头限制
        "X-Development-Mode": "true",
        "X-Loose-Validation": "true",
      }
    : {},
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
export type TaskRecord = ApiSchema<"TaskRecord">;
export type TaskMetrics = ApiSchema<"TaskMetrics">;
export type InterventionRequest = ApiSchema<"InterventionRequest">;
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

function stringifyDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const items = detail
      .map((item) => stringifyDetail(item) ?? (typeof item === "object" && item !== null ? JSON.stringify(item) : String(item)))
      .filter(Boolean);
    return items.length > 0 ? items.join("；") : null;
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.msg === "string") return record.msg;
    if (typeof record.message === "string") return record.message;
    if ("loc" in record || "type" in record) {
      const loc = Array.isArray(record.loc) ? record.loc.join(".") : undefined;
      const message = typeof record.msg === "string" ? record.msg : typeof record.message === "string" ? record.message : null;
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
        ? (record.response as Record<string, unknown>).data ?? (record.response as Record<string, unknown>).detail
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

  let eventSource: EventSource | null = null;
  let pollingTimer: ReturnType<typeof setInterval> | null = null;
  let isClosed = false;

  const cleanup = () => {
    if (isClosed) return;
    isClosed = true;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
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

  const startSSE = () => {
    const url = `${API_BASE}/api/tasks/${taskId}/events/stream`;
    eventSource = new EventSource(url);

    const timeoutId = setTimeout(() => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      if (IS_DEV) {
        console.debug("[SSE] Timeout, falling back to polling");
      }
      startPolling();
    }, sseTimeout);

    eventSource.onmessage = (event) => {
      clearTimeout(timeoutId);
      try {
        const taskEvent: TaskEvent = JSON.parse(event.data);
        callbacks.onEvent(taskEvent);
        if (taskEvent.status === "completed" || taskEvent.status === "failed") {
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
        }
      } catch (parseErr) {
        if (IS_DEV) {
          console.debug("[SSE] Parse error (continuing):", parseErr);
        }
      }
    };

    eventSource.onerror = () => {
      clearTimeout(timeoutId);
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      if (IS_DEV) {
        console.debug("[SSE] Error, falling back to polling");
      }
      startPolling();
    };
  };

  if (forcePolling || IS_DEV) {
    startPolling();
  } else {
    startSSE();
  }

  return { unsubscribe: cleanup };
}

export async function cancelTask(taskId: string): Promise<{ status: string; task: TaskRecord }> {
  const response = await fetch(`${API_BASE}/api/tasks/${taskId}/cancel`, {
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

    const response = await fetch(`${API_BASE}/api/uploads/files`, {
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

    const response = await fetch(`${API_BASE}/api/uploads/file`, {
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
