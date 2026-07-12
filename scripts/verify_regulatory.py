"""scripts/verify_regulatory.py — re-derive the regulatory corpus against the LIVE database.
Trusts neither the ingest run's stdout nor NOTES.

Checks (each PASS/FAIL; exits non-zero on any failure):
  1. counts: 233 rows, the pinned per-document census, every embedding 1536-dim.
  2. PARSE FIDELITY — the check that CANNOT live in CI. `data/raw/*.pdf` is gitignored, so CI does
     not have the PDFs; asserting parse fidelity there would assert the markdown against itself.
     Here the PDF text is re-extracted INDEPENDENTLY (pypdf) and every stored red-flag body must be
     found in it. Silently corrupted regulatory text would otherwise be embedded, retrieved and
     cited as authoritative FATF/FFIEC language with nothing downstream able to catch it.
  3. retrieval is REAL: each row's own stored embedding retrieves ITSELF as top-1, distance ~0.
  4. AOST time-travel of the retrieval runs; out-of-window as_of -> ValueError (-> 400), not a 500.
  5. THE VECTOR INDEX. EXPLAIN the real retrieval query and assert the C-SPANN index is USED.
     This is the first query in this project whose plan actually contains a `vector search` node.
     The two legacy indexes (beliefs, typology_corpus) are simultaneously re-checked as STILL L2 —
     that is the known, deliberately-deferred defect, and it is pinned so it cannot drift silently.
  6. structural isolation: no FK touches regulatory_corpus, in either direction.

Run:  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/verify_regulatory.py
"""

import asyncio
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.regulation import PROFILES, chunk_document, retrieve_regulation, vec_literal  # noqa: E402

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
PDFS = {
    "ffiec-bsa-aml-appendix-f": "Appendix-F-Money-Laundering-and-Terrorist-Financing.pdf",
    "fatf-va-red-flags-2020": "Virtual-Assets-Red-Flag-Indicators.pdf",
    "fatf-egmont-tbml-2021": "Trade-Based-Money-Laundering-Risk-Indicators.pdf",
    "fincen-2014-a005": "FIN-2014-A005.pdf",
    "fincen-2010-a001": "fin-2010-a001.pdf",
}
EXPECTED = {PROFILES[s].source: len(chunk_document(s)) for s in PROFILES}

_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}", flush=True)
    if not ok:
        _fails.append(name)


SHINGLE = 12   # characters


def _norm(s: str) -> str:
    """Letters and digits only, no spaces at all.

    THE NO-SPACES PART IS LOAD-BEARING, and every reason for it was found by RUNNING this check and
    diagnosing its failures — not by trusting the first version that passed. A naive
    `body in pdf_text` check failed on 8 bodies and the shingle-over-WORDS version on 25 more, and
    EVERY ONE was an artifact of pypdf's text stream rather than corrupted regulatory text:

      * pypdf splits words MID-TOKEN on PDF kerning: FATF's "residential" comes out as
        "residen tial". Stripping spaces makes the two identical again — a word-based comparison
        cannot recover from this at all.
      * The PDFs render sub-bullet GLYPHS as text: a literal `o` in FATF's Wingdings bullets and
        `\\uf0a7` in Egmont's. The stream reads "...transactions o in short succession...". A red
        flag that (correctly) omits the glyph is not a substring of that.
      * pypdf's reading order splices FOOTNOTE text into the middle of a FinCEN sentence.

    None of these is a fidelity failure. Text going MISSING, or being INVENTED, is — and character
    shingles catch that, because fabricated prose shares no 12-character window with the source.
    Proven, not asserted: the fabricated-red-flag check below scores 0.000.
    """
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _coverage(fragment: str, pdf: str) -> float:
    """Fraction of the fragment's 12-character windows that occur in the PDF. 1.0 == fully traced."""
    f = _norm(fragment)
    if len(f) < SHINGLE:
        return 1.0 if f and f in pdf else 0.0
    windows = [f[i:i + SHINGLE] for i in range(len(f) - SHINGLE + 1)]
    return sum(w in pdf for w in windows) / len(windows)


async def main() -> None:
    print("=== verifying the regulatory corpus against live defaultdb ===", flush=True)

    # ---- 1. counts + census + dim -------------------------------------------------------------
    async with engine.connect() as c:
        rows = (await c.execute(text(
            "SELECT source, count(*) AS n FROM regulatory_corpus GROUP BY source ORDER BY source"
        ))).all()
        census = {s: n for s, n in rows}
        total = (await c.execute(text("SELECT count(*) FROM regulatory_corpus"))).scalar_one()
        dims = (await c.execute(text(
            "SELECT DISTINCT array_length(embedding::REAL[], 1) FROM regulatory_corpus"
        ))).scalars().all()

    print(f"[census] {census}", flush=True)
    check("row count == 233", total == 233, f"got {total}")
    check("per-document census matches the chunker", census == EXPECTED,
          f"db={census} chunker={EXPECTED}")
    check("every embedding is 1536-dim", list(dims) == [1536], f"{dims}")

    # ---- 2. PARSE FIDELITY vs the original PDFs (cannot run in CI — PDFs are gitignored) -------
    try:
        from pypdf import PdfReader
    except ImportError:
        check("parse fidelity vs source PDFs", False, "pypdf not installed — cannot verify")
        PdfReader = None

    if PdfReader is not None:
        FLOOR = 0.95
        stem_for = {PROFILES[s].source: s for s in PROFILES}
        worst_overall, bad_total = (1.0, ""), 0

        for source, pdf in PDFS.items():
            path = RAW / pdf
            if not path.exists():
                check(f"parse fidelity: {source}", False, f"{path} not present")
                continue
            pdf_text = _norm(" ".join(p.extract_text() or "" for p in PdfReader(str(path)).pages))
            chunks = chunk_document(stem_for[source])

            # The chain is PDF -> markdown -> chunker -> database, and each link is checked.
            # Link 1: what the DATABASE holds is exactly what the chunker produces.
            async with engine.connect() as c:
                bodies = (await c.execute(text(
                    "SELECT body FROM regulatory_corpus WHERE source = :s ORDER BY ordinal"
                ), {"s": source})).scalars().all()
            check(f"db bodies == chunker output: {source}",
                  list(bodies) == [ch.body for ch in chunks],
                  f"{len(bodies)} db vs {len(chunks)} chunker")

            # Link 2: every SOURCE LINE traces to the PDF. Per LINE, not per body: a composite's
            # clauses are contiguous in the PDF individually, but the reassembled body is not —
            # the PDF has a bullet glyph between them. Checking the body whole would flag the
            # correct reassembly as an error.
            scored = [(_coverage(line, pdf_text), line) for ch in chunks for line in ch.source_lines]
            below = [t for t in scored if t[0] < FLOOR]
            bad_total += len(below)
            worst = min(scored, key=lambda t: t[0])
            worst_overall = min(worst_overall, worst, key=lambda t: t[0])
            check(f"parse fidelity: every red flag traces to {pdf}", not below,
                  f"{len(scored)} lines, worst coverage {worst[0]:.3f}"
                  + (f" — {len(below)} BELOW {FLOOR}: {below[0][1][:60]!r}" if below else ""))

        check("NO regulatory text was invented or corrupted in transit", bad_total == 0,
              f"worst line coverage across all five documents = {worst_overall[0]:.3f}")

        # THE GATE MUST BE ABLE TO FAIL. A fabricated red flag — plausible, well-formed, written in
        # the regulator's own register — must score near zero against the real document. If this
        # ever stops being true, every fidelity check above is decoration.
        fabricated = (
            "A customer repeatedly structures wire transfers through shell corporations "
            "domiciled in offshore secrecy jurisdictions to obscure beneficial ownership."
        )
        ffiec = _norm(" ".join(
            p.extract_text() or ""
            for p in PdfReader(str(RAW / PDFS["ffiec-bsa-aml-appendix-f"])).pages
        ))
        fab = _coverage(fabricated, ffiec)
        check("the fidelity gate TRIPS on fabricated regulatory text (it is not decoration)",
              fab < FLOOR, f"a plausible INVENTED red flag scores {fab:.3f} against the real "
                           f"FFIEC PDF, vs floor {FLOOR}")

    # ---- 3. retrieval is real: self-retrieval top-1 --------------------------------------------
    async with engine.connect() as c:
        sample = (await c.execute(text(
            "SELECT id, source, embedding FROM regulatory_corpus "
            "WHERE ordinal IN (0, 5) ORDER BY source, ordinal"
        ))).all()
    ok, detail = True, []
    for rid, source, emb in sample:
        vec = [float(x) for x in str(emb).strip("[]").split(",") if x != ""]
        hits = await retrieve_regulation(vec, k=1)
        top = hits[0] if hits else None
        ok = ok and top is not None and top["id"] == rid and abs(float(top["distance"])) < 1e-6
        detail.append(f"{source[:14]}->{'self' if top and top['id'] == rid else 'MISS'}")
    check("each row self-retrieves as top-1 at distance ~0", ok, " ".join(detail))

    # ---- 4. AOST over the vector search ---------------------------------------------------------
    vec = [float(x) for x in str(sample[0][2]).strip("[]").split(",") if x != ""]
    async with engine.connect() as c:
        t0 = str((await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar())
    past = await retrieve_regulation(vec, k=1, as_of=t0)
    check("AOST retrieval (as_of a live HLC) runs over the vector search",
          bool(past) and past[0]["id"] == sample[0][0])
    oow = False
    try:
        await retrieve_regulation(vec, k=1, as_of="2020-01-01T00:00:00+00:00")
    except ValueError:
        oow = True
    check("out-of-window as_of -> ValueError (not a 500)", oow)

    # ---- 5. THE VECTOR INDEX — the whole point --------------------------------------------------
    lit = vec_literal(vec)
    async with engine.connect() as c:
        plan_rows = (await c.execute(text(
            f"EXPLAIN SELECT id, embedding <=> '{lit}'::VECTOR(1536) AS d "
            f"FROM regulatory_corpus ORDER BY d LIMIT 5"
        ))).all()
    plan = "\n".join(r[0].encode("ascii", "replace").decode() for r in plan_rows)
    print("[explain] the REAL retrieval plan at 233 rows:", flush=True)
    for line in plan.splitlines():
        print("    " + line, flush=True)
    used = "ix_regulatory_corpus_embedding" in plan and "vector search" in plan
    check("the C-SPANN vector index IS SELECTED for the cosine query", used,
          "plan contains a `vector search` node over ix_regulatory_corpus_embedding" if used
          else "the index is NOT used — check the opclass matches the operator")
    check("and it is NOT a full scan", "FULL SCAN" not in plan)

    # The two legacy indexes: pinned as the KNOWN, DEFERRED defect. They are L2; their queries are
    # cosine; they have never been selected. Fixing them makes retrieval APPROXIMATE where it is
    # currently EXACT — a live change to Item 4's Gate 0 and Item 8's golden set. Its own session.
    async with engine.connect() as c:
        legacy = {}
        for t in ("beliefs", "typology_corpus"):
            ddl = str((await c.execute(text(f"SHOW CREATE TABLE {t}"))).all()[0][1])
            legacy[t] = next((l.strip() for l in ddl.splitlines() if "VECTOR INDEX" in l), "")
    check("beliefs' index is STILL vector_l2_ops (known defect, deferred by decision)",
          "vector_l2_ops" in legacy["beliefs"], legacy["beliefs"])
    check("typology_corpus' index is STILL vector_l2_ops (known defect, deferred by decision)",
          "vector_l2_ops" in legacy["typology_corpus"], legacy["typology_corpus"])

    # ---- 6. structural isolation ----------------------------------------------------------------
    async with engine.connect() as c:
        fks = (await c.execute(text(
            "SELECT tc.table_name AS child, ccu.table_name AS parent "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY'"
        ))).all()
    touching = sorted({(ch, pa) for ch, pa in fks if "regulatory_corpus" in (ch, pa)})
    check("no FK touches regulatory_corpus", not touching,
          f"{touching}" if touching else "FK-isolated on its own CorpusBase metadata")

    await engine.dispose()
    print("\n" + ("=== ALL CHECKS PASSED ===" if not _fails
                  else f"=== {len(_fails)} CHECK(S) FAILED: {_fails} ==="), flush=True)
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
