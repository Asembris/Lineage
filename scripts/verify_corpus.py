"""scripts/verify_corpus.py — re-derive the typology-corpus (Roadmap Item 3) against the LIVE
database. Trusts neither the ingest run's stdout nor these notes.

Checks (each PASS/FAIL; exits non-zero on any failure):
  1. counts: exactly 4 'altman-2306.16424' documents, each a 1536-dim embedding.
  2. join thread: every corpus `typology` is present in aml_pattern_instances.typology (the exact
     uppercase string Item 4 joins on). 0 orphans, and all 4 aml typologies are covered.
  3. retrieval is REAL: each document's own stored embedding retrieves ITSELF as top-1 (cosine),
     needs no OpenAI. Distances are finite and self-distance ~0.
  4. AOST time-travel of retrieval RUNS mechanically: the same query as_of a just-captured HLC and
     at present both return the expected top-1 (the SET TRANSACTION AS OF SYSTEM TIME path works
     over the vector search); an out-of-window as_of raises ValueError (-> 400), never a 500.
  5. EXPLAIN: report the REAL query plan at 4 rows. HONEST label — at this size the planner does a
     FULL SCAN + top-k and does NOT use the C-SPANN vector index; this proves the mechanism is
     wired, not retrieval quality at scale. (Not a failure — a stated property.)
  6. structural isolation: NO foreign key touches typology_corpus in either direction (it is on its
     own CorpusBase metadata; the join to aml_* is by string, not FK).

Run:  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/verify_corpus.py
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.corpus_models import SOURCE  # noqa: E402
from app.db import engine  # noqa: E402
from app.services.corpus import TYPOLOGY_DOCS, retrieve_typology, vec_literal  # noqa: E402

EXP_TYPOLOGIES = {"CYCLE", "SCATTER-GATHER", "GATHER-SCATTER", "STACK"}

_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}", flush=True)
    if not ok:
        _fails.append(name)


def _parse_vec(v: str) -> list[float]:
    return [float(x) for x in v.strip("[]").split(",") if x != ""]


async def main() -> None:
    print("=== verifying typology corpus against live defaultdb ===", flush=True)
    async with engine.connect() as c:
        # ---- 1. counts + dim -----------------------------------------------------------------
        rows = (await c.execute(text(
            "SELECT typology, embedding, version FROM typology_corpus WHERE source = :s "
            "ORDER BY typology"), {"s": SOURCE})).all()
        typ_to_vec = {t: _parse_vec(e) for t, e, _ in rows}
        print(f"[counts] {len(rows)} '{SOURCE}' documents: {sorted(typ_to_vec)}", flush=True)
        check("exactly 4 corpus documents", len(rows) == 4, f"got {len(rows)}")
        dims = {t: len(v) for t, v in typ_to_vec.items()}
        check("every embedding is 1536-dim", all(d == 1536 for d in dims.values()), f"{dims}")
        check("typologies == the 4 expected", set(typ_to_vec) == EXP_TYPOLOGIES, f"{set(typ_to_vec)}")

        # ---- 2. join thread: corpus typology subset of aml, all covered ----------------------
        aml = {r[0] for r in (await c.execute(text(
            "SELECT DISTINCT typology FROM aml_pattern_instances"))).all()}
        orphans = set(typ_to_vec) - aml
        check("0 corpus typologies absent from aml_pattern_instances", not orphans,
              f"orphans={orphans}" if orphans else f"aml has {sorted(aml)}")
        check("all 4 aml typologies covered by the corpus", EXP_TYPOLOGIES <= set(typ_to_vec),
              f"missing={EXP_TYPOLOGIES - set(typ_to_vec)}")

    # ---- 3. retrieval is real: self-retrieval top-1 ------------------------------------------
    self_ok = True
    detail = []
    for typ, vec in typ_to_vec.items():
        hits = await retrieve_typology(vec, k=1, source=SOURCE)
        top = hits[0]["typology"] if hits else None
        d0 = hits[0]["distance"] if hits else None
        detail.append(f"{typ}->{top}({d0:.4f})" if d0 is not None else f"{typ}->None")
        self_ok = self_ok and top == typ and abs(float(d0)) < 1e-6
    check("each document self-retrieves as top-1 at distance ~0", self_ok, " ".join(detail))

    # ---- 4. AOST retrieval runs mechanically + out-of-window -> ValueError --------------------
    async with engine.connect() as c:
        t0 = str((await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar())
    cyc = typ_to_vec["CYCLE"]
    past = await retrieve_typology(cyc, k=1, source=SOURCE, as_of=t0)
    now = await retrieve_typology(cyc, k=1, source=SOURCE)
    check("AOST retrieval (as_of a live HLC) runs and returns CYCLE",
          past and past[0]["typology"] == "CYCLE", f"got {past[0]['typology'] if past else None}")
    check("present retrieval returns CYCLE", now and now[0]["typology"] == "CYCLE")
    oow_ok = False
    try:
        await retrieve_typology(cyc, k=1, source=SOURCE, as_of="2020-01-01T00:00:00+00:00")
    except ValueError:
        oow_ok = True
    check("out-of-window as_of -> ValueError (not 500)", oow_ok)

    # ---- 5. EXPLAIN: honest query-plan finding at 4 rows -------------------------------------
    lit = vec_literal(cyc)
    async with engine.connect() as c:
        plan_rows = (await c.execute(text(
            f"EXPLAIN SELECT id, typology, embedding <=> '{lit}'::VECTOR(1536) AS d "
            f"FROM typology_corpus ORDER BY d LIMIT 3"))).all()
    plan = "\n".join(r[0].encode("ascii", "replace").decode() for r in plan_rows)
    used_vector_index = "ix_typology_corpus_embedding" in plan
    full_scan = "FULL SCAN" in plan
    print("[explain] query plan at 4 rows:", flush=True)
    for line in plan.splitlines():
        print("    " + line, flush=True)
    verdict = ("uses C-SPANN vector index" if used_vector_index
               else "FULL SCAN + top-k; C-SPANN vector index NOT exercised at 4 rows "
                    "(mechanism proof, not retrieval-quality at scale)")
    check("query-plan captured and reported honestly", True, verdict)
    # At 4 rows we EXPECT the full scan; flag loudly if reality diverges from the documented claim.
    check("plan matches documented small-scale behavior (full scan, no vector index)",
          full_scan and not used_vector_index, verdict)

    # ---- 6. structural isolation: no FK touches typology_corpus ------------------------------
    async with engine.connect() as c:
        fks = (await c.execute(text(
            "SELECT tc.table_name AS child, ccu.table_name AS parent "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY'"))).all()
    touching = sorted({(ch, pa) for ch, pa in fks
                       if "typology_corpus" in (ch, pa)})
    check("no FK touches typology_corpus (join to aml_* is by string, not FK)", not touching,
          f"{touching}" if touching else "corpus is FK-isolated on its own CorpusBase metadata")

    await engine.dispose()
    print("\n" + ("=== ALL CHECKS PASSED ===" if not _fails
                  else f"=== {len(_fails)} CHECK(S) FAILED: {_fails} ==="), flush=True)
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
