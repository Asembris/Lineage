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
