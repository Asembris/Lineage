"""GET /agents/{id}/beliefs?as_of=  — real AS OF SYSTEM TIME deposition."""

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.schemas import AgentBeliefsResponse
from app.services import time_travel

router = APIRouter(tags=["agents"])


@router.get("/agents/{agent_id}/beliefs", response_model=AgentBeliefsResponse)
async def get_agent_beliefs(
    agent_id: uuid.UUID,
    as_of: str | None = Query(
        None,
        description="Time-travel point: ISO-8601 timestamp or CRDB HLC decimal. "
        "Omit for current state.",
    ),
) -> AgentBeliefsResponse:
    agent = await time_travel.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    try:
        beliefs = await time_travel.beliefs_held_by_agent(agent_id, as_of)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AgentBeliefsResponse(agent=agent, as_of=as_of, beliefs=beliefs)
