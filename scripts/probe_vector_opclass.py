"""THE VECTOR-INDEX OPCLASS MEASUREMENT — the probe behind migration 0010.

Committed under scripts/ rather than left in scratchpad/ ON PURPOSE. This project has shipped
FOUR fabricated verification citations, every one of them a claim backed by an ephemeral probe
that evaporated at session end (NOTES "FABRICATED VERIFICATION CITATIONS"). The rule that came
out of that: if a measurement matters enough to cite in shipped code, commit the probe and cite
THAT. Migration 0010 cites this file. So it has to exist, and it has to run.

It answers the three questions migration 0010 rests on, and it answers them by RUNNING, against
the live cluster, never by reasoning:

  1. STRUCTURE — when is a CockroachDB vector index actually selected? (the plan matrix)
     A vector index's PREFIX columns must each be constrained to a specific value. Both legacy
     indexes are on `(embedding)` alone — no prefix columns — so ANY WHERE clause forces a scan,
     whatever the opclass. This is why flipping `vector_l2_ops` -> `vector_cosine_ops` on
     `beliefs` or `typology_corpus` is INERT: it changes no plan.

  2. BEHAVIOUR — the only option that WOULD activate an approximate search on the real
     source-scoped query is a `(source, embedding vector_cosine_ops)` PREFIX index. Would that
     approximate search ever return a different top-3 than today's exact one? Measured over the
     four REAL corpus vectors with adversarially-manufactured near-ties.

  3. THE REAL AGENT QUERIES — the same comparison over the queries the agent ACTUALLY issues:
     `structure_text()` over real subjects, embedded with text-embedding-3-small. NOT
     self-retrieval (a document's own stored embedding retrieves itself at distance 0.000 and
     proves nothing — that is the trap tests/test_aml_brake.py sits in). Reports the top-3 SET
     for every subject and every Gate 0 outcome that would flip.

COSTS OPENAI: one embedding call per subject (~130 calls, well under a cent). Reads only; the
zz_opclass_probe table it creates is dropped on exit. Run:

    PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/probe_vector_opclass.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import random
import sys
import uuid

import numpy as np
from sqlalchemy import text

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.config import get_settings  # noqa: E402
from app.corpus_models import SOURCE  # noqa: E402
from app.db import engine  # noqa: E402
from app.services.aml_agent import RETRIEVAL_K  # noqa: E402
from app.services.aml_evidence import structure_text  # noqa: E402
from app.services.aml_graph import load_graph  # noqa: E402
from app.services.corpus import retrieve_typology  # noqa: E402
from app.services.embeddings import embed_text  # noqa: E402

PROBE = "zz_opclass_probe"
GOLDENSET = pathlib.Path("eval/grounding/goldenset.json")
SAMPLE_N = 100  # extra real subjects beyond the golden set, drawn deterministically


def lit(v) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


async def retry_exec(sql: str, params: dict | None = None, tries: int = 10) -> None:
    """CRDB 40001s under a vector index's background maintenance. Retry the unit (resilience.py)."""
    for attempt in range(tries):
        try:
            async with engine.begin() as c:
                await c.execute(text(sql), params or {})
            return
        except Exception as e:  # noqa: BLE001
            transient = "40001" in str(e) or "RETRY_SERIALIZABLE" in str(e)
            if not transient or attempt == tries - 1:
                raise
            await asyncio.sleep(0.4 * (2**attempt))


async def settle_stats(tbl: str, expect: int, tries: int = 20) -> bool:
    """A plan read off STALE statistics is worthless — a prior session reported one and had to
    throw it away. Block until the planner's statistics see all `expect` rows.

    The AUTHORITATIVE guard is not this function: it is `plan_verdict`, which refuses to read any
    plan whose text carries CockroachDB's own `missing stats` marker. This is belt-and-braces.
    """
    for _ in range(tries):
        await retry_exec(f"ANALYZE {tbl}")
        async with engine.connect() as c:
            rows = (await c.execute(text(f"SHOW STATISTICS FOR TABLE {tbl}"))).mappings().all()
        if rows and max(int(r["row_count"]) for r in rows) >= expect:
            return True
        await asyncio.sleep(2)
    return False


async def explain(c, sql: str, params: dict | None = None) -> str:
    rows = (await c.execute(text("EXPLAIN " + sql), params or {})).all()
    return "\n".join(r[0].encode("ascii", "replace").decode() for r in rows)


def plan_verdict(plan: str, index_name: str) -> str:
    if "missing stats" in plan:
        return "MISSING STATS - not trustworthy"
    if index_name in plan and "vector search" in plan:
        return "VECTOR SEARCH"
    return "FULL SCAN" if "FULL SCAN" in plan else "constrained scan (exact)"


# =================================================================================================
# 1. STRUCTURE — the plan matrix
# =================================================================================================
async def part1_plan_matrix(dim: int) -> None:
    print("=" * 92)
    print("1. WHEN IS A VECTOR INDEX SELECTED? (the structural fact behind migration 0010)")
    print("=" * 92)

    async with engine.connect() as c:
        for tbl in ("beliefs", "typology_corpus", "regulatory_corpus"):
            n = (await c.execute(text(f"SELECT count(*) FROM {tbl}"))).scalar()
            ddl = str((await c.execute(text(f"SHOW CREATE TABLE {tbl}"))).all()[0][1])
            ix = next((ln.strip() for ln in ddl.splitlines() if "VECTOR INDEX" in ln), None)
            print(f"  live catalog: {tbl:<19} n={n:<5} {ix or '(no vector index)'}")

    rng = np.random.default_rng(20260713)

    def rvec():
        v = rng.standard_normal(dim)
        return v / np.linalg.norm(v)

    async with engine.begin() as c:
        for t in (f"{PROBE}_plain", f"{PROBE}_prefix"):
            await c.execute(text(f"DROP TABLE IF EXISTS {t}"))
        for tbl, cols in ((f"{PROBE}_plain", "(embedding vector_cosine_ops)"),
                          (f"{PROBE}_prefix", "(source, embedding vector_cosine_ops)")):
            await c.execute(text(
                f"CREATE TABLE {tbl} (id UUID PRIMARY KEY, source TEXT NOT NULL, "
                f"embedding VECTOR({dim}) NOT NULL)"))
            await c.execute(text(f"CREATE VECTOR INDEX ix_{tbl} ON {tbl} {cols}"))

    n_rows = 1000
    for tbl in (f"{PROBE}_plain", f"{PROBE}_prefix"):
        for _ in range(n_rows // 100):
            vals = ",".join(
                f"('{uuid.uuid4()}', '{SOURCE}', '{lit(rvec())}'::VECTOR({dim}))" for _ in range(100)
            )
            await retry_exec(f"INSERT INTO {tbl} (id, source, embedding) VALUES {vals}")
        ok = await settle_stats(tbl, n_rows)
        print(f"  [stats] {tbl}: planner sees {n_rows} rows -> {'OK' if ok else 'STALE'}")

    q = lit(rvec())
    cases = [
        ("no WHERE, index on (embedding)                 [regulation.py's shape]",
         f"{PROBE}_plain", f"SELECT id, embedding <=> '{q}'::VECTOR({dim}) AS d "
         f"FROM {PROBE}_plain ORDER BY d LIMIT 3", {}),
        ("WHERE source=:s, index on (embedding)          [corpus.py's REAL shape]",
         f"{PROBE}_plain", f"SELECT id, embedding <=> '{q}'::VECTOR({dim}) AS d "
         f"FROM {PROBE}_plain WHERE source = :s ORDER BY d LIMIT 3", {"s": SOURCE}),
        ("WHERE source=:s, index on (source, embedding)  [the PREFIX option]",
         f"{PROBE}_prefix", f"SELECT id, embedding <=> '{q}'::VECTOR({dim}) AS d "
         f"FROM {PROBE}_prefix WHERE source = :s ORDER BY d LIMIT 3", {"s": SOURCE}),
        ("no WHERE, index on (source, embedding)         [prefix UNconstrained]",
         f"{PROBE}_prefix", f"SELECT id, embedding <=> '{q}'::VECTOR({dim}) AS d "
         f"FROM {PROBE}_prefix ORDER BY d LIMIT 3", {}),
    ]
    print()
    print("  n=1000, opclass ALREADY cosine in every row below — so the opclass is NOT the variable:")
    async with engine.connect() as c:
        for label, tbl, sql, params in cases:
            p = await explain(c, sql, params)
            print(f"    {label}\n        -> {plan_verdict(p, 'ix_' + tbl)}")

    print()
    print("  READ THIS OFF THE TABLE: a vector index is selected ONLY when every PREFIX column is")
    print("  constrained. `beliefs` and `typology_corpus` index (embedding) alone — no prefix — so")
    print("  their WHERE clauses force a scan REGARDLESS of opclass. Flipping the opclass is INERT.")


# =================================================================================================
# 2 + 3. BEHAVIOUR — would an ANN plan ever return a different top-3?
# =================================================================================================
async def build_ann_probe(docs, dim: int) -> bool:
    """The four REAL corpus vectors behind a (source, embedding vector_cosine_ops) PREFIX index —
    the ONLY configuration that actually activates a C-SPANN search on the real query."""
    async with engine.begin() as c:
        await c.execute(text(f"DROP TABLE IF EXISTS {PROBE}_ann"))
        await c.execute(text(
            f"CREATE TABLE {PROBE}_ann (id UUID PRIMARY KEY, source TEXT NOT NULL, "
            f"typology TEXT NOT NULL, embedding VECTOR({dim}) NOT NULL)"))
        await c.execute(text(
            f"CREATE VECTOR INDEX ix_{PROBE}_ann ON {PROBE}_ann (source, embedding vector_cosine_ops)"))
    vals = ",".join(
        f"('{d['id']}', '{SOURCE}', '{d['typology']}', '{lit(d['vec'])}'::VECTOR({dim}))" for d in docs
    )
    await retry_exec(f"INSERT INTO {PROBE}_ann (id, source, typology, embedding) VALUES {vals}")
    await settle_stats(f"{PROBE}_ann", len(docs))

    async with engine.connect() as c:
        p = await explain(
            c,
            f"SELECT typology, embedding <=> '{lit(docs[0]['vec'])}'::VECTOR({dim}) AS d "
            f"FROM {PROBE}_ann WHERE source = :s ORDER BY d LIMIT 3",
            {"s": SOURCE},
        )
    is_ann = f"ix_{PROBE}_ann" in p and "vector search" in p
    print(f"  the probe's plan IS a real C-SPANN vector search: {is_ann}")
    if not is_ann:
        print("  *** it is not — so anything measured against it would prove NOTHING. Aborting. ***")
    return is_ann


async def ann_top_k(c, qvec, dim: int, k: int) -> list[str]:
    rows = (await c.execute(text(
        f"SELECT typology, embedding <=> '{lit(qvec)}'::VECTOR({dim}) AS d "
        f"FROM {PROBE}_ann WHERE source = :s ORDER BY d LIMIT {k}"), {"s": SOURCE})).mappings().all()
    return [r["typology"] for r in rows]


def exact_top_k(qvec, docs, k: int) -> list[tuple[str, float]]:
    qn = np.asarray(qvec, dtype=float)
    qn = qn / np.linalg.norm(qn)
    scored = sorted(
        (1.0 - float(qn @ (d["vec"] / np.linalg.norm(d["vec"]))), d["typology"]) for d in docs
    )
    return [(t, dist) for dist, t in scored[:k]]


async def part2_adversarial(docs, dim: int) -> None:
    print()
    print("=" * 92)
    print("2. WOULD AN APPROXIMATE SEARCH REORDER THE TOP-3? (adversarial near-ties, no OpenAI)")
    print("=" * 92)
    rng = np.random.default_rng(2026)
    queries = []
    for i in range(len(docs)):
        for j in range(len(docs)):
            if i == j:
                continue
            for t in np.linspace(0.40, 0.60, 81):
                v = t * docs[i]["vec"] + (1 - t) * docs[j]["vec"]
                queries.append(v + 0.002 * rng.standard_normal(dim))
    for _ in range(400):
        v = rng.standard_normal(dim)
        queries.append(v / np.linalg.norm(v))
    centroid = sum(d["vec"] for d in docs) / len(docs)
    for _ in range(200):
        queries.append(centroid + 0.05 * rng.standard_normal(dim))

    mism, margins = 0, []
    async with engine.connect() as c:
        for qv in queries:
            ex = exact_top_k(qv, docs, RETRIEVAL_K)
            margins.append(exact_top_k(qv, docs, 2)[1][1] - ex[0][1])
            if {t for t, _ in ex} != set(await ann_top_k(c, qv, dim, RETRIEVAL_K)):
                mism += 1
    m = np.array(margins)
    print(f"  queries: {len(queries)}  (960 manufactured near-ties + 400 random + 200 centroid)")
    print(f"  top-1/top-2 margin: min={m.min():.6f}  median={np.median(m):.6f}  max={m.max():.6f}")
    print(f"  queries inside Item 4's dangerous band (margin <= 0.02): {int((m <= 0.02).sum())}")
    print(f"  TOP-3 SET MISMATCHES vs exact: {mism}")


async def part3_real_agent_queries(docs, dim: int) -> None:
    print()
    print("=" * 92)
    print("3. THE REAL AGENT QUERIES — structure_text() over real subjects, embedded (OpenAI)")
    print("=" * 92)
    print("   NOT self-retrieval. This is the query aml_agent actually issues.")

    g = await load_graph()
    subjects: list[tuple[str, uuid.UUID]] = []
    claims: dict[uuid.UUID, str] = {}
    cached: dict[uuid.UUID, list[str]] = {}

    if GOLDENSET.exists():
        for t in json.loads(GOLDENSET.read_text(encoding="utf-8")):
            sid = uuid.UUID(t["subject_id"])
            subjects.append(("goldenset", sid))
            claim = t.get("claim")
            if isinstance(claim, str):
                claim = eval(claim)  # noqa: S307 - the file stores a repr'd dict
            if isinstance(claim, dict) and claim.get("typology"):
                claims[sid] = claim["typology"]
            rt = t.get("retrieved_typologies")
            if isinstance(rt, str):
                rt = eval(rt)  # noqa: S307
            if rt:
                cached[sid] = list(rt)
        print(f"   golden-set subjects: {len(subjects)} (their cached top-3 was built against EXACT)")

    seen = {s for _, s in subjects}
    pool = sorted((eid for eid in g.by_id if eid not in seen), key=str)
    subjects += [("sampled", sid) for sid in random.Random(20260713).sample(pool, SAMPLE_N)]
    print(f"   + {SAMPLE_N} deterministically sampled real edges  ->  {len(subjects)} total")
    print(f"   embedding {len(subjects)} structure_text() queries with text-embedding-3-small ...")

    set_changes, gate_flips, gate_checked, cache_drift = [], [], 0, []
    margins = []
    async with engine.connect() as c:
        for i, (origin, sid) in enumerate(subjects, 1):
            edge = g.by_id.get(sid)
            if edge is None:
                continue
            qvec = await embed_text(structure_text(g, edge))
            live = await retrieve_typology(qvec, k=RETRIEVAL_K, source=SOURCE)
            exact = [r["typology"] for r in live]
            if len(live) >= 2:
                margins.append(float(live[1]["distance"]) - float(live[0]["distance"]))
            ann = await ann_top_k(c, qvec, dim, RETRIEVAL_K)

            if set(exact) != set(ann):
                set_changes.append((origin, sid, exact, ann))
            if origin == "goldenset" and sid in cached and set(cached[sid]) != set(exact):
                cache_drift.append((sid, cached[sid], exact))
            claimed = claims.get(sid)
            if claimed:
                gate_checked += 1
                before, after = claimed in exact, claimed in ann
                if before != after:
                    gate_flips.append((sid, claimed, before, after))
            if i % 25 == 0:
                print(f"     ... {i}/{len(subjects)}")

    m = np.array(margins)
    print()
    print(f"  subjects measured:                                {len(margins)}")
    print(f"  real top-1/top-2 margin: min={m.min():.6f} median={np.median(m):.6f} max={m.max():.6f}")
    print(f"  real queries inside Item 4's dangerous band (<=0.02): {int((m <= 0.02).sum())}/{len(m)}")
    print()
    print(f"  TOP-3 SET CHANGES (exact -> approximate):         {len(set_changes)}")
    for origin, sid, ex, an in set_changes[:20]:
        print(f"     [{origin}] {str(sid)[:8]}  exact={sorted(ex)}  ann={sorted(an)}")
    print(f"  GATE 0 OUTCOMES FLIPPED (of {gate_checked} subjects carrying a real model claim): "
          f"{len(gate_flips)}")
    for sid, claimed, before, after in gate_flips[:20]:
        print(f"     {str(sid)[:8]}  claimed={claimed}  retrieved_before={before} after={after}")
    print(f"  GOLDEN-SET retrieval_context drift (cached top-3 vs live EXACT top-3): {len(cache_drift)}")
    for sid, was, now in cache_drift[:20]:
        print(f"     {str(sid)[:8]}  cached={sorted(was)}  live={sorted(now)}")


async def main() -> None:
    dim = get_settings().embedding_dim
    try:
        await part1_plan_matrix(dim)

        async with engine.connect() as c:
            rows = (await c.execute(text(
                "SELECT id, typology, embedding::TEXT AS e FROM typology_corpus "
                "WHERE source = :s ORDER BY typology"), {"s": SOURCE})).mappings().all()
        docs = [
            {"id": uuid.UUID(str(r["id"])), "typology": r["typology"],
             "vec": np.array([float(x) for x in r["e"].strip("[]").split(",")])}
            for r in rows
        ]
        print(f"\n  pulled the {len(docs)} REAL corpus vectors: {[d['typology'] for d in docs]}")
        if not await build_ann_probe(docs, dim):
            return
        await part2_adversarial(docs, dim)
        await part3_real_agent_queries(docs, dim)
    finally:
        async with engine.begin() as c:
            for t in (f"{PROBE}_plain", f"{PROBE}_prefix", f"{PROBE}_ann"):
                await c.execute(text(f"DROP TABLE IF EXISTS {t}"))
        print("\n  [cleanup] every zz_opclass_probe* table dropped.")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
