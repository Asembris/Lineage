"""GET /decisions — the fraud-decision feed (paginated, filterable).

The frontend's decision panel wants the FLEET-WIDE feed by default (all agents, newest first);
the filters narrow it. Paginated because `decisions` grows to thousands of rows (unlike the
bounded agent/belief lists, which are served whole).

THE REVERSE LOOKUP LIVES HERE, NOT ON /aml — and that placement is deliberate. "Did any agent act
on this transaction?" is a question about the MOAT (`decisions`), and a decision carries `is_fraud`.
The /aml surface is the EVIDENCE layer's, and Item 5 built it claim-free and label-free on purpose:
its witness is only meaningful because it never saw a label, so hanging a route that serves
`is_fraud` off `/aml/transactions/{id}/...` would put the answer key one hop from the witness's own
work, under the prefix whose whole discipline is that it does not go there. See NOTES.md "G5".
"""

import uuid

from fastapi import APIRouter, Query

from app.schemas import DecisionListResponse, DecisionOut
from app.services import catalog

router = APIRouter(tags=["decisions"])


@router.get("/decisions", response_model=DecisionListResponse)
async def list_decisions(
    agent_id: uuid.UUID | None = Query(
        None, description="Narrow to one agent's history. Omit for the fleet-wide feed."
    ),
    aml_transaction_id: uuid.UUID | None = Query(
        None,
        description="THE REVERSE LOOKUP: the decision(s) made about one real AML money-flow edge. "
        "The grounding seam's FK resolves decision -> transaction; this resolves the other way. "
        "`total: 0` means no agent ever ruled on that transaction.",
    ),
    driving_belief_id: uuid.UUID | None = Query(
        None, description="Every decision this belief drove."
    ),
    kind: str | None = Query(
        None,
        pattern="^(aml|card)$",
        description="'aml' = grounded in a real IBM money-flow edge; 'card' = a Phase-2 card "
        "authorization. The two kinds migration 0007's ck_decisions_kind makes structural. "
        "Anything else -> 422, never a silent empty page.",
    ),
    witness_outcome: str | None = Query(
        None,
        pattern="^(MATCH|CONCLUSIVE_NO|INCONCLUSIVE)$",
        description="The BASIS of an AML decision. Pair with `limit=1` and read `total` to COUNT "
        "an outcome: this is what makes the 65.3% disclosure countable rather than merely readable "
        "(1,500 / 57 / 463 / 980, and with is_fraud=true, 43 / 5 / 252). Anything else -> 422.",
    ),
    is_fraud: bool | None = Query(
        None,
        description="The recorded ground truth. An AUDIT fact — the label attached to a decision "
        "AFTER it was made, never readable by the decider (see the oracle boundary).",
    ),
    limit: int = Query(50, ge=1, le=200, description="Page size (max 200)."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> DecisionListResponse:
    """The decision feed. Filters AND together.

    Each AML row carries `witness_outcome` (MATCH | CONCLUSIVE_NO | INCONCLUSIVE) — read
    DecisionOut's docstring before counting an AML `approve`: two different witness outcomes both
    map to `approve`, and 65.3% of the extract is the one that means "we could not tell".
    """
    rows, total = await catalog.list_decisions(
        agent_id,
        aml_transaction_id=aml_transaction_id,
        driving_belief_id=driving_belief_id,
        kind=kind,
        witness_outcome=witness_outcome,
        is_fraud=is_fraud,
        limit=limit,
        offset=offset,
    )
    return DecisionListResponse(
        decisions=[DecisionOut(**r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        agent_id=agent_id,
        aml_transaction_id=aml_transaction_id,
        driving_belief_id=driving_belief_id,
        kind=kind,
        witness_outcome=witness_outcome,
        is_fraud=is_fraud,
    )
