import type { ChatTurn, FactoryState, Incident } from "./types";

/** 後端擋下操作時(HTTP 409)回傳給主管看的原因。 */
export class ApiError extends Error {}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.json().then((d) => d.detail, () => null);
    throw new ApiError(typeof detail === "string" ? detail : `後端回應 ${response.status}`);
  }
  return response.json();
}

export const api = {
  state: () => call<FactoryState>("/state"),
  incident: (id: string) => call<Incident>(`/incidents/${id}`),
  start: () => call<FactoryState>("/factory/start", {}),
  pause: () => call<FactoryState>("/factory/pause", {}),
  reset: () => call<FactoryState>("/factory/reset", {}),
  emit: () => call<{ id: string }>("/factory/emit", {}),
  approve: (id: string, planId: string) => call<Incident>(`/incidents/${id}/approve`, { plan_id: planId }),
  reject: (id: string, reason: string) => call<Incident>(`/incidents/${id}/reject`, { reason }),
  reassess: (id: string) => call<{ id: string }>(`/incidents/${id}/reassess`, {}),
  ask: (id: string, question: string) => call<ChatTurn>(`/incidents/${id}/ask`, { question }),
  confirmTask: (id: string, taskId: string) => call<Incident>(`/incidents/${id}/tasks/${taskId}/confirm`, {}),
};
