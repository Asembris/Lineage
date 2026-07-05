/*
 * Typed API client — one function per row of the endpoint table in FRONTEND.md.
 * Every call hits the real backend; nothing here is mocked. Phase 1 only calls
 * listAgents() (the connectivity proof); the rest are defined and typed now so
 * later phases wire straight in.
 */

import type {
  AgentBeliefsResponse,
  AgentListResponse,
  BeliefListResponse,
  BeliefPerformanceResponse,
  DecisionListResponse,
  InvalidateResponse,
  LineageResponse,
  UUID,
} from "./types";

/** Backend base URL. Override with VITE_API_BASE in a .env file. */
export const API_BASE = (
  import.meta.env.VITE_API_BASE ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** Thrown for any non-2xx response; carries the HTTP status and server detail. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
  } catch (cause) {
    // Network / CORS failure never reaches the server — surface it plainly.
    throw new ApiError(0, `cannot reach the API at ${API_BASE} (${String(cause)})`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/* --- Read surface --------------------------------------------------------- */

/** GET /agents — full genealogy, optionally filtered. */
export function listAgents(
  opts: { bloodline?: string; status?: string } = {},
): Promise<AgentListResponse> {
  return request<AgentListResponse>(
    `/agents${buildQuery({ bloodline: opts.bloodline, status: opts.status })}`,
  );
}

/** GET /decisions — fleet-wide feed by default, or one agent's history. */
export function listDecisions(
  opts: { agentId?: UUID; limit?: number; offset?: number } = {},
): Promise<DecisionListResponse> {
  return request<DecisionListResponse>(
    `/decisions${buildQuery({
      agent_id: opts.agentId,
      limit: opts.limit,
      offset: opts.offset,
    })}`,
  );
}

/** GET /beliefs — belief catalog, optionally filtered by status. */
export function listBeliefs(
  opts: { status?: string } = {},
): Promise<BeliefListResponse> {
  return request<BeliefListResponse>(`/beliefs${buildQuery({ status: opts.status })}`);
}

/** GET /agents/{id}/beliefs — one agent's beliefs, optional real AOST time-travel. */
export function getAgentBeliefs(
  agentId: UUID,
  asOf?: string,
): Promise<AgentBeliefsResponse> {
  return request<AgentBeliefsResponse>(
    `/agents/${agentId}/beliefs${buildQuery({ as_of: asOf })}`,
  );
}

/** GET /beliefs/{id}/lineage — trace a belief origin → current. */
export function getBeliefLineage(beliefId: UUID): Promise<LineageResponse> {
  return request<LineageResponse>(`/beliefs/${beliefId}/lineage`);
}

/** GET /beliefs/{id}/performance — the measured staleness curve (ordered windows). */
export function getBeliefPerformance(
  beliefId: UUID,
): Promise<BeliefPerformanceResponse> {
  return request<BeliefPerformanceResponse>(`/beliefs/${beliefId}/performance`);
}

/* --- The governed write --------------------------------------------------- */

/** POST /beliefs/{id}/invalidate — atomic fleet-wide invalidation. */
export function invalidateBelief(
  beliefId: UUID,
  actorId: UUID,
): Promise<InvalidateResponse> {
  return request<InvalidateResponse>(`/beliefs/${beliefId}/invalidate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor_id: actorId }),
  });
}

/* --- SSE ------------------------------------------------------------------ */

/**
 * GET /demo/consistency/stream — open the SSE stream of real observer samples.
 * Returns a native EventSource; consumers attach listeners for the named events
 * ('start', 'sample', 'summary', 'busy'). Remember to close() it.
 */
export function openConsistencyStream(): EventSource {
  return new EventSource(`${API_BASE}/demo/consistency/stream`);
}
