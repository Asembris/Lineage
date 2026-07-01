"""GET /beliefs/{id}/lineage — traverse belief_inheritance back to the origin."""

import uuid

from fastapi import APIRouter, HTTPException

from app.schemas import LineageResponse
from app.services import lineage

router = APIRouter(tags=["beliefs"])


@router.get("/beliefs/{belief_id}/lineage", response_model=LineageResponse)
async def get_belief_lineage(belief_id: uuid.UUID) -> LineageResponse:
    result = await lineage.belief_lineage(belief_id)
    if result is None:
        raise HTTPException(status_code=404, detail="belief not found")
    return LineageResponse(**result)
