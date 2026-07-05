/*
 * TypeScript mirrors of the backend's real Pydantic DTOs (app/schemas.py).
 * These are hand-kept in sync with the API — every field here exists in a real
 * response. UUIDs and datetimes arrive as JSON strings.
 */

export type UUID = string;
export type ISODateTime = string; // ISO-8601, e.g. "2026-07-01T12:34:56.789Z"

/** Agent as returned by the deposition path (AgentOut). */
export interface Agent {
  id: UUID;
  generation: number;
  bloodline: string;
  status: string; // 'alive' | 'dead'
}

/** Agent with genealogy fields for the tree (AgentGenealogyOut). */
export interface AgentGenealogy {
  id: UUID;
  generation: number;
  bloodline: string;
  status: string; // 'alive' | 'dead'
  spawned_at: ISODateTime;
  retired_at: ISODateTime | null;
  parent_id: UUID | null;
}

export interface AgentListResponse {
  agents: AgentGenealogy[];
  count: number;
}

export interface Belief {
  id: UUID;
  rule_text: string;
  status: string; // 'active' | 'invalidated'
  originating_agent_id: UUID;
  formed_at: ISODateTime;
  invalidated_at: ISODateTime | null;
}

export interface BeliefListResponse {
  beliefs: Belief[];
  count: number;
}

export interface AgentBeliefsResponse {
  agent: Agent;
  as_of: string | null;
  beliefs: Belief[];
}

/** One measured performance window (BeliefPerformanceWindow). Windows are
 *  generation-ordered by window_start — ordinal position IS the generation. */
export interface BeliefPerformanceWindow {
  window_start: ISODateTime;
  window_end: ISODateTime;
  confidence: number;
  false_positive_rate: number;
  frauds_approved: number;
}

export interface BeliefPerformanceResponse {
  belief_id: UUID;
  windows: BeliefPerformanceWindow[];
  count: number;
}

export interface LineageNode {
  depth: number;
  agent_id: UUID;
  generation: number;
  bloodline: string;
  status: string;
  from_agent_id: UUID | null;
  inherited_at: ISODateTime | null;
}

export interface LineageResponse {
  belief: Belief;
  origin_agent_id: UUID;
  path: LineageNode[];
}

export interface Decision {
  id: UUID;
  agent_id: UUID;
  txn_ref: string;
  merchant: string;
  amount: number;
  verdict: string; // 'approve' | 'decline' | 'blocked'
  driving_belief_id: UUID | null;
  confidence: number;
  decided_at: ISODateTime;
  is_fraud: boolean;
}

export interface DecisionListResponse {
  decisions: Decision[];
  total: number;
  limit: number;
  offset: number;
  agent_id: UUID | null;
}

/** The self-contained pre-kill record (PreInvalidationState) — captured inside the
 *  invalidation txn before the flip. This is the SAME dict embedded and hash-covered in
 *  the S3 certificate (the backend serializes one value into both; a round-trip test
 *  asserts byte-identity), so the console can surface the proof without reconstructing it. */
export interface PreInvalidationState {
  belief_status: string; // 'active' by the invalidation guard
  closure_edge_total: number;
  closure_edge_open: number;
  affected_agent_count: number;
  living_holder_count: number;
  snapshot_hlc: string;
  source: string; // 'issue-time-read' | 'aost-replay' | 'derived'
}

export interface InvalidateResponse {
  belief_id: UUID;
  status: string; // 'invalidated'
  actor_id: UUID;
  invalidated_at: ISODateTime;
  affected_agent_count: number;
  affected_edge_count: number;
  living_holder_count: number;
  db_snapshot_hlc: string;
  audit_id: UUID;
  pre_invalidation_state: PreInvalidationState;
  certificate_id: UUID | null;
  certificate_s3_key: string | null;
  certificate_status: string; // 'written' | 'failed'
  content_hash: string | null;
}

/* --- SSE: GET /demo/consistency/stream event payloads --------------------- */

/** Which invalidation the stream runs (the `?strategy=` query param, echoed in `start`).
 *  `eventual` = the per-holder fan-out baseline (a real SPLIT window); `strong` = the REAL
 *  atomic endpoint function (invalidate_belief) — one serializable commit, 0 split reads. */
export type ConsistencyStrategy = "eventual" | "strong";

/** The closure classification the observer emits (consistency.classify). */
export type ConsistencyState = "ALL_ACTIVE" | "SPLIT" | "ALL_INVALIDATED";

export interface ConsistencyStartEvent {
  belief_id: UUID;
  strategy: string;
  note: string;
}

export interface ConsistencySampleEvent {
  seq: number;
  state: ConsistencyState;
  open_edges: number;
  total_edges: number;
  elapsed_ms: number;
}

export interface ConsistencySummaryEvent {
  commit_points: number;
  split_samples: number;
  saw_transition: boolean;
  total_samples: number;
  elapsed_ms: number;
}

export interface ConsistencyBusyEvent {
  detail: string;
}
