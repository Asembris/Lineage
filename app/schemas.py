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
    # sha256 of the pre-kill world (belief row + every closure edge's revocation state) as of
    # snapshot_hlc — the same digest GET /beliefs/{id}/replay returns. Null when the replay was
    # unavailable, or on a 'derived' pre_state that has no reconstructed world behind it.
    closure_content_hash: str | None = None
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


# --- AML evidence layer — read-only interrogation surface (Roadmap Item 5) ------------------
# Deliberately carries NO label field. `is_laundering` / pattern membership are the answer key
# and are never served: app/services/aml_interrogate.py selects neither.


class AmlAccountOut(BaseModel):
    # Node identity in the money-flow graph is the compound (bank, account), never account alone.
    id: uuid.UUID
    bank: str
    account: str


class AmlTransactionOut(BaseModel):
    # An EDGE of the money-flow graph: it runs between two accounts.
    id: uuid.UUID
    ts: dt.datetime
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_paid: float
    payment_currency: str
    amount_received: float
    receiving_currency: str
    payment_format: str


class AmlWitnessOut(BaseModel):
    """One typology's structural verdict on the subject, with its traversal re-derived.

    `kind` tells a client whether `transaction_ids` means anything as an order:
      RING   — CYCLE: contiguous and closed; ids are the walk, subject first.
      LEGS   — SCATTER-GATHER: use `legs`; `transaction_ids` is a set, not a path.
      BUNDLE — GATHER-SCATTER / STACK: a real edge set with no single traversal.
      NONE   — no witness.
    """

    typology: str
    flag_capable: bool
    outcome: str
    kind: str
    transaction_ids: list[uuid.UUID]
    legs: dict[str, list[uuid.UUID]] | None
    boundary_account_id: uuid.UUID | None
    detail: str


class AmlInterrogationResponse(BaseModel):
    """Everything a click on one transaction resolves to. Deterministic; no model call.

    `competing_typologies` lists every typology that independently witnesses this subject.
    More than one (`has_competing_structure`) means the structural evidence genuinely supports
    more than one story. It is reported, never used to suppress a verdict.
    """

    transaction_id: uuid.UUID
    subject: AmlTransactionOut
    witnesses: list[AmlWitnessOut]
    competing_typologies: list[str]
    has_competing_structure: bool
    # Every transaction/account referenced above, resolved to a real row so a client renders
    # the witness without a second round-trip per id.
    transactions: dict[uuid.UUID, AmlTransactionOut]
    accounts: dict[uuid.UUID, AmlAccountOut]
    as_of: str | None
