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


# --- Phase 3: atomic invalidation ---------------------------------------------


class InvalidateRequest(BaseModel):
    actor_id: uuid.UUID  # the supervisor / agent authorizing the invalidation


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
    # Certificate outcome (post-commit S3 side effect; never gates the invalidation).
    certificate_id: uuid.UUID | None
    certificate_s3_key: str | None
    certificate_status: str  # 'written' | 'failed'
    content_hash: str | None
