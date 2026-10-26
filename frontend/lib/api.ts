import type { ChatTurn, FactoryState, Incident, ProcessParameter } from "./types";

/** 後端擋下操作時(HTTP 409)回傳給主管看的原因;5xx 多半是轉接時連不到後端,結果不明。 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** 請求有沒有被後端處理不確定:連線斷掉,或轉接層回 5xx。 */
export function outcomeUnknown(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status >= 500;
}

/** 核准請求的識別碼;用 http 開區網位址時沒有 crypto.randomUUID。 */
export function requestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export type Seen = { snapshot_version: number; schedule_version: number; policy_version: string };

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.json().then((d) => d.detail, () => null);
    throw new ApiError(typeof detail === "string" ? detail : `後端回應 ${response.status}`, response.status);
  }
  return response.json();
}

export const api = {
  state: () => call<FactoryState>("/state"),
  incident: (id: string) => call<Incident>(`/incidents/${id}`),
  parameters: () => call<ProcessParameter[]>("/parameters"),
  start: () => call<FactoryState>("/factory/start", {}),
  pause: () => call<FactoryState>("/factory/pause", {}),
  reset: () => call<FactoryState>("/factory/reset", {}),
  /** id 為 null 表示有事件等決定,這件先排隊 */
  emit: () => call<{ id: string | null; backlog?: number }>("/factory/emit", {}),
  approve: (id: string, planId: string, expected: Seen, requestId: string) =>
    call<Incident>(`/incidents/${id}/approve`, { plan_id: planId, expected, request_id: requestId }),
  reject: (id: string, reason: string) => call<Incident>(`/incidents/${id}/reject`, { reason }),
  reassess: (id: string) => call<{ id: string }>(`/incidents/${id}/reassess`, {}),
  ask: (id: string, question: string) => call<ChatTurn>(`/incidents/${id}/ask`, { question }),
  confirmTask: (id: string, taskId: string) => call<Incident>(`/incidents/${id}/tasks/${taskId}/confirm`, {}),
};
