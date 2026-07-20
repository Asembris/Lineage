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


# --- Roadmap Item A: inheritance-provenance audit -----------------------------


class ProvenanceEdgeReport(BaseModel):
    """One belief_inheritance edge's provenance verdict.

    `verdict` is 'ok' | 'anomalous' | 'inconclusive'. `violated` lists the invariant codes
    an anomalous edge broke (A1 genealogy-consistency, A2 spawn-time, A3 source-was-a-holder,
    A4 not-post-invalidation). `evidence` carries the per-invariant specifics (expected vs
    actual values) for a forensic reader.
    """

    edge_id: uuid.UUID
    from_agent_id: uuid.UUID
    to_agent_id: uuid.UUID
    verdict: str
    violated: list[str]
    evidence: dict


class ProvenanceAuditResponse(BaseModel):
    """A read-only provenance-integrity verdict over a belief's inheritance closure.

    `status` is CLEAN (every edge legitimate) | ANOMALOUS (>=1 edge violates an invariant) |
    INCONCLUSIVE (an edge's backing data is missing — surfaced, never a silent pass). This is
    verification + out-of-band tamper detection: the legitimate write paths preserve A1..A4 by
    construction, so an anomaly means an edge was inserted by something other than the spawn
    lifecycle. See app/services/provenance_audit.py for the invariants and taxonomy framing.
    """

    belief_id: uuid.UUID
    belief_status: str
    origin_agent_id: uuid.UUID
    status: str
    edge_count: int
    anomaly_count: int
    edges: list[ProvenanceEdgeReport]
    anomalies: list[ProvenanceEdgeReport]


# --- Roadmap Item B: counterfactual "what-if invalidation" --------------------


class CounterfactualWindow(BaseModel):
    """One measured window's slice of the counterfactually-affected set.

    Windows are the BELIEF'S OWN `belief_performance` rows — not the crimson generation clock —
    so this lines up against exactly the staleness curve the belief actually has. A belief with no
    measured windows gets `windows: null` on the response, never a grid of zeros.
    """

    window_start: dt.datetime
    window_end: dt.datetime
    withdrawn_approvals: int
    withdrawn_blocks: int
    frauds_auto_approved: int


class CounterfactualResponse(BaseModel):
    """"If this belief had been invalidated at `at`, which downstream verdicts change?"

    Answered against the moat's `decisions` table — the only path carrying a real
    driving_belief_id link. `at` is BUSINESS-TIME (a decided_at instant), deliberately not the
    MVCC `as_of` clock: this is a plain query, no AOST, no content-hash.

    THE COUNTS ARE VERDICT-AWARE, and that is not cosmetic. Item B was built when the fleet's one
    belief only ever APPROVED, so it reported the affected row count as the withdrawn-approval
    count. The grounding seam's azure belief BLOCKS (a re-derived laundering cycle -> `blocked`),
    and under the old aggregate the endpoint reported those blocks as withdrawn approvals and
    counted correctly-blocked fraud as `frauds_auto_approved` — crediting the belief's catches as
    its harms, which is the worst possible direction for a forensic tool to be wrong in. So:

      withdrawn_approvals    (N) — affected rows the belief APPROVED; the approval loses its driver
      withdrawn_blocks           — affected rows the belief BLOCKED; the block loses its driver
      frauds_auto_approved   (M) — of the APPROVED rows, is_fraud: the real harm. Never "fraud we
                                   would have caught" — no fallback verdict is reproduced.
      frauds_caught_by_block     — of the BLOCKED rows, is_fraud: what invalidating would FORFEIT.
                                   M's counterweight; it exists so a correct block can never again
                                   be presented as a harm.

    See app/services/counterfactual.py for the full derivation and the measured before/after.
    """

    belief_id: uuid.UUID
    rule_text: str
    belief_status: str
    originating_agent_id: uuid.UUID
    formed_at: dt.datetime
    at: dt.datetime  # the counterfactual instant T (business-time), echoed as parsed
    total_belief_driven: int  # all belief-driven decisions, for context
    affected_decisions: int  # every driven row after T, whatever its verdict
    withdrawn_approvals: int  # N — of those, the APPROVALS
    withdrawn_blocks: int  # of those, the blocks/declines
    frauds_auto_approved: int  # M — of the approvals, is_fraud = true (real ground truth)
    frauds_caught_by_block: int  # of the blocks, is_fraud = true — the forfeited catches
    affected_holder_count: int
    affected_holders: list[uuid.UUID]  # distinct agents that made an affected decision
    # None when the belief has no measured belief_performance windows (e.g. the azure laundering
    # belief, whose decisions all share ONE decided_at by design). Explicitly absent, never zeros.
    windows: list[CounterfactualWindow] | None


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
    """A decision is either a Phase-2 CARD authorization or an AML decision grounded in a real
    IBM money-flow edge (migration 0006, the grounding seam).

    Three fields are nullable BECAUSE of that split, and each null is a refusal to fabricate:
      * `merchant`   — NULL for AML: a bank-to-bank transfer has no merchant.
      * `confidence` — NULL for AML: the structural witness is deterministic (and Item D proved
                       this column is noise anyway — "do not use this column for anything").
      * `amount_currency` — NULL for Phase-2 card rows: the simulator never declared a currency.
                       AML rows name their real one; the extract spans 14.
    A consumer distinguishes the two kinds by `aml_transaction_id is not None`.

    ===================== READ THIS BEFORE COUNTING AN AML `approve` =====================
    For an AML decision, `verdict` ALONE IS NOT THE STORY, and reading it as one will mislead you.
    TWO DIFFERENT WITNESS OUTCOMES BOTH MAP TO `approve`, and they are categorically different:

        witness_outcome   verdict    edges   share    of which laundering
        MATCH             blocked       57    3.8%     43   a real directed cycle, re-derived
        CONCLUSIVE_NO     approve      463   30.9%      5   no cycle — but only 16 of these were
                                                            SEARCHED; 447 are self-loops, where no
                                                            search was ever possible (see below)
        INCONCLUSIVE      approve      980   65.3%    252   COULD NOT TELL — the search ran off
                                                            the edge of the 1,500-edge extract

    So of the belief's 1,443 approvals, **980 (65.3% of the whole extract) are not "this is clean" —
    they are "we could not determine", silently approving 252 of the extract's 300 laundering rows.**

    AND `CONCLUSIVE_NO` IS NOT ONE THING. Its long-standing gloss — *"searched; there is no cycle"* —
    is true of **16** of the 463. The other **447 are SELF-LOOPS**: an account paying itself, which is
    not a transfer between two accounts, so `aml_graph.Graph` excludes it from adjacency by
    construction and **no search ever ran**. The outcome count was never wrong; its description of
    itself was. The basis is therefore FOUR-way, and the wire already carries the distinction —
    `GET /aml/transactions/{id}/interrogate` serves a `detail` string per witness:

        447x  CONCLUSIVE_NO  "self-loop is not a transfer cycle"          <- no search possible
         16x  CONCLUSIVE_NO  "no return path; search closed inside the extract"
        980x  INCONCLUSIVE   "search reached an account whose outgoing edges are not in this extract"

    `witness_outcome` below stays THREE-valued on purpose: self-loop-vs-closed-search is a property of
    the EVIDENCE, re-derived from the graph, not of what the agent RECORDED. Serving it here would be
    the decision surface re-deriving a fact about the evidence layer. It belongs to /interrogate.
    (Measured by scripts/probe_conclusive_no.py; asserted by tests/test_decision_read_surface.py::
    test_the_conclusive_no_decomposition_is_447_selfloops_and_16_closed_searches.)
    Mapping "cannot corroborate" -> `approve` is a DISCLOSED MODELING CHOICE (a real system lets a
    payment through absent evidence), not a corner case, and this proportion must travel with every
    quote of these decisions.

    `witness_outcome` exists to make that legible FROM THE RESPONSE ALONE. The information was always
    in `txn_ref` (`aml:INCONCLUSIVE`), but only as an undocumented string convention inside a column
    named "the transaction reference" — recoverable only by a caller who already knew to look, which
    is exactly the failure the tag was meant to prevent. Migration 0008 makes the tag structural
    (`ck_decisions_kind`), so this field is TOTAL for AML rows and NULL for card rows, never absent
    because a writer forgot. See app/services/aml_seam.py and NOTES.md "THE BASE-RATE MIRAGE".
    """

    id: uuid.UUID
    agent_id: uuid.UUID
    txn_ref: str
    merchant: str | None
    amount: float
    amount_currency: str | None
    verdict: str
    driving_belief_id: uuid.UUID | None
    confidence: float | None
    decided_at: dt.datetime
    is_fraud: bool
    # The grounding seam: the REAL aml_transactions row this decision was made about.
    aml_transaction_id: uuid.UUID | None
    # MATCH | CONCLUSIVE_NO | INCONCLUSIVE for an AML decision; NULL for a card decision.
    # A PROJECTION of the persisted `txn_ref` — what the agent RECORDED at decision time, never a
    # fresh re-run of the witness against today's graph. Those are different objects and this
    # surface deliberately serves only the first. (To re-derive the witness NOW, which is a
    # different question, call GET /aml/transactions/{id}/interrogate.)
    witness_outcome: str | None = None


class DecisionListResponse(BaseModel):
    """The decision feed. Paginated — `decisions` is the one table in the moat designed to grow.

    Every filter is optional and they AND together. `aml_transaction_id` is the REVERSE LOOKUP:
    the seam's FK resolves decision -> transaction, and this resolves transaction -> decision
    ("did any agent act on this money-flow edge, and what did it decide?"). `total: 0` is the
    honest answer for a transaction no agent ever ruled on.

    `total` WITH `witness_outcome` + `is_fraud` IS AN AGGREGATE, and deliberately so: seven calls at
    `limit=1` yield the seam's entire census (1,500 / 57 / 463 / 980; 43 / 5 / 252) without pulling a
    single row. The console's honesty ledger reads the 65.3% disclosure that way — LIVE from the
    cluster — because this exact number has twice been corrupted by prose (misstated once, falsely
    sourced once), and a number read from the data cannot be wrong about the data.
    """

    decisions: list[DecisionOut]
    total: int
    limit: int
    offset: int
    # The filters, echoed back as parsed, so a response is self-describing.
    agent_id: uuid.UUID | None
    aml_transaction_id: uuid.UUID | None = None
    driving_belief_id: uuid.UUID | None = None
    kind: str | None = None
    witness_outcome: str | None = None
    is_fraud: bool | None = None


class BeliefListResponse(BaseModel):
    # Full list, no pagination: founding beliefs are bounded-small by the data model.
    beliefs: list[BeliefOut]
    count: int


class BeliefPerformanceWindow(BaseModel):
    """One measured performance window for a belief — the real belief_performance columns.

    No synthetic 'generation' field: the table stores none. Windows are generation-ordered
    by window_start, so a window's ordinal position in the list IS its generation.

    `sample_size` is the number of belief-driven decisions the window's confidence was
    aggregated over. belief_performance persists NO denominator, so it is re-aggregated from
    `decisions` at read time (a deterministic aggregate over immutable columns — no migration,
    no change to the five-table moat). The Wilson bounds are null whenever the sample cannot be
    trusted (see StalenessUncertainty.sample_agreement) — withheld, never faked.

    NOTE `false_positive_rate` deliberately has NO interval: it is structurally 0 for a belief
    that only ever approves, and an interval there would dress a structural impossibility as an
    uncertain estimate.
    """

    window_start: dt.datetime
    window_end: dt.datetime
    confidence: float
    false_positive_rate: float
    frauds_approved: int
    sample_size: int
    confidence_ci_low: float | None = None
    confidence_ci_high: float | None = None


class StalenessUncertainty(BaseModel):
    """How much to trust the curve above — the house's honest "cannot determine" state.

    `sample_agreement`:
      * `agreed`      — every window's persisted confidence reproduces from the decisions it
                        summarizes; the intervals are real and are emitted.
      * `disagreed`   — a persisted confidence does NOT reproduce, so belief_performance is
                        stale w.r.t. `decisions`. Intervals withheld: a fresh denominator paired
                        with a stale point estimate is exactly the confidently-wrong number.
      * `unavailable` — a window has no decisions to aggregate. No interval is derivable.

    `decay_supported` — whether the "valid then / rotten now" claim survives its own uncertainty:
    a two-sided FISHER EXACT test on the first vs. last window rejects "same rate" at 95%, AND
    the movement is downward. There is no minimum-sample-size gate (that would repeat the
    rejected MARGIN_FLOOR); Fisher is exact at every n, so a thin window disqualifies itself.
    Comparing the intervals for overlap would NOT have done this — it calls a single wrongly-
    decided decision a supported decay. See certificate.staleness_evidence.
    """

    method: str
    confidence_level: float
    sample_source: str
    sample_agreement: str
    decay_supported: bool | None = None
    decay_p_value: float | None = None
    decay_support_criterion: str


class BeliefPerformanceResponse(BaseModel):
    # The ordered staleness curve for one belief — the "valid then / rotten now" signal,
    # MEASURED from belief_performance (never asserted), now carrying the sample sizes and
    # intervals behind each point estimate. An empty `windows` for a real belief means it has no
    # measured windows yet (not an error), and `uncertainty` is then null; an unknown belief is
    # a 404. Same block the certificate embeds — one instrument, so the console and the
    # hash-covered document can never state different intervals for the same belief.
    belief_id: uuid.UUID
    windows: list[BeliefPerformanceWindow]
    count: int
    uncertainty: StalenessUncertainty | None = None


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


class LiveDecisionBelief(BaseModel):
    """The inherited memory the live decision acted on, and where it came from."""

    id: uuid.UUID
    rule_text: str
    formed_at: dt.datetime
    status: str
    originating_agent_id: uuid.UUID
    originating_agent_generation: int
    originating_agent_bloodline: str
    # 'dead' for azure-0. The living decider never met the agent whose rule it just applied.
    originating_agent_status: str
    inheritance_edge_count: int


class LiveDecisionAgent(BaseModel):
    id: uuid.UUID
    generation: int
    bloodline: str
    status: str


class LiveDecisionResponse(BaseModel):
    """One LIVE governed decision: the deterministic witness ran just now and a real row was written.

    ================== READ `is_new_row` EXACTLY AS IT IS NAMED ==================
    `is_new_row` is TRUE OF THE ROW, and only of the row. It does NOT mean "no agent had ever ruled
    on this transaction": on the seeded world every one of the 1,500 ingested edges already carries a
    BACKFILLED decision, so that stronger claim would be simply false. `prior_decisions_for_this_
    transaction` says how many rulings already existed, and `prior_decision_ids` names them — the
    response discloses the prior row rather than concealing it.

    The honest narration is therefore **"this ROW was written just now, and it agrees with the prior
    verdict"** — which is a DEMONSTRATION OF DETERMINISM, not a novelty claim. The same frozen,
    label-free witness re-derived the same verdict from the same unlabeled graph, live, on request.
    `verdict_agrees_with_prior` states that as data (null when there is genuinely no prior).

    ============================ WHAT `is_fraud` IS HERE ============================
    An AUDIT STAMP, attached in a strictly separate second phase AFTER the verdict existed, only
    because `decisions.is_fraud` is NOT NULL. The decider never saw it and structurally cannot:
    `decide()` takes a `Graph` and an `Edge`, and neither type has anywhere to put a label
    (tests/test_oracle_boundary.py). Reading it back beside the verdict is scoring, not deciding.

    And the verdict alone is not the story — `witness_outcome` is the BASIS. Two outcomes map to
    `approve`, and the larger one (INCONCLUSIVE, 65.3% of the extract) means "we could not tell",
    not "this is clean". See DecisionOut's docstring before quoting any AML approval.
    """

    decision_id: uuid.UUID
    # True of the ROW this call inserted. See the class docstring — this is not a novelty claim.
    is_new_row: bool
    row_written_at: dt.datetime
    decided_at: dt.datetime

    transaction_id: uuid.UUID
    verdict: str
    witness_outcome: str
    # The real `aml_transactions` ids the witness cites — re-derivable, each resolvable through
    # GET /aml/transactions/{id}. Empty unless the outcome is MATCH.
    witness_txn_ids: list[uuid.UUID]
    boundary_account: uuid.UUID | None
    txn_ref: str
    amount: float
    amount_currency: str

    prior_decisions_for_this_transaction: int
    prior_decision_ids: list[uuid.UUID]
    # None when there is no prior decision at all; otherwise whether this live verdict matches the
    # most recent one. On the seeded world this is the determinism beat.
    verdict_agrees_with_prior: bool | None

    belief: LiveDecisionBelief
    deciding_agent: LiveDecisionAgent
    is_fraud: bool
