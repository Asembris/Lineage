"""The fraud agent's scoring brain (Phase 2 live layer — real OpenAI).

score_transaction() is how a LIVING agent acts on a transaction using the beliefs it holds
(originated ∪ inherited, active only):

  1. Retrieve — embed the transaction and rank the agent's held beliefs by CockroachDB vector
     similarity (cosine, C-SPANN index on beliefs.embedding). This is the distributed-vector
     leg of the thesis: the agent surfaces the belief most relevant to what it's seeing.
  2. Decide  — a real OpenAI chat call with STRICT structured JSON output returns a verdict,
     a confidence, which belief drove the call, and a rationale. The model applies the belief;
     it never sees the transaction's ground-truth fraud label.
  3. Persist — write a `decisions` row. `verdict` is the agent's guess; `is_fraud` is reality
     (from the world). Their later comparison is what makes belief performance measurable.

This is the ONLY place OpenAI drives a decision. The historical backfill is deterministic and
OpenAI-free; this layer demonstrates the genuine agent on the current window.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Decision
from app.services.embeddings import embed_text, transaction_text
from app.services.openai_client import get_openai

_VERDICT_SCHEMA = {
    "name": "fraud_verdict",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["approve", "decline", "blocked"]},
            "confidence": {"type": "number", "description": "0..1 confidence in the verdict"},
            "driving_belief_id": {
                "type": ["string", "null"],
                "description": "exact id of the belief that drove the verdict, or null",
            },
            "rationale": {"type": "string"},
        },
        "required": ["verdict", "confidence", "driving_belief_id", "rationale"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are a fraud-detection agent. You approve, decline, or block card transactions "
    "using ONLY the beliefs you have inherited from your ancestors. Each belief is a rule "
    "you must apply faithfully — you act on inherited knowledge, even from agents you never "
    "met. Choose exactly one verdict. If a specific belief drove your verdict, return its "
    "exact id in driving_belief_id; if no belief applies, return null. Be concise."
)


async def _retrieve_beliefs(agent_id: uuid.UUID, query_vec: list[float], k: int) -> list[dict]:
    """The agent's active held beliefs, ranked by cosine similarity to the transaction."""
    dim = get_settings().embedding_dim
    literal = "[" + ",".join(repr(float(x)) for x in query_vec) + "]"
    sql = text(
        f"""
        SELECT b.id, b.rule_text,
               b.embedding <=> (:qvec)::VECTOR({dim}) AS distance
        FROM beliefs b
        LEFT JOIN belief_inheritance bi
               ON bi.belief_id = b.id AND bi.to_agent_id = :agent
        WHERE b.status = 'active'
          AND (b.originating_agent_id = :agent OR bi.to_agent_id = :agent)
        ORDER BY distance
        LIMIT :k
        """
    )
    async with engine.connect() as c:
        rows = (
            await c.execute(sql, {"qvec": literal, "agent": agent_id, "k": k})
        ).mappings().all()
    return [dict(r) for r in rows]


async def score_transaction(
    agent_id: uuid.UUID,
    *,
    txn_ref: str,
    merchant: str,
    mcc: int,
    amount: Decimal,
    account_age_months: float,
    is_fraud: bool,
    top_k: int = 3,
) -> dict:
    """Score one transaction with the live OpenAI agent and persist a decisions row.

    `is_fraud` is the world's ground truth — stored, but NEVER shown to the model.
    Returns the structured verdict plus the retrieved candidates and the new decision id.
    """
    query_vec = await embed_text(transaction_text(merchant, mcc, amount, account_age_months))
    candidates = await _retrieve_beliefs(agent_id, query_vec, top_k)

    belief_lines = "\n".join(f"  - id={c['id']}: {c['rule_text']}" for c in candidates) or "  (none)"
    user_msg = (
        f"Transaction:\n"
        f"  merchant: {merchant}\n  mcc: {mcc}\n  amount: ${amount}\n"
        f"  account_age_months: {account_age_months:.1f}\n\n"
        f"Beliefs you hold (most relevant first):\n{belief_lines}\n\n"
        f"Return your verdict."
    )

    resp = await get_openai().chat.completions.create(
        model=get_settings().chat_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_schema", "json_schema": _VERDICT_SCHEMA},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)

    # Validate the model's cited belief against the real candidates (no dangling refs).
    valid_ids = {str(c["id"]) for c in candidates}
    driving_raw = data.get("driving_belief_id")
    driving_id = uuid.UUID(driving_raw) if driving_raw in valid_ids else None
    verdict = data["verdict"]
    confidence = max(0.0, min(1.0, float(data["confidence"])))

    decision_id = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(
            Decision(
                id=decision_id,
                agent_id=agent_id,
                txn_ref=txn_ref,
                merchant=merchant,
                amount=Decimal(str(amount)),
                verdict=verdict,
                driving_belief_id=driving_id,
                confidence=confidence,
                decided_at=dt.datetime.now(dt.timezone.utc),
                is_fraud=is_fraud,
            )
        )
        await s.commit()

    return {
        "decision_id": decision_id,
        "verdict": verdict,
        "confidence": confidence,
        "driving_belief_id": driving_id,
        "rationale": data.get("rationale", ""),
        "is_fraud": is_fraud,
        "candidates": candidates,
    }
