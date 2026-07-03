"""GET /decisions — the fraud-decision feed (paginated, optionally filtered by agent).

The frontend's decision panel wants the FLEET-WIDE feed by default (all agents, newest first);
passing ?agent_id narrows to one agent's history for the investigate flow. Paginated because
`decisions` grows to thousands of rows (unlike the bounded agent/belief lists).
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
    limit: int = Query(50, ge=1, le=200, description="Page size (max 200)."),
    offset: int = Query(0, ge=0, description="Rows to skip."),
) -> DecisionListResponse:
    rows, total = await catalog.list_decisions(agent_id, limit=limit, offset=offset)
    return DecisionListResponse(
        decisions=[DecisionOut(**r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        agent_id=agent_id,
    )
