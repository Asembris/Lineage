"""Re-embed a belief with a REAL OpenAI vector (replaces the seed's placeholder).

The seed plants a deterministic PLACEHOLDER embedding for every belief (honest: embeddings
drive vector SEARCH, not the staleness signal, and the seed is deliberately OpenAI-free). The
live agent retrieves its driving belief by vector similarity, so a belief it must retrieve needs
a real text-embedding-3-small vector. This is a data op requiring OpenAI, so it lives here
(post-gate) — not in the OpenAI-free seed/backfill.

TARGETED BY DEFAULT, AND THAT IS DELIBERATE (G3). This script used to embed EVERY active belief
(`WHERE status = 'active'`). With a second belief in the fleet that made "embed the new azure
belief" silently rewrite the crimson belief's vector too — a write to the crimson bloodline from
a phase whose whole guarantee is that it touches nothing crimson. So the target is now explicit.

  python -m scripts.embed_beliefs aml-cycle    # the azure laundering belief (G3's target)
  python -m scripts.embed_beliefs origin       # the crimson card belief
  python -m scripts.embed_beliefs --all        # every active belief (opt-in, never implicit)

NOTE, and it is a real finding rather than a caveat: as of 2026-07-12 the crimson belief's
stored vector on the live cluster is STILL the deterministic placeholder (measured: cosine
distance 0.000000000 from `seed.placeholder_embedding(1536)`). `seed.seed()` re-plants the
placeholder on every run, so any embedding written here is discarded by the next reseed unless
this script is re-run. Do not read the honesty ledger's "placeholder -> real" row as a state the
live cluster is currently in. See NOTES "G3".
"""

import argparse
import asyncio
import sys
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.services.embeddings import embed_text
from seed.seed import bid


async def embed_beliefs(belief_ids: list[uuid.UUID] | None = None) -> int:
    """Embed the named beliefs, or every active belief when `belief_ids` is None."""
    dim = get_settings().embedding_dim
    sql = "SELECT id, rule_text FROM beliefs WHERE status = 'active'"
    params: dict = {}
    if belief_ids is not None:
        sql += " AND id = ANY(:ids)"
        params["ids"] = belief_ids

    async with engine.connect() as c:
        rows = (await c.execute(text(sql), params)).mappings().all()

    if belief_ids is not None and len(rows) != len(belief_ids):
        found = {r["id"] for r in rows}
        missing = [str(b) for b in belief_ids if b not in found]
        raise SystemExit(f"no ACTIVE belief for id(s): {', '.join(missing)}")

    n = 0
    for r in rows:
        vec = await embed_text(r["rule_text"])
        literal = "[" + ",".join(repr(float(x)) for x in vec) + "]"
        async with engine.begin() as c:
            await c.execute(
                text(f"UPDATE beliefs SET embedding = (:v)::VECTOR({dim}) WHERE id = :id"),
                {"v": literal, "id": r["id"]},
            )
        n += 1
        print(f'embedded belief {r["id"]}  ({len(vec)} dims)  "{r["rule_text"][:60]}..."')
    return n


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "names",
        nargs="*",
        help="seed belief name(s), e.g. 'aml-cycle' or 'origin' (resolved via seed.bid)",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="embed EVERY active belief (opt-in — this rewrites other bloodlines' vectors)",
    )
    args = ap.parse_args()

    if args.all and args.names:
        raise SystemExit("--all takes no names")
    if not args.all and not args.names:
        raise SystemExit(
            "name a belief (e.g. 'aml-cycle') or pass --all. Refusing to guess: an implicit "
            "--all would rewrite every bloodline's embedding as a side effect."
        )

    targets = None if args.all else [bid(n) for n in args.names]
    n = await embed_beliefs(targets)
    scope = "every active belief" if args.all else ", ".join(args.names)
    print(f"\nre-embedded {n} belief(s) with real OpenAI vectors  [target: {scope}]")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
