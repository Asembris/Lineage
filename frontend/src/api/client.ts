/*
 * Typed API client — one function per row of the endpoint table in FRONTEND.md.
 * Every call hits the real backend; nothing here is mocked. Phase 1 only calls
 * listAgents() (the connectivity proof); the rest are defined and typed now so
 * later phases wire straight in.
 */

import type {
  AgentBeliefsResponse,
  AgentListResponse,
  AmlInterrogationResponse,
  BeliefListResponse,
  BeliefPerformanceResponse,
  DecisionListResponse,
  InvalidateResponse,
  LineageResponse,
  ProvenanceAuditResponse,
  UUID,
  WitnessOutcome,
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

// `boolean` is accepted so a false-y filter still travels: `is_fraud=false` is a REAL filter
// (decisions the belief got right), and dropping it would silently widen the query to "any".
function buildQuery(
  params: Record<string, string | number | boolean | undefined>,
): string {
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
  opts: {
    agentId?: UUID;
    amlTransactionId?: UUID;
    drivingBeliefId?: UUID;
    kind?: "aml" | "card";
    witnessOutcome?: WitnessOutcome;
    isFraud?: boolean;
    limit?: number;
    offset?: number;
  } = {},
): Promise<DecisionListResponse> {
  return request<DecisionListResponse>(
    `/decisions${buildQuery({
      agent_id: opts.agentId,
      aml_transaction_id: opts.amlTransactionId,
      driving_belief_id: opts.drivingBeliefId,
      kind: opts.kind,
      witness_outcome: opts.witnessOutcome,
      is_fraud: opts.isFraud,
      limit: opts.limit,
      offset: opts.offset,
    })}`,
  );
}

/** COUNT decisions matching a filter, without fetching any. `limit=1`, read `total`.
 *
 *  This is how the honesty ledger reads the seam's census LIVE (1,500 / 57 / 463 / 980, and with
 *  isFraud, 43 / 5 / 252). It matters that it is COUNTED and not retyped: that census has twice
 *  been corrupted by prose — once misstated (the phantom "728 / 48.5%"), once falsely sourced (a
 *  `verify_seam` script that never existed). A number read from the cluster cannot be wrong about
 *  the cluster. See NOTES.md → "FABRICATED VERIFICATION CITATIONS". */
export async function countDecisions(
  opts: Parameters<typeof listDecisions>[0] = {},
): Promise<number> {
  const res = await listDecisions({ ...opts, limit: 1 });
  return res.total;
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

/** GET /beliefs/{id}/provenance-audit — top-line provenance-integrity verdict (Item A).
 *  A zero-argument data-point call (belief id only): CLEAN / ANOMALOUS / INCONCLUSIVE +
 *  edge/anomaly counts. Consumed by the honesty ledger as a live fact, NOT a per-edge feature. */
export function getProvenanceAudit(
  beliefId: UUID,
): Promise<ProvenanceAuditResponse> {
  return request<ProvenanceAuditResponse>(`/beliefs/${beliefId}/provenance-audit`);
}

/* --- The evidence layer --------------------------------------------------- */

/** GET /aml/transactions/{id}/interrogate — what structure the graph witnesses around one
 *  real money-flow edge, and in what order. Deterministic, free, and label-free: no model call,
 *  and the response carries no ground truth (see AmlInterrogationResponse).
 *
 *  `asOf` pins the graph AND the row resolution to ONE MVCC snapshot (real AS OF SYSTEM TIME).
 *  Out-of-window / malformed → 400; unknown transaction → 404. Not wired to the UI in Rung 2. */
export function interrogateTransaction(
  txnId: UUID,
  asOf?: string,
): Promise<AmlInterrogationResponse> {
  return request<AmlInterrogationResponse>(
    `/aml/transactions/${txnId}/interrogate${buildQuery({ as_of: asOf })}`,
  );
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

/*
 * The consistency stream is consumed by `lib/consistencyStream.ts` — a fetch +
 * ReadableStream reader, NOT a native EventSource. This is deliberate: this endpoint
 * is DESTRUCTIVE (it truncates + reseeds the cluster and runs a real invalidation to
 * completion), and a bare `new EventSource(url)` auto-reconnects both on error AND
 * after the server closes the stream on `summary` — which would silently re-trigger the
 * wipe on a loop (the failure mode NOTES.md documents happening once). The fetch reader
 * runs exactly once and never reconnects. See lib/consistencyStream.ts.
 */
