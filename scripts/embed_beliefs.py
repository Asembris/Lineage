"""Re-embed active beliefs with REAL OpenAI vectors (replaces the Phase-1 placeholder).

The Phase-1 seed plants a deterministic PLACEHOLDER embedding (honest: embeddings drive
vector SEARCH, not the staleness signal). Phase 2's live agent retrieves the driving belief
by vector similarity, so the belief needs a real text-embedding-3-small vector. This is a
data op requiring OpenAI, so it lives here (post-gate) — not in the OpenAI-free seed/backfill.

Run after seeding:  PYTHONPATH=. .venv/Scripts/python.exe -m scripts.embed_beliefs
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.services.embeddings import embed_text


async def embed_active_beliefs() -> int:
    dim = get_settings().embedding_dim
    async with engine.connect() as c:
        rows = (
            await c.execute(
                text("SELECT id, rule_text FROM beliefs WHERE status = 'active'")
            )
        ).mappings().all()

    n = 0
    for r in rows:
        vec = await embed_text(r["rule_text"])
        literal = "[" + ",".join(repr(float(x)) for x in vec) + "]"
        async with engine.begin() as c:
            await c.execute(
                text(
                    f"UPDATE beliefs SET embedding = (:v)::VECTOR({dim}) WHERE id = :id"
                ),
                {"v": literal, "id": r["id"]},
            )
        n += 1
        print(f"embedded belief {r['id']}  ({len(vec)} dims)  \"{r['rule_text'][:60]}...\"")
    return n


async def _main() -> None:
    n = await embed_active_beliefs()
    print(f"\nre-embedded {n} active belief(s) with real OpenAI vectors")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
