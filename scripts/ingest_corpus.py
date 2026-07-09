"""scripts/ingest_corpus.py — load the typology corpus into defaultdb, ONE TRANSACTION PER
DOCUMENT, with REAL text-embedding-3-small vectors (Roadmap Item 3).

Why one-txn-per-document (the roadmap's "no batch inserts"): each document is committed in its own
transaction, so each becomes its own AOST-addressable HLC moment. That is what makes "time-travel
the retrieval" a real, demonstrable capability rather than a slogan — a corpus read AS OF a past
timestamp genuinely reflects only the documents committed by then. (Proven in tests/test_corpus.py
and scripts/verify_corpus.py.)

Embeddings reuse the EXACT proven path beliefs use — app.services.embeddings.embed_text ->
app.services.openai_client (the solved Windows/AVG-MITM TLS fix) -> text-embedding-3-small @ 1536
dims. No parallel embedding implementation.

Idempotent: id = uuid5(source, typology); ON CONFLICT (source, typology) DO NOTHING. Re-running
does NOT overwrite (a revision is a deliberate, separate UPDATE — see scripts/demo_corpus_timetravel.py).

Load-time integrity GATE: every corpus `typology` MUST already exist in aml_pattern_instances.typology
(the exact join key Item 4 relies on). If a definition names a typology that was never ingested,
this aborts BEFORE writing anything — a silent CYCLE/"Cycle" mismatch can't slip through.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/ingest_corpus.py
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.corpus_models import SOURCE  # noqa: E402
from app.db import engine  # noqa: E402
from app.services.corpus import TYPOLOGY_DOCS, corpus_id, vec_literal  # noqa: E402
from app.services.embeddings import embed_text  # noqa: E402

_INSERT = text(
    """
    INSERT INTO typology_corpus (id, typology, title, body, source, version, embedding)
    VALUES (:id, :typology, :title, :body, :source, 1, (:embedding)::VECTOR(1536))
    ON CONFLICT (source, typology) DO NOTHING
    """
)


async def main() -> None:
    print("=== ingesting typology corpus (one txn per document, real embeddings) ===", flush=True)

    # GATE: corpus typologies must be a subset of what Item 1 actually ingested.
    async with engine.connect() as c:
        aml_typologies = {
            r[0] for r in (await c.execute(text("SELECT DISTINCT typology FROM aml_pattern_instances"))).all()
        }
    doc_typologies = {d["typology"] for d in TYPOLOGY_DOCS}
    orphans = doc_typologies - aml_typologies
    if orphans:
        print(f"ABORT: corpus typologies not present in aml_pattern_instances.typology: {orphans}",
              flush=True)
        print(f"       aml distinct typologies = {sorted(aml_typologies)}", flush=True)
        await engine.dispose()
        sys.exit(1)
    print(f"[gate] all {len(doc_typologies)} corpus typologies present in aml_pattern_instances "
          f"({sorted(doc_typologies)})", flush=True)

    inserted = 0
    for doc in TYPOLOGY_DOCS:
        # Real embedding via the SAME path beliefs use (text-embedding-3-small, 1536 dims).
        vec = await embed_text(doc["body"])
        if len(vec) != 1536:
            raise SystemExit(f"embedding dim {len(vec)} != 1536 for {doc['typology']}")
        # One document == one committed transaction == one AOST-addressable HLC moment.
        async with engine.begin() as conn:
            res = await conn.execute(
                _INSERT,
                {
                    "id": corpus_id(SOURCE, doc["typology"]),
                    "typology": doc["typology"],
                    "title": doc["title"],
                    "body": doc["body"],
                    "source": SOURCE,
                    "embedding": vec_literal(vec),
                },
            )
        n = res.rowcount or 0
        inserted += n
        print(f"  [{'insert' if n else 'exists '}] {doc['typology']:<15} "
              f"(id={corpus_id(SOURCE, doc['typology'])})", flush=True)

    async with engine.connect() as c:
        total = (await c.execute(text("SELECT count(*) FROM typology_corpus WHERE source = :s"),
                                 {"s": SOURCE})).scalar_one()
    print(f"\n[done] inserted {inserted} new; corpus now holds {total} '{SOURCE}' documents.",
          flush=True)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
