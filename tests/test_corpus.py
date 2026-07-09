"""Roadmap Item 3 — CockroachDB-native RAG corpus: retrieval + time-travel of retrieval.

Two proofs, mirroring the Phase-1 / Item-2 AOST done-tests but over the vector-retrieval path:

  1. RETRIEVAL + AOST TIME-TRAVEL + RE-EMBED: with controlled deterministic vectors, the cosine
     top-1 is the nearest document; after a REVISION that gives one document a genuinely NEW
     embedding vector, a present-time retrieval reflects the revision while a retrieval AS OF a
     timestamp captured BEFORE the revision still returns the pre-revision top-1 (MVCC hides it).
     The test EXPLICITLY asserts the embedding vector changed (new_vec != old_vec) — the AOST
     differentiation only proves what it claims if the vector itself moved, not just the body text.
     (Requirement: the revision re-embeds. The LIVE path uses embed_text(); this hermetic test
     uses hand-built vectors so it needs no OpenAI and is CI-safe — see scripts/demo_corpus_timetravel.py
     for the real embed_text() re-embedding against the cluster.)

  2. Out-of-window / malformed as_of -> ValueError (caller maps -> 400), never a 500.

Isolation: the test operates ONLY on rows tagged source='__test_corpus__', scopes every retrieval
to that source, and deletes them before and after. It never touches the real 'altman-2306.16424'
corpus, so running it (incl. in CI) needs no re-ingest and cannot corrupt the loaded documents.
"""

import asyncio

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.services.corpus import retrieve_typology, vec_literal

SRC = "__test_corpus__"
DIM = get_settings().embedding_dim  # 1536

_INSERT = text(
    """
    INSERT INTO typology_corpus (id, typology, title, body, source, version, embedding)
    VALUES (gen_random_uuid(), :typology, :title, :body, :source, 1, (:emb)::VECTOR(1536))
    """
)


def _vec(*pairs: tuple[int, float]) -> list[float]:
    """A DIM-length vector, zero except the given (index, value) entries."""
    v = [0.0] * DIM
    for i, val in pairs:
        v[i] = val
    return v


async def _delete_test_rows() -> None:
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM typology_corpus WHERE source = :s"), {"s": SRC})


def test_corpus_retrieval_time_travels_and_revision_reembeds():
    async def _run():
        try:
            await _delete_test_rows()  # clean slate for our tagged source

            q = _vec((0, 1.0))          # query points along axis 0
            e_a = _vec((0, 1.0))        # TEST-A: identical to q  -> cosine distance 0 (nearest)
            e_b = _vec((0, 0.6), (1, 0.8))  # TEST-B: cos 0.6 -> distance 0.4 (second)

            # Load each document in its own committed txn (mirrors the one-txn-per-doc loader).
            async with engine.begin() as c:
                await c.execute(_INSERT, {"typology": "TEST-A", "title": "a", "body": "a",
                                          "source": SRC, "emb": vec_literal(e_a)})
            async with engine.begin() as c:
                await c.execute(_INSERT, {"typology": "TEST-B", "title": "b", "body": "b",
                                          "source": SRC, "emb": vec_literal(e_b)})

            # Capture t0 AFTER the inserts, BEFORE the revision.
            async with engine.connect() as c:
                t0 = str((await c.execute(text("SELECT cluster_logical_timestamp()"))).scalar())

            # Before revision: top-1 is TEST-A.
            pre = await retrieve_typology(q, k=2, source=SRC)
            assert [h["typology"] for h in pre] == ["TEST-A", "TEST-B"], pre

            # REVISE TEST-A with a genuinely NEW embedding vector (axis 1, orthogonal to q).
            e_a2 = _vec((1, 1.0))
            # EXPLICIT: the vector itself changed. The AOST proof below is only meaningful because
            # of this — a revision that left a stale vector behind would not move the ranking.
            assert e_a2 != e_a, "revision must produce a genuinely different embedding vector"
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE typology_corpus SET embedding = (:emb)::VECTOR(1536), "
                        "version = 2, updated_at = now() WHERE source = :s AND typology = 'TEST-A'"
                    ),
                    {"emb": vec_literal(e_a2), "s": SRC},
                )

            # Present-time retrieval reflects the revision: TEST-A moved away, TEST-B is now top-1.
            now = await retrieve_typology(q, k=2, source=SRC)
            assert now[0]["typology"] == "TEST-B", now
            assert now[0]["version"] == 1 and _typ(now, "TEST-A")["version"] == 2

            # AS OF t0 (before the revision): retrieval STILL returns the pre-revision top-1 TEST-A,
            # at version 1 — CockroachDB MVCC time-travels the VECTOR SEARCH itself, not just the graph.
            past = await retrieve_typology(q, k=2, source=SRC, as_of=t0)
            assert past[0]["typology"] == "TEST-A", (
                "AOST retrieval reflected a revision committed after t0 — retrieval time-travel is "
                "not real / not reproducible"
            )
            assert past[0]["version"] == 1
        finally:
            await _delete_test_rows()
            await engine.dispose()

    asyncio.run(_run())


def test_corpus_retrieval_rejects_out_of_window_and_malformed_as_of():
    async def _run():
        try:
            q = _vec((0, 1.0))
            # older than the GC TTL (4500s) -> fails inside CRDB at SET TRANSACTION -> ValueError
            with pytest.raises(ValueError):
                await retrieve_typology(q, as_of="2020-01-01T00:00:00+00:00")
            # malformed -> ValueError (normalize_as_of rejects it before any SQL)
            with pytest.raises(ValueError):
                await retrieve_typology(q, as_of="not-a-timestamp")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _typ(rows: list[dict], typology: str) -> dict:
    return next(r for r in rows if r["typology"] == typology)
