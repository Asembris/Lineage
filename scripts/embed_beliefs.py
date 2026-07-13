"""Regenerate the committed REAL belief embeddings (seed/belief_embeddings.json).

WHAT THIS SCRIPT IS FOR CHANGED ON 2026-07-13, AND THE OLD JOB WAS UNFIXABLE.

It used to UPDATE the live `beliefs` rows with a real text-embedding-3-small vector, because
`seed.seed()` planted a deterministic PLACEHOLDER. That never worked, and it could not:
`seed.seed()` re-planted the placeholder on EVERY reseed, so whatever this wrote was discarded by
the next test run, CI run, or backfill. It was listed as the third of three ordered restore
commands, an operator was asked to remember it, and — measured on the live cluster on 2026-07-13 —
BOTH beliefs were sitting at cosine distance 0.000000000 from `seed.placeholder_embedding(1536)`
while the honesty ledger asserted one of them carried a real vector. The instruction was followed
imperfectly exactly once and the claim silently became false. That is the whole argument.

So the seed now plants the REAL vector, from a COMMITTED FIXTURE, and this script's job is to
produce that fixture. A reseed can no longer undo the embedding, and the restore procedure lost a
command (three -> two). The seed stays OpenAI-FREE: CI runs it with a dummy key and the isolated
`demo` database is seeded through the same path, so the seed cannot call the API — it reads the
cached vector instead. A cached embedding is a genuine model output; caching one is not the same
as inventing one, and `tests/test_belief_embeddings.py` proves the seeded vector is not the
placeholder rather than asserting it.

RUN IT WHEN A BELIEF'S `rule_text` CHANGES. Nothing else. `seed.belief_embedding()` refuses to
plant a vector whose recorded `rule_text` differs from what the seed is seeding, so editing a
belief's wording without re-running this fails loudly at seed time.

    python -m scripts.embed_beliefs --refresh-fixture         # both beliefs (one OpenAI call each)
    python -m scripts.embed_beliefs --refresh-fixture origin  # just one

`--update-live` additionally writes the vectors straight into the live `beliefs` rows, for when you
want the change without a destructive reseed. It is NOT part of the restore procedure any more.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.services.embeddings import embed_text
from seed.seed import _FIXTURE, AML_RULE_TEXT, RULE_TEXT, bid

# The beliefs the seed plants, and the text each one's vector must describe.
BELIEFS: dict[str, str] = {
    "origin": RULE_TEXT,        # crimson — the card-authorization heuristic
    "aml-cycle": AML_RULE_TEXT,  # azure  — the laundering typology (the grounding seam)
}


async def refresh_fixture(names: list[str]) -> None:
    settings = get_settings()
    data: dict = {"beliefs": {}}
    if _FIXTURE.exists():
        data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    data["model"] = settings.embedding_model
    data["dim"] = settings.embedding_dim
    data["generated_at"] = dt.date.today().isoformat()
    data.setdefault("beliefs", {})

    for name in names:
        rule_text = BELIEFS[name]
        vec = await embed_text(rule_text)
        if len(vec) != settings.embedding_dim:
            raise SystemExit(f"{name}: model returned {len(vec)} dims, schema wants "
                             f"{settings.embedding_dim}")
        # The rule_text travels WITH the vector so seed.belief_embedding() can refuse a vector
        # that was computed from different words than the ones being seeded.
        data["beliefs"][name] = {"rule_text": rule_text, "embedding": vec}
        print(f"  embedded {name:<10} ({len(vec)} dims)  {rule_text[:56]!r}...")

    _FIXTURE.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {_FIXTURE} — COMMIT IT. The seed plants these; a reseed can no longer undo them.")


async def update_live(names: list[str]) -> None:
    dim = get_settings().embedding_dim
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    async with engine.begin() as c:
        for name in names:
            vec = data["beliefs"][name]["embedding"]
            literal = "[" + ",".join(repr(float(x)) for x in vec) + "]"
            await c.execute(
                text(f"UPDATE beliefs SET embedding = (:v)::VECTOR({dim}) WHERE id = :id"),
                {"v": literal, "id": bid(name)},
            )
            print(f"  live row updated: {name} ({bid(name)})")


async def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("names", nargs="*", choices=sorted(BELIEFS) or None, default=None,
                    help=f"belief name(s): {', '.join(sorted(BELIEFS))} (default: all)")
    ap.add_argument("--refresh-fixture", action="store_true",
                    help="recompute the vectors and rewrite seed/belief_embeddings.json")
    ap.add_argument("--update-live", action="store_true",
                    help="also write the fixture's vectors into the live beliefs rows")
    args = ap.parse_args()

    if not args.refresh_fixture and not args.update_live:
        raise SystemExit(
            "nothing to do. Pass --refresh-fixture (recompute + rewrite the committed vectors) "
            "and/or --update-live (push the fixture into the live rows). Refusing to guess."
        )
    names = args.names or sorted(BELIEFS)

    if args.refresh_fixture:
        await refresh_fixture(names)
    if args.update_live:
        await update_live(names)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
