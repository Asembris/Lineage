"""Agent endpoints — genealogy list + AS OF SYSTEM TIME deposition."""

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.schemas import AgentBeliefsResponse, AgentGenealogyOut, AgentListResponse
from app.services import catalog, time_travel

router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=AgentListResponse)
async def list_agents(
    bloodline: str | None = Query(None, description="Filter to one bloodline."),
    status: str | None = Query(None, description="Filter by status ('alive' | 'dead')."),
) -> AgentListResponse:
    """The full genealogy — every agent with its parent edge — for the tree view.

    Returned in full (no pagination): rendering the tree needs every node, and the
    genealogy is bounded-small by the data model.
    """
    rows = await catalog.list_agents(bloodline=bloodline, status=status)
    agents = [AgentGenealogyOut(**r) for r in rows]
    return AgentListResponse(agents=agents, count=len(agents))


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
