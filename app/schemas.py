"""Pydantic response DTOs (Phase 1 — kept light; full validation is Phase 4)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel


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
