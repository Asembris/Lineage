"""scripts/demo_corpus_timetravel.py — the LIVE proof that a corpus revision RE-EMBEDS and that
retrieval time-travels over the real embedding (Roadmap Item 3).

Unlike tests/test_corpus.py (hermetic, hand-built vectors, CI-safe), this demo uses the REAL
embed_text() path against OpenAI and the live cluster, to confirm the property the roadmap asked
for explicitly: the revision gets a genuinely NEW text-embedding-3-small vector, not just updated
body text sitting behind a stale vector. It:

  1. reads the current CYCLE document (body, embedding, version);
  2. captures t0 (an HLC) BEFORE any change;
  3. revises CYCLE's body with a faithful added sentence and RE-EMBEDS it via embed_text()
     -> a new real 1536-dim vector; asserts new_vec != old_vec and reports their cosine distance;
  4. shows that querying with the OLD vector self-matches CYCLE at ~0 distance AS OF t0, but at a
     strictly POSITIVE distance now (the stored vector genuinely moved) -> CRDB time-travels the
     vector search itself;
  5. RESTORES the original body/embedding/version so the corpus is left pristine.

Reversible by design: it writes the saved originals back (no second OpenAI call needed to restore),
so running it does not require a re-ingest afterward.

Run:  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_corpus_timetravel.py
"""

import asyncio
import math
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.corpus_models import SOURCE  # noqa: E402
from app.db import engine  # noqa: E402
from app.services.corpus import retrieve_typology, vec_literal  # noqa: E402
from app.services.embeddings import embed_text  # noqa: E402

REVISION_NOTE = (
    " Revised note: in the IBM HI-Small data every ingested CYCLE instance forms a single "
    "weakly-connected component (num_components = 1) — a genuinely connected traversal path."
)


def _parse_vec(v: str) -> list[float]:
    return [float(x) for x in v.strip("[]").split(",") if x != ""]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / (na * nb)


async def main() -> None:
    print("=== LIVE demo: corpus revision re-embeds + retrieval time-travels ===", flush=True)

    async with engine.connect() as c:
        row = (await c.execute(text(
            "SELECT body, embedding, version FROM typology_corpus "
            "WHERE source = :s AND typology = 'CYCLE'"), {"s": SOURCE})).one()
    orig_body, orig_emb_lit, orig_version = row[0], row[1], row[2]
    old_vec = _parse_vec(orig_emb_lit)
    print(f"[read] CYCLE v{orig_version}, {len(old_vec)}-dim embedding, body {len(orig_body)} chars",
          flush=True)

    async with engine.connect() as c:
        t0 = str((await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar())
    print(f"[t0]   captured HLC before revision: {t0}", flush=True)

    try:
        # --- revise: real re-embed via embed_text() ---------------------------------------------
        new_body = orig_body + REVISION_NOTE
        new_vec = await embed_text(new_body)
        assert len(new_vec) == 1536, f"got dim {len(new_vec)}"
        assert new_vec != old_vec, "REVISION DID NOT RE-EMBED — new vector equals the old one"
        cos_d = _cosine_distance(old_vec, new_vec)
        print(f"[revise] re-embedded CYCLE via embed_text(); new_vec != old_vec = "
              f"{new_vec != old_vec}; cosine distance(old,new) = {cos_d:.6f}", flush=True)

        async with engine.begin() as c:
            await c.execute(
                text("UPDATE typology_corpus SET body = :b, embedding = (:e)::VECTOR(1536), "
                     "version = :v, updated_at = now() WHERE source = :s AND typology = 'CYCLE'"),
                {"b": new_body, "e": vec_literal(new_vec), "v": orig_version + 1, "s": SOURCE},
            )
        print(f"[commit] CYCLE now v{orig_version + 1} with the revised body + new vector", flush=True)

        # --- prove the vector moved AND time-travels --------------------------------------------
        # Query with the OLD vector. As of t0, CYCLE's stored vector WAS old_vec -> self-distance ~0.
        past = await retrieve_typology(old_vec, k=1, source=SOURCE, as_of=t0)
        now = await retrieve_typology(old_vec, k=4, source=SOURCE)
        now_cycle = next(h for h in now if h["typology"] == "CYCLE")
        past_top, past_d = past[0]["typology"], float(past[0]["distance"])
        now_d = float(now_cycle["distance"])
        print(f"[aost] query=OLD CYCLE vector:", flush=True)
        print(f"       AS OF t0  -> top-1 {past_top} at distance {past_d:.6f} "
              f"(v{past[0]['version']})", flush=True)
        print(f"       PRESENT   -> CYCLE at distance {now_d:.6f} (v{now_cycle['version']})",
              flush=True)
        assert past_top == "CYCLE" and past_d < 1e-6, "AOST did not reproduce the pre-revision vector"
        assert now_d > 1e-6, "present CYCLE distance to the old vector should be > 0 (vector moved)"
        assert past[0]["version"] == orig_version and now_cycle["version"] == orig_version + 1
        print("[proof] the stored vector genuinely moved; AS OF SYSTEM TIME reproduces the "
              "pre-revision embedding and ranking. Retrieval — not just the graph — time-travels.",
              flush=True)
    finally:
        # --- restore the corpus to pristine (write back the saved originals) --------------------
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE typology_corpus SET body = :b, embedding = (:e)::VECTOR(1536), "
                     "version = :v, updated_at = now() WHERE source = :s AND typology = 'CYCLE'"),
                {"b": orig_body, "e": orig_emb_lit, "v": orig_version, "s": SOURCE},
            )
        print(f"[restore] CYCLE reset to v{orig_version} with its original body + embedding.",
              flush=True)
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
