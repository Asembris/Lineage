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
 *  generation-ordered by window_start — ordinal position IS the generation.
 *
 *  `sample_size` is the number of belief-driven decisions the window's confidence was aggregated
 *  over. belief_performance persists NO denominator, so the backend re-aggregates it from
 *  `decisions` at read time. The Wilson bounds are NULL whenever the sample cannot be trusted
 *  (see StalenessUncertainty.sample_agreement) — withheld, never faked. A null bound must be
 *  rendered as a STATED absence, never as a narrow interval.
 *
 *  `false_positive_rate` deliberately has NO interval: it is structurally 0 for a belief that only
 *  ever approves, and an interval there would dress a structural impossibility as an estimate. */
export interface BeliefPerformanceWindow {
  window_start: ISODateTime;
  window_end: ISODateTime;
  confidence: number;
  false_positive_rate: number;
  frauds_approved: number;
  sample_size: number;
  confidence_ci_low: number | null;
  confidence_ci_high: number | null;
}

/** How much to trust the curve — the backend's honest "cannot determine" state (StalenessUncertainty).
 *
 *  `sample_agreement`:
 *    - "agreed"      — every window's persisted confidence reproduces from the decisions it
 *                      summarizes; the intervals are real and are emitted.
 *    - "disagreed"   — a persisted confidence does NOT reproduce; belief_performance is stale
 *                      w.r.t. `decisions`. Intervals withheld (a fresh denominator on a stale point
 *                      estimate is exactly the confidently-wrong number).
 *    - "unavailable" — a window has no decisions to aggregate. No interval is derivable.
 *
 *  `decay_supported` — whether "valid then / rotten now" survives its own uncertainty: a two-sided
 *  FISHER EXACT test on first vs. last window rejects "same rate" at 95%, AND the movement is
 *  downward. There is NO minimum-sample-size gate; Fisher is exact at every n, so a thin window
 *  disqualifies itself. Comparing the intervals for overlap would NOT have done this — it calls a
 *  single wrongly-decided decision a supported decay. null = not assertable. */
export interface StalenessUncertainty {
  method: string;
  confidence_level: number;
  sample_source: string;
  sample_agreement: "agreed" | "disagreed" | "unavailable";
  decay_supported: boolean | null;
  decay_p_value: number | null;
  decay_support_criterion: string;
}

export interface BeliefPerformanceResponse {
  belief_id: UUID;
  windows: BeliefPerformanceWindow[];
  count: number;
  /** null when the belief has no measured windows at all (an honest absence, not an error). */
  uncertainty: StalenessUncertainty | null;
}

/** Top-line provenance-integrity verdict (ProvenanceAuditResponse, Item A). The full
 *  backend DTO also carries a per-edge `edges[]` report; the honesty ledger consumes only
 *  this top-line fact (a zero-argument data-point, not a per-edge UI), so `edges` is omitted. */
export interface ProvenanceAuditResponse {
  belief_id: UUID;
  belief_status: string;
  status: string; // 'CLEAN' | 'ANOMALOUS' | 'INCONCLUSIVE'
  edge_count: number;
  anomaly_count: number;
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

/**
 * A decision is either a Phase-2 CARD authorization, or an AML decision grounded in a REAL IBM
 * money-flow edge (the grounding seam, migration 0006). Distinguish them by
 * `aml_transaction_id !== null`.
 *
 * Three fields are nullable because of that split, and each null is a refusal to fabricate:
 *   merchant        — null for AML: a bank-to-bank transfer has no merchant.
 *   confidence      — null for AML: the structural witness is deterministic, so there is no
 *                     confidence to report.
 *   amount_currency — null for CARD rows: the Phase-2 simulator never declared a currency.
 *                     AML rows carry their real one (the extract spans 14, not just dollars).
 */
export type WitnessOutcome = "MATCH" | "CONCLUSIVE_NO" | "INCONCLUSIVE";

/** The two kinds of decision the fleet makes. This is a STRUCTURAL distinction, not a UI
 *  convention: migration 0007's `ck_decisions_kind` CHECK makes a mislabelled row unwritable, and
 *  `GET /decisions?kind=` 422s on anything else rather than serving a silent empty page. */
export type DecisionKind = "card" | "aml";

export interface Decision {
  id: UUID;
  agent_id: UUID;
  txn_ref: string;
  merchant: string | null;
  amount: number;
  amount_currency: string | null;
  verdict: string; // 'approve' | 'decline' | 'blocked'
  driving_belief_id: UUID | null;
  confidence: number | null;
  decided_at: ISODateTime;
  is_fraud: boolean;
  aml_transaction_id: UUID | null;
  /* THE BASIS of an AML decision; null for a card decision.
   *
   * READ THIS BEFORE COUNTING AN AML `approve`. Two different witness outcomes both map to
   * `approve` and `verdict` alone CANNOT tell them apart:
   *   CONCLUSIVE_NO — there is no cycle. BUT ONLY 16 OF THESE 463 WERE ACTUALLY SEARCHED:
   *                   the other 447 are SELF-LOOPS (an account paying itself), which is not a
   *                   transfer, so it is excluded from the graph's adjacency and no search ever
   *                   ran. Its long-standing gloss ("searched; there is no cycle") described all
   *                   463 as searches. The count was never wrong; its description of itself was.
   *   INCONCLUSIVE  — the search ran off the edge of the 1,500-edge extract and
   *                   COULD NOT DETERMINE.                (980 edges = 65.3%, 252 laundering)
   * So 980 of the belief's 1,443 approvals are not "this is clean" — they are "we could not
   * tell". That is a disclosed modeling choice, not a corner case. A PROJECTION of the persisted
   * `txn_ref`, i.e. what the agent RECORDED, never a fresh re-run of the witness — which is also
   * why this field stays THREE-valued: the self-loop split is a property of the EVIDENCE
   * (re-derived from the graph), not of what the agent RECORDED. The evidence layer serves it, as
   * a per-witness `detail` string on GET /aml/transactions/{id}/interrogate, and the AML console
   * must render the four apart. */
  witness_outcome: WitnessOutcome | null;
}

export interface DecisionListResponse {
  decisions: Decision[];
  total: number;
  limit: number;
  offset: number;
  agent_id: UUID | null;
  aml_transaction_id: UUID | null;
  driving_belief_id: UUID | null;
  kind: "aml" | "card" | null;
  witness_outcome: WitnessOutcome | null;
  is_fraud: boolean | null;
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

/* --- The EVIDENCE layer: GET /aml/transactions/{id}/interrogate ------------
 *
 * THE ORACLE BOUNDARY, IN TYPES. Nothing below carries a label, and nothing below reaches a
 * `Decision`. That separation is not a convention — `frontend/scripts/composition-guard.mjs`
 * walks the TYPE GRAPH and fails the build if any component's prop surface reaches BOTH this
 * family and the `Decision` family. The two layers are joined by an ORDERED REVEAL (the evidence
 * surface takes over the body; the audit layer is not mounted beside it), and the join is carried
 * by a bare `UUID` — an id carries no verdict and no ground truth.
 *
 * The label is never readable by the DECIDER; never served as EVIDENCE on the evidence layer; and
 * served only where it is an AUDIT fact, attached to a decision that was already made without it.
 * `Decision.is_fraud` is that audit fact. It has no counterpart here, and must never gain one.
 */

/** How to read `AmlWitness.transaction_ids` as an ORDER. Rung 2 renders none of these as geometry
 *  (the ring, the legs and the bundle are Rung 3) — it reports the SHAPE and the SIZE only. */
export type TraversalKind = "RING" | "LEGS" | "BUNDLE" | "NONE";

/** A node of the money-flow graph. Identity is the compound (bank, account), never account alone. */
export interface AmlAccount {
  id: UUID;
  bank: string;
  account: string;
}

/** An EDGE of the money-flow graph — a transaction runs BETWEEN two accounts.
 *
 *  `from_account_id === to_account_id` is a SELF-LOOP: an account paying itself, which is not a
 *  transfer, so `aml_graph.Graph` excludes it from adjacency by construction. This is the one field
 *  pair the console re-derives the fourth basis from — see `basisOf()`. */
export interface AmlTransaction {
  id: UUID;
  ts: ISODateTime;
  from_account_id: UUID;
  to_account_id: UUID;
  amount_paid: number;
  payment_currency: string;
  amount_received: number;
  receiving_currency: string;
  payment_format: string;
}

/** One typology's structural verdict on the subject, re-derived FRESH from the current graph.
 *
 *  `outcome` reuses the `WitnessOutcome` union because it is the same VALUE DOMAIN (aml_graph's
 *  three-way `Outcome`). It is emphatically NOT the same OBJECT as `Decision.witness_outcome`:
 *  that one is what the agent RECORDED at decision time, this one is a fresh re-derivation from
 *  the graph as it stands now. G5 drew that line and it holds here.
 *
 *  `boundary_account_id` is non-null EXACTLY on INCONCLUSIVE (measured: 980/980 name one) and null
 *  everywhere else — so "we ran off the edge of the data" is always renderable as a PLACE. */
export interface AmlWitness {
  typology: string;
  flag_capable: boolean;
  outcome: WitnessOutcome;
  kind: TraversalKind;
  transaction_ids: UUID[];
  legs: Record<string, UUID[]> | null;
  boundary_account_id: UUID | null;
  detail: string;
}

/** Everything a click on one transaction resolves to. Deterministic; no model call; no label. */
export interface AmlInterrogationResponse {
  transaction_id: UUID;
  subject: AmlTransaction;
  witnesses: AmlWitness[];
  competing_typologies: string[];
  has_competing_structure: boolean;
  /** Every transaction/account referenced above, resolved to a real row — no per-id round-trip. */
  transactions: Record<UUID, AmlTransaction>;
  accounts: Record<UUID, AmlAccount>;
  as_of: string | null;
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
