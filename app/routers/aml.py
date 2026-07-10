"""AML evidence-layer read surface — click-to-interrogate (Roadmap Item 5).

READ-ONLY, and deliberately so. Two things are NOT here:

  * No POST. Nothing in the evidence layer is written by a request; `aml_*` is static
    reference data loaded once by scripts/ingest_aml.py.
  * No LLM verdict route. `aml_agent.evaluate_transaction()` makes a paid OpenAI call, and
    putting a paid, non-deterministic call behind a GET is a separate decision with its own
    cost and caching questions. It stays a callable for Item F to invoke deliberately. What is
    exposed here is the deterministic half: free, replayable, and safe to hammer.

No label column is served. `is_laundering` and pattern membership are the answer key.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.schemas import AmlInterrogationResponse, AmlTransactionOut
from app.services import aml_interrogate

router = APIRouter(prefix="/aml", tags=["aml"])


@router.get("/transactions/{txn_id}", response_model=AmlTransactionOut)
async def get_transaction(txn_id: uuid.UUID) -> AmlTransactionOut:
    """One real money-flow edge, resolved to its row. Unknown id -> 404."""
    result = await aml_interrogate.interrogate_transaction(txn_id)
    if result is None:
        raise HTTPException(status_code=404, detail="transaction not in the AML evidence layer")
    return AmlTransactionOut(**result["subject"])


@router.get("/transactions/{txn_id}/interrogate", response_model=AmlInterrogationResponse)
async def interrogate_transaction(
    txn_id: uuid.UUID,
    as_of: str | None = Query(
        None,
        description="Interrogate AS OF a past instant: ISO-8601 timestamp or CRDB HLC decimal. "
        "Omit for current state. Bounded to the AOST window (~75 min); older -> 400.",
    ),
) -> AmlInterrogationResponse:
    """What structure the graph witnesses around one transaction, and in what order.

    Runs all four typology witnesses against the subject, re-derives each witness's traversal
    from the rows (only CYCLE has a linear order — see aml_interrogate), and resolves every
    referenced transaction and account to a real row. `competing_typologies` surfaces the case
    where more than one typology witnesses the same edge.

    Real CockroachDB time-travel: `as_of` pins the graph AND the row resolution to one MVCC
    snapshot. Malformed / out-of-window -> 400, never a 500. Unknown transaction -> 404.
    """
    try:
        result = await aml_interrogate.interrogate_transaction(txn_id, as_of=as_of)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="transaction not in the AML evidence layer")

    return AmlInterrogationResponse(
        transaction_id=result["transaction_id"],
        subject=AmlTransactionOut(**result["subject"]),
        witnesses=[
            {
                "typology": w.typology,
                "flag_capable": w.flag_capable,
                "outcome": w.outcome.value,
                "kind": w.kind.value,
                "transaction_ids": w.transaction_ids,
                "legs": w.legs,
                "boundary_account_id": w.boundary_account_id,
                "detail": w.detail,
            }
            for w in result["witnesses"]
        ],
        competing_typologies=result["competing_typologies"],
        has_competing_structure=result["has_competing_structure"],
        transactions={k: AmlTransactionOut(**v) for k, v in result["transactions"].items()},
        accounts=result["accounts"],
        as_of=result["as_of"],
    )
