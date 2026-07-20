# READS THE ORACLE, AND IS ALLOWLISTED FOR IT — under exactly the backfill's terms. The label
# appears in this module in ONE place: the value of `_LABELS_SQL`, queried strictly AFTER the
# verdict already exists. tests/test_oracle_boundary.py::
# test_the_live_decider_reads_the_label_only_to_attach_ground_truth pins that, and the module is
# in DECIDING_PATH so the walk actually visits it. An unwalked label-read is the hole; this is not.
"""THE LIVE GOVERNED DECISION (J1): azure-7 decides one real money-flow edge, NOW.

`seed/backfill_aml_decisions.py` proved the seam can be *backfilled*. This proves the living agent
can *act*: an HTTP request drives the SAME frozen, label-free witness over the SAME unlabeled graph
and writes a real `decisions` row into the joined two-graph memory. The row is indistinguishable in
schema from a backfilled one — same FK citation, same basis tag, same NULL merchant/confidence —
except for `decided_at`, which is now.

    THE DECIDER IS `aml_seam.decide`. IT IS NOT, AND MUST NEVER BECOME, THE LLM.
    `aml_agent.evaluate_transaction()` makes a paid, non-deterministic OpenAI call and has ZERO
    application callers (tests/test_aml_routes.py asserts this statically over the whole `app`
    package). Nothing here needs it: the witness is free, deterministic, and offline-replayable,
    and "determinism is deliberate" is the seam's whole story. Wiring a model into this path would
    not make the system more agentic — it would make the one governed write unverifiable.

WHY THIS ROUTE IS NOT UNDER `/aml`
-----------------------------------
`tests/test_aml_routes.py` asserts every `/aml` route is GET-or-HEAD: the EVIDENCE layer is static
reference data and nothing about it is written by a request. That guard is load-bearing and is not
weakened here — this write is a MOAT operation (it inserts into `decisions`), so it lives on the
decisions surface, which is exactly the argument `app/routers/decisions.py` already makes for
putting the reverse lookup there rather than on `/aml`.

TWO PHASES, THE BACKFILL'S DISCIPLINE, FOR THE SAME REASON
-----------------------------------------------------------
  Phase 1 — decide.  `decide(graph, edge)` over a `Graph` built by a SELECT that projects no label.
                     The label query has not run and CANNOT have influenced the verdict.
  Phase 2 — label.   ONLY THEN is `is_laundering` read, and only to stamp `decisions.is_fraud` —
                     an AUDIT fact attached after the fact, never an input.

`decisions.is_fraud` is NOT NULL, so a live row must carry a label; that is the only reason this
module touches the answer key at all. The alternative (a migration making `is_fraud` nullable for
live rows) was considered and rejected: it would change the moat's shape and make a live row
schema-DISTINGUISHABLE from a backfilled one, which is the one property this route exists to have.

THE GOVERNANCE PRECONDITIONS — WHY A DECISION CAN BE REFUSED
-------------------------------------------------------------
A decision is only "an agent acting on inherited memory" if the memory is live and the agent really
holds it. Three checks, all against real rows, all before any write:

  * THE BELIEF MUST BE ACTIVE. If it has been invalidated, this route REFUSES (409). This is the
    Phase-3 kill-shot made observable at the agent's own hands: run POST /beliefs/{id}/invalidate,
    then watch the living agent become UNABLE to act on the belief it inherited. The atomic
    invalidation stops being a number in a certificate and becomes a behaviour change.
  * THE AGENT MUST BE ALIVE. A retired agent does not decide.
  * THE AGENT MUST ACTUALLY HOLD THE BELIEF — a real `belief_inheritance` edge to it, not yet
    revoked (`invalidated_at IS NULL`). This is the provenance requirement CLAUDE.md calls
    paraphrase-free: no decision may cite a belief its author never inherited.

The holder check subsumes the status check today (the atomic invalidation closes every edge in the
same commit), and both are kept anyway: they fail for different reasons and say so differently, and
a future partial-revocation would separate them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.db import engine
from app.services.aml_graph import load_graph
from app.services.aml_seam import decide, txn_ref_for
from seed.seed import aid, bid

# The belief, and the LIVING holder that acts on it. Same two the backfill used — this route is the
# same agent doing the same job, one edge at a time, on request.
AML_BELIEF = bid("aml-cycle")
DECIDING_AGENT = aid("azure-7")


class LiveDecideError(Exception):
    """Base for a refusal the router turns into a status code, never a 500."""


class TransactionNotFound(LiveDecideError):
    """The subject is not in the AML evidence layer -> 404."""


class BeliefInvalidated(LiveDecideError):
    """The belief has been invalidated: the agent may no longer act on it -> 409."""


class AgentNotAHolder(LiveDecideError):
    """The agent is dead, or holds no live inheritance edge for this belief -> 409."""


# ---------------------------------------------------------------------------------------------
# PHASE 2 ONLY. This module's SOLE reference to the answer key, and it runs strictly after the
# verdict already exists. The label is ALIASED on the way out so the oracle's name appears EXACTLY
# ONCE in this file — here — which is what lets the pinned test in tests/test_oracle_boundary.py
# assert the read is this constant and nothing else.
# ---------------------------------------------------------------------------------------------
_LABELS_SQL = text("SELECT is_laundering AS ground_truth FROM aml_transactions WHERE id = :id")

# Phase-1 inputs: row facts the WITNESS does not use. `payment_currency` is required by migration
# 0007's ck_decisions_kind (an AML row must name its currency; the extract spans 14). Not a label.
_FACTS_SQL = text("SELECT amount_paid, payment_currency FROM aml_transactions WHERE id = :id")

# The governance preconditions, in one round-trip. `holds` counts LIVE inheritance edges to the
# deciding agent (`invalidated_at IS NULL` — the atomic invalidation closes them all in one commit).
_GOVERNANCE_SQL = text(
    """
    SELECT
      b.status                                                             AS belief_status,
      b.rule_text, b.formed_at, b.originating_agent_id,
      oa.generation AS origin_generation, oa.bloodline AS origin_bloodline,
      oa.status     AS origin_status,
      a.generation  AS decider_generation, a.bloodline AS decider_bloodline,
      a.status      AS decider_status,
      (SELECT count(*) FROM belief_inheritance WHERE belief_id = b.id)     AS edge_count,
      (SELECT count(*) FROM belief_inheritance
         WHERE belief_id = b.id AND to_agent_id = :agent
           AND invalidated_at IS NULL)                                     AS live_edges_to_agent
    FROM beliefs b
    JOIN agents oa ON oa.id = b.originating_agent_id
    JOIN agents a  ON a.id  = :agent
    WHERE b.id = :belief
    """
)

# The prior decisions about this same edge. Served through migration 0008's partial index
# (ix_decisions_aml_txn) — this is the reverse lookup that index exists for.
_PRIOR_SQL = text(
    """
    SELECT id, verdict, txn_ref, decided_at
    FROM decisions
    WHERE aml_transaction_id = :id
    ORDER BY decided_at DESC, id DESC
    """
)

_INSERT_SQL = text(
    """
    INSERT INTO decisions
      (id, agent_id, txn_ref, merchant, amount, amount_currency, verdict,
       driving_belief_id, confidence, decided_at, is_fraud, aml_transaction_id)
    VALUES
      (:id, :agent_id, :txn_ref, NULL, :amount, :amount_currency, :verdict,
       :driving_belief_id, NULL, :decided_at, :is_fraud, :aml_transaction_id)
    """
)


@dataclass(frozen=True)
class PriorDecision:
    id: uuid.UUID
    verdict: str
    txn_ref: str
    decided_at: dt.datetime


async def decide_live(txn_id: uuid.UUID) -> dict:
    """Run the frozen witness over one real edge NOW and persist the verdict as a real decision.

    Returns the full narration payload (see app/schemas.py::LiveDecisionResponse). Raises
    TransactionNotFound / BeliefInvalidated / AgentNotAHolder for the router to translate.
    """
    async with engine.connect() as c:
        # ---- Governance. Refuse before deciding: a refused call must write nothing and must
        # ---- not spend a graph load either.
        gov = (
            await c.execute(
                _GOVERNANCE_SQL, {"belief": AML_BELIEF, "agent": DECIDING_AGENT}
            )
        ).mappings().one()

        if gov["belief_status"] != "active":
            raise BeliefInvalidated(
                f"belief {AML_BELIEF} is '{gov['belief_status']}', not 'active' — the agent may no "
                "longer act on it. This is the atomic invalidation taking effect at the agent's "
                "hands, not an error."
            )
        if gov["decider_status"] != "alive":
            raise AgentNotAHolder(
                f"agent {DECIDING_AGENT} is '{gov['decider_status']}' — a retired agent does not decide"
            )
        if not gov["live_edges_to_agent"]:
            raise AgentNotAHolder(
                f"agent {DECIDING_AGENT} holds no live belief_inheritance edge for belief "
                f"{AML_BELIEF} — it never inherited it, or its edge has been revoked"
            )

        # ---- PHASE 1: DECIDE. The label query has not run; it cannot have influenced anything.
        graph = await load_graph(conn=c)  # SELECTs no label column
        edge = graph.by_id.get(txn_id)
        if edge is None:
            raise TransactionNotFound(str(txn_id))

        seam = decide(graph, edge)  # pure; sees no label, by type

        facts = (await c.execute(_FACTS_SQL, {"id": txn_id})).mappings().one()
        prior = [
            PriorDecision(r["id"], r["verdict"], r["txn_ref"], r["decided_at"])
            for r in (await c.execute(_PRIOR_SQL, {"id": txn_id})).mappings().all()
        ]

        # ---- PHASE 2: LABEL. Ground truth the witness never saw, attached after the fact so the
        # ---- NOT NULL `is_fraud` column can be honoured. An audit stamp, never an input.
        ground_truth = bool(
            (await c.execute(_LABELS_SQL, {"id": txn_id})).mappings().one()["ground_truth"]
        )

    decided_at = dt.datetime.now(dt.timezone.utc)
    # A FRESH id, never the backfill's `uuid5(NAMESPACE_OID, f"aml-decision:{txn_id}")` — that one
    # is already taken for every one of the 1,500 edges, so reusing it is a primary-key collision.
    decision_id = uuid.uuid4()

    async with engine.begin() as c:
        await c.execute(
            _INSERT_SQL,
            {
                "id": decision_id,
                "agent_id": DECIDING_AGENT,
                "txn_ref": txn_ref_for(seam.witness_outcome),  # 0008 makes anything else unwritable
                "amount": facts["amount_paid"],
                "amount_currency": facts["payment_currency"],
                "verdict": seam.verdict,
                "driving_belief_id": AML_BELIEF,
                "decided_at": decided_at,
                "is_fraud": ground_truth,
                "aml_transaction_id": txn_id,
            },
        )

    return {
        "decision_id": decision_id,
        # TRUE OF THE ROW, AND ONLY OF THE ROW. See the schema's docstring: on the seeded world
        # every one of the 1,500 edges already carries a backfilled decision, so "this decision did
        # not exist before" would be FALSE. What this call created is a ROW.
        "is_new_row": True,
        "row_written_at": decided_at,
        "decided_at": decided_at,
        "transaction_id": txn_id,
        "verdict": seam.verdict,
        "witness_outcome": seam.witness_outcome.value,
        "witness_txn_ids": list(seam.witness_txn_ids),
        "boundary_account": seam.boundary_account,
        "txn_ref": txn_ref_for(seam.witness_outcome),
        "amount": float(facts["amount_paid"]),
        "amount_currency": facts["payment_currency"],
        # The determinism beat, stated as data rather than as a claim.
        "prior_decisions_for_this_transaction": len(prior),
        "prior_decision_ids": [p.id for p in prior],
        "verdict_agrees_with_prior": (prior[0].verdict == seam.verdict) if prior else None,
        "belief": {
            "id": AML_BELIEF,
            "rule_text": gov["rule_text"],
            "formed_at": gov["formed_at"],
            "status": gov["belief_status"],
            "originating_agent_id": gov["originating_agent_id"],
            "originating_agent_generation": gov["origin_generation"],
            "originating_agent_bloodline": gov["origin_bloodline"],
            "originating_agent_status": gov["origin_status"],
            "inheritance_edge_count": gov["edge_count"],
        },
        "deciding_agent": {
            "id": DECIDING_AGENT,
            "generation": gov["decider_generation"],
            "bloodline": gov["decider_bloodline"],
            "status": gov["decider_status"],
        },
        "is_fraud": ground_truth,
    }
