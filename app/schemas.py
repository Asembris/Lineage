"""Pydantic response DTOs (Phase 1 — kept light; full validation is Phase 4)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, field_validator


class AgentOut(BaseModel):
    id: uuid.UUID
    generation: int
    bloodline: str
    status: str


class BeliefOut(BaseModel):
    id: uuid.UUID
    rule_text: str
    status: str
    originating_agent_id: uuid.UUID
    formed_at: dt.datetime
    # Optional so read paths that don't select it (deposition / lineage) default cleanly;
    # GET /beliefs selects it so the frontend can flag invalidated beliefs.
    invalidated_at: dt.datetime | None = None


class AgentBeliefsResponse(BaseModel):
    agent: AgentOut
    as_of: str | None
    beliefs: list[BeliefOut]


class LineageNode(BaseModel):
    depth: int
    agent_id: uuid.UUID
    generation: int
    bloodline: str
    status: str
    from_agent_id: uuid.UUID | None
    inherited_at: dt.datetime | None


class LineageResponse(BaseModel):
    belief: BeliefOut
    origin_agent_id: uuid.UUID
    path: list[LineageNode]


# --- Roadmap Item 2: reversible-deterministic replay --------------------------


class ReplayClosureNode(BaseModel):
    """One node of a belief's inheritance closure as reconstructed at a past time.

    Same shape as LineageNode plus `edge_invalidated_at` (whether this holder's inheritance
    edge was revoked as of the replayed timestamp) — the per-edge closure state that item B's
    counterfactual invalidation replays over.
    """

    depth: int
    agent_id: uuid.UUID
    generation: int
    bloodline: str
    status: str
    from_agent_id: uuid.UUID | None
    inherited_at: dt.datetime | None
    edge_invalidated_at: dt.datetime | None


class ReplaySnapshotResponse(BaseModel):
    """A deterministic, content-hashed reconstruction of a belief's closure AS OF a time.

    `content_hash` = sha256 over the canonical JSON of the reconstructed world (belief +
    closure) only; `as_of` (input) and `read_hlc` (the resolved MVCC read timestamp) are
    provenance, NOT hashed. Two independent reads at the same timestamp are byte-identical.
    """

    belief: BeliefOut
    origin_agent_id: uuid.UUID
    closure: list[ReplayClosureNode]
    as_of: str | None  # the requested ISO/HLC point; None => current-state snapshot
    read_hlc: str  # the MVCC read timestamp actually used (cluster_logical_timestamp())
    content_hash: str  # 'sha256:...' over the canonical reconstructed world


# --- Phase 3: atomic invalidation ---------------------------------------------


class InvalidateRequest(BaseModel):
    # The human supervisor authorizing the invalidation. NOT a fleet agent (agents = the
    # supervised AI fleet), so it is intentionally NOT checked against the agents table.
    # Lean Phase 4 only requires a well-formed, non-null identifier: the uuid.UUID type gives
    # well-formed/non-null, and this validator rejects the all-zeros nil UUID sentinel so a
    # "null identifier" cannot masquerade as a real actor and produce a dangling audit row.
    actor_id: uuid.UUID

    @field_validator("actor_id")
    @classmethod
    def _reject_nil(cls, v: uuid.UUID) -> uuid.UUID:
        if v.int == 0:
            raise ValueError("actor_id must be a non-nil identifier")
        return v


class PreInvalidationState(BaseModel):
    """The self-contained "belief active, whole closure open" record captured INSIDE the
    invalidation txn immediately before the flip (invalidation.invalidate_belief → pre_state).

    This is the SAME dict embedded and hash-covered in the S3 certificate's
    `pre_invalidation_state` — serialized here so the console can display the pre-kill proof
    without a second computation path that could silently disagree (the round-trip test asserts
    byte-identity with the certificate). Not re-derived; the value already exists at issue time.
    """

    belief_status: str  # 'active' by the invalidation guard
    closure_edge_total: int
    closure_edge_open: int
    affected_agent_count: int
    living_holder_count: int
    snapshot_hlc: str
    source: str  # 'issue-time-read' (endpoint) | 'aost-replay' (Lambda) | 'derived'


class InvalidateResponse(BaseModel):
    belief_id: uuid.UUID
    status: str  # 'invalidated'
    actor_id: uuid.UUID
    invalidated_at: dt.datetime
    affected_agent_count: int
    affected_edge_count: int
    living_holder_count: int
    db_snapshot_hlc: str  # AOST cross-check oracle for the certificate
    audit_id: uuid.UUID
    # The self-contained pre-kill record (same dict embedded + hashed in the certificate).
    pre_invalidation_state: PreInvalidationState
    # Certificate outcome (post-commit S3 side effect; never gates the invalidation).
    certificate_id: uuid.UUID | None
    certificate_s3_key: str | None
    certificate_status: str  # 'written' | 'failed'
    content_hash: str | None


# --- Frontend read surface (list endpoints) -----------------------------------


class AgentGenealogyOut(BaseModel):
    """One agent with the genealogy fields the frontend needs to render the tree.

    `parent_id` is the tree edge; generation/bloodline/status drive node rendering.
    """

    id: uuid.UUID
    generation: int
    bloodline: str
    status: str
    spawned_at: dt.datetime
    retired_at: dt.datetime | None
    parent_id: uuid.UUID | None


class AgentListResponse(BaseModel):
    # Full list, no pagination: the genealogy is bounded-small by design and the tree
    # needs every node at once.
    agents: list[AgentGenealogyOut]
    count: int


class DecisionOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    txn_ref: str
    merchant: str
    amount: float
    verdict: str
    driving_belief_id: uuid.UUID | None
    confidence: float
    decided_at: dt.datetime
    is_fraud: bool


class DecisionListResponse(BaseModel):
    # Paginated: decisions grows to thousands of rows. `agent_id` filter is OPTIONAL —
    # default is the fleet-wide feed (all agents), narrowing to one agent when provided.
    decisions: list[DecisionOut]
    total: int
    limit: int
    offset: int
    agent_id: uuid.UUID | None


class BeliefListResponse(BaseModel):
    # Full list, no pagination: founding beliefs are bounded-small by the data model.
    beliefs: list[BeliefOut]
    count: int


class BeliefPerformanceWindow(BaseModel):
    """One measured performance window for a belief — the real belief_performance columns.

    No synthetic 'generation' field: the table stores none. Windows are generation-ordered
    by window_start, so a window's ordinal position in the list IS its generation.
    """

    window_start: dt.datetime
    window_end: dt.datetime
    confidence: float
    false_positive_rate: float
    frauds_approved: int


class BeliefPerformanceResponse(BaseModel):
    # The ordered staleness curve for one belief — the "valid then / rotten now" signal,
    # MEASURED from belief_performance (never asserted). An empty `windows` for a real belief
    # means it has no measured windows yet (not an error); an unknown belief is a 404.
    belief_id: uuid.UUID
    windows: list[BeliefPerformanceWindow]
    count: int
