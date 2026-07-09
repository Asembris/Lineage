"""CockroachDB-native RAG over the typology corpus (Roadmap Item 3).

`retrieve_typology()` ranks the corpus by CockroachDB cosine vector similarity (the SAME `<=>`
path the fraud agent uses over beliefs.embedding — app/services/agent_brain.py), and — the point
of this item — supports `as_of` so the RETRIEVAL itself is time-travelled with real
`SET TRANSACTION AS OF SYSTEM TIME`, exactly like the lineage deposition. A query run as of an
earlier MVCC timestamp sees the corpus as it was then; if a definition was revised (re-embedded)
after that timestamp, the earlier read returns the earlier vector's ranking. Proven in
tests/test_corpus.py and scripts/verify_corpus.py.

What Item 4 calls: retrieve_typology(query_vec, ...) -> rows whose `typology` is GUARANTEED to be
a value present in aml_pattern_instances.typology (enforced at ingest), so a retrieved definition
joins straight back to real ingested pattern instances. This module owns retrieval + the canonical
definition text; scripts/ingest_corpus.py owns the one-transaction-per-document load.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.corpus_models import SOURCE
from app.db import engine
from app.services.time_travel import _AOST_RANGE_ERRORS, normalize_as_of

# Deterministic namespace for corpus uuid5 ids (distinct from the aml namespace).
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "lineage.corpus")


def corpus_id(source: str, typology: str) -> uuid.UUID:
    """Stable id for a (source, typology) definition — so load is idempotent and a revision
    UPDATEs the same row (MVCC keeps the prior version for AOST replay)."""
    return uuid.uuid5(_NS, f"{source}:{typology}")


def vec_literal(vec: list[float]) -> str:
    """list[float] -> the '[..]' literal CRDB's VECTOR accepts (matches app/types_crdb.Vector)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# The four typology definitions, quoted faithfully from Altman et al., "Realistic Synthetic
# Financial Transactions for Anti-Money Laundering Models" (NeurIPS 2023), §3.2 / Figure 2 — the
# paper behind the IBM HI-Small dataset ingested in Item 1. `typology` is the EXACT uppercase join
# key from Patterns.txt / aml_pattern_instances (not the paper's lowercase prose). Only the four
# typologies actually ingested in Item 1 are included, so every retrieval result is joinable to a
# real pattern instance. The regulatory corpus (FATF/FinCEN/FFIEC) is a separate, gated track.
TYPOLOGY_DOCS: list[dict[str, str]] = [
    {
        "typology": "CYCLE",
        "title": "Cycle laundering typology",
        "body": (
            "Cycle. A cycle laundering pattern is a sequence of transaction edges that starts and "
            "ends with the same account and visits every other account at most once, so the funds "
            "ultimately return to their account of origin. It is distinguished from the random "
            "pattern — a similar traversal in which the funds are not returned to the original "
            "account — precisely by this return-to-origin closure. (Altman et al., 'Realistic "
            "Synthetic Financial Transactions for Anti-Money Laundering Models', NeurIPS 2023, "
            "§3.2 / Figure 2.)"
        ),
    },
    {
        "typology": "SCATTER-GATHER",
        "title": "Scatter-gather laundering typology",
        "body": (
            "Scatter-gather. A fan-out pattern of a source account v and a fan-in pattern of a "
            "destination account u form a scatter-gather pattern when the fan-out and fan-in "
            "connect v and u to the same set of intermediate accounts: funds scatter from a single "
            "source across many intermediaries and are then gathered back into a single "
            "destination. (A fan-out is the outgoing edges of a vertex connecting it to at least "
            "two different vertices; a fan-in is a vertex connected to at least two different "
            "vertices via incoming edges.) (Altman et al., NeurIPS 2023, §3.2 / Figure 2.)"
        ),
    },
    {
        "typology": "GATHER-SCATTER",
        "title": "Gather-scatter laundering typology",
        "body": (
            "Gather-scatter. A gather-scatter pattern combines a fan-in pattern with a fan-out "
            "pattern of the same vertex: funds are first gathered from multiple source accounts "
            "into one intermediary account and then scattered from that account out to multiple "
            "destination accounts. (Altman et al., NeurIPS 2023, §3.2 / Figure 2.)"
        ),
    },
    {
        "typology": "STACK",
        "title": "Stack laundering typology",
        "body": (
            "Stack. A stack laundering pattern extends the bipartite pattern by adding an "
            "additional bipartite layer. A bipartite pattern moves funds from a set of input "
            "accounts to a set of output accounts; stacking chains two such layers so the funds "
            "pass through an intermediate tier of accounts. A single labeled stack instance may "
            "bundle several internally disjoint sub-chains rather than one connected path (Item 1 "
            "measured num_components > 1 for every ingested STACK instance). (Altman et al., "
            "NeurIPS 2023, §3.2 / Figure 2.)"
        ),
    },
]


def _retrieval_sql(source: str | None):
    dim = get_settings().embedding_dim
    source_clause = "WHERE source = :source" if source is not None else ""
    return text(
        f"""
        SELECT id, typology, title, body, source, version,
               embedding <=> (:qvec)::VECTOR({dim}) AS distance
        FROM typology_corpus
        {source_clause}
        ORDER BY distance
        LIMIT :k
        """
    )


async def retrieve_typology(
    query_vec: list[float],
    *,
    k: int = 3,
    as_of: str | None = None,
    source: str | None = None,
) -> list[dict]:
    """Cosine-nearest typology definitions to `query_vec`.

    `as_of` (ISO-8601 or HLC decimal) time-travels the retrieval with real AS OF SYSTEM TIME:
    the same query against an earlier MVCC snapshot returns the corpus as it was then. Out-of-window
    / malformed `as_of` raises ValueError (caller maps -> 400), never a 500 — same contract as the
    deposition. `source` optionally scopes the search (e.g. to one corpus provenance).
    """
    ts_literal = normalize_as_of(as_of) if as_of else None
    params: dict = {"qvec": vec_literal(query_vec), "k": k}
    if source is not None:
        params["source"] = source
    sql = _retrieval_sql(source)
    try:
        async with engine.connect() as conn:
            async with conn.begin():  # one explicit txn on a single physical connection
                if ts_literal is not None:
                    # MUST be the transaction's first statement; only the validated timestamp is
                    # inlined, the SELECT stays fully parameterized (same rule as time_travel.py).
                    await conn.execute(text(f"SET TRANSACTION AS OF SYSTEM TIME {ts_literal}"))
                rows = (await conn.execute(sql, params)).mappings().all()
    except DBAPIError as e:
        detail = str(getattr(e, "orig", None) or e).lower()
        if ts_literal is not None and any(s in detail for s in _AOST_RANGE_ERRORS):
            raise ValueError(
                f"as_of {as_of!r} is outside the time-travel window "
                "(older than the GC TTL or in the future)"
            ) from e
        raise
    return [dict(r) for r in rows]
