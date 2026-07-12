"""scripts/ingest_regulatory.py — load the regulatory corpus (FATF / FFIEC / FinCEN red flags).

Reads the COMMITTED MARKDOWN in data/corpus/, not the PDFs. That is deliberate and it is what keeps
`llama-cloud-services` out of requirements.txt entirely: it pulls 67 packages including
llama-index-core, and it upgrades SQLAlchemy 2.0.36 -> 2.0.51 and numpy 2.2.1 -> 2.5.1 out from
under the app — the deepeval dependency break (NOTES, Item 8) landing on the DATABASE layer. The
markdown is a committed derived artifact, so this ingest is reproducible by anyone with no
LlamaParse key at all. Re-derive it with scripts/parse_regulatory.py in an ISOLATED venv.

ONE TRANSACTION PER DOCUMENT — the same rule as scripts/ingest_corpus.py, applied to the same unit
(a document). Each of the five becomes its own AOST-addressable HLC moment, so a corpus read AS OF a
past timestamp genuinely reflects only the documents committed by then.

Embeddings reuse the EXACT proven path beliefs and the typology corpus use — app.services.embeddings
.embed_text -> text-embedding-3-small @ 1536 dims. No parallel embedding implementation.

COST: one embedding call per chunk. 233 chunks == 233 calls (~20k tokens, well under a cent). The
count is printed and CONFIRMED before a single call is made; --dry-run stops before the first one.

Idempotent: id = uuid5(source, ordinal); ON CONFLICT (source, ordinal) DO NOTHING. Re-running does
not overwrite and does not re-embed what is already loaded.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/ingest_regulatory.py [--dry-run]
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.embeddings import embed_text  # noqa: E402
from app.services.regulation import PROFILES, chunk_document, vec_literal  # noqa: E402

_INSERT = text(
    """
    INSERT INTO regulatory_corpus
        (id, source, doc_label, part, section, lead_in, ordinal, body, embed_input, embedding)
    VALUES
        (:id, :source, :doc_label, :part, :section, :lead_in, :ordinal, :body, :embed_input,
         (:embedding)::VECTOR(1536))
    ON CONFLICT (source, ordinal) DO NOTHING
    """
)


async def main() -> None:
    dry = "--dry-run" in sys.argv
    print("=== ingesting the regulatory corpus (one txn per document, real embeddings) ===",
          flush=True)

    docs = {stem: chunk_document(stem) for stem in PROFILES}
    total = sum(len(v) for v in docs.values())

    print("\n[plan] chunks per document:", flush=True)
    for stem, chunks in docs.items():
        print(f"  {PROFILES[stem].source:34s} {len(chunks):4d}", flush=True)
        if not chunks:
            # The exact failure the first prototype hit: FIN-2014-A005 NUMBERS its red flags, so a
            # bullet-only extractor scored it zero. A document contributing nothing must never be a
            # quiet no-op — it is a silently absent authority.
            print(f"\nABORT: {stem} produced ZERO chunks. A parsed document that yields nothing is "
                  "an extraction rule that stopped matching, not an empty document.", flush=True)
            await engine.dispose()
            sys.exit(1)
    print(f"  {'TOTAL':34s} {total:4d}   <- this is the EXACT number of OpenAI embedding calls",
          flush=True)

    async with engine.connect() as c:
        already = (await c.execute(text("SELECT count(*) FROM regulatory_corpus"))).scalar_one()
    print(f"\n[state] regulatory_corpus currently holds {already} rows.", flush=True)

    if dry:
        print("[dry-run] stopping BEFORE the first embedding call. Nothing was spent.", flush=True)
        await engine.dispose()
        return

    if already:
        print("[skip] corpus is already loaded; ON CONFLICT would no-op every row. "
              "Nothing to embed, nothing to spend. Delete the rows first to re-ingest.", flush=True)
        await engine.dispose()
        return

    calls = 0
    for stem, chunks in docs.items():
        vectors = []
        for ch in chunks:
            # The SAME representation that is stored in `embed_input` — never a re-rendering.
            # Item 8's grounding-representation bug was exactly a re-rendering that differed.
            vectors.append(await embed_text(ch.embed_input))
            calls += 1
        bad = [i for i, v in enumerate(vectors) if len(v) != 1536]
        if bad:
            raise SystemExit(f"embedding dim != 1536 for {stem} chunks {bad}")

        # One document == one committed transaction == one AOST-addressable HLC moment.
        async with engine.begin() as conn:
            for ch, vec in zip(chunks, vectors):
                await conn.execute(_INSERT, {
                    "id": ch.id, "source": ch.source, "doc_label": ch.doc_label,
                    "part": ch.part, "section": ch.section, "lead_in": ch.lead_in,
                    "ordinal": ch.ordinal, "body": ch.body, "embed_input": ch.embed_input,
                    "embedding": vec_literal(vec),
                })
        print(f"  [committed] {PROFILES[stem].source:34s} {len(chunks):4d} chunks "
              f"({calls} embedding calls so far)", flush=True)

    async with engine.connect() as c:
        n = (await c.execute(text("SELECT count(*) FROM regulatory_corpus"))).scalar_one()
    print(f"\n[done] {calls} embedding calls made; regulatory_corpus now holds {n} rows.",
          flush=True)
    print("Next: PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe "
          "scripts/verify_regulatory.py", flush=True)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
