# NOT on app.db.Base by design (same call as app/aml_models.py, Roadmap Item 1): the
# typology/regulation RAG corpus is a defaultdb-ONLY concern. Keeping it on its own metadata
# makes that structural, not a convention to remember — Base.metadata.create_all (used to
# provision the throwaway `demo` database, see NOTES "Roadmap Item 0") physically cannot reach
# this table, and no FK runs from the corpus into the five-table moat OR the aml_* evidence layer.
# Full reasoning: NOTES.md "Roadmap Item 3".
"""CockroachDB-native RAG corpus — typology definitions embedded on the SAME cluster and the
SAME AOST timeline as the lineage graph and the AML evidence layer.

This is the whole competitive point over a Pinecone-style split: the vectors live in the same
transactional store as the genealogy graph, so a retrieval can be time-travelled with real
`AS OF SYSTEM TIME` exactly like the lineage deposition.

One table:
  * typology_corpus — one row per typology definition. `typology` is the exact uppercase join
    key that also lives in aml_pattern_instances.typology (CYCLE / SCATTER-GATHER /
    GATHER-SCATTER / STACK), so Item 4 can join a RETRIEVED definition straight back to real
    ingested pattern instances with no fuzzy matching. `embedding` is a real 1536-dim
    text-embedding-3-small vector (the SAME path beliefs use — app/services/embeddings.py).

Scope note (Roadmap Item 3): this is the typology half only. The regulatory corpus
(FATF/FinCEN/FFIEC red flags) is gated on a data/raw/ drop and is NOT modeled here.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Integer, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings
from app.types_crdb import Vector


class CorpusBase(DeclarativeBase):
    """Declarative base for the RAG corpus — a SEPARATE metadata from app.db.Base.

    Same rationale as AmlBase: CorpusBase.metadata is independent, so the demo database's
    Base.metadata.create_all never creates this table, and the five-table moat stays the moat.
    Schema for defaultdb is owned by Alembic migration 0005.
    """


_TS = DateTime(timezone=True)
SOURCE = "altman-2306.16424"  # Altman et al., NeurIPS 2023 — the typology definitions' provenance


class TypologyCorpus(CorpusBase):
    __tablename__ = "typology_corpus"

    # Client-assigned deterministic uuid5 (see scripts/ingest_corpus.py) — NO server default, so
    # re-ingestion is idempotent by identity and a REVISION is an in-place UPDATE of the same row
    # (MVCC keeps the prior version, which is what makes AOST-time-travel of retrieval real).
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # The exact uppercase join key — MUST be a value present in aml_pattern_instances.typology.
    # ingest_corpus.py asserts this against the live distinct set; verify_corpus.py re-checks it.
    typology: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text(f"'{SOURCE}'"))
    # Bumped on a revision; the prior version stays readable via AS OF SYSTEM TIME.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Real 1536-dim text-embedding-3-small vector (same path/dim as beliefs.embedding).
    embedding: Mapped[list[float]] = mapped_column(
        Vector(get_settings().embedding_dim), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=text("now()"))

    __table_args__ = (
        # One current row per (source, typology): idempotent load + revise-in-place, and it lets a
        # test scope its controlled rows under a distinct source without touching the real corpus.
        UniqueConstraint("source", "typology", name="uq_typology_corpus_source_typology"),
    )


class RegulatoryCorpus(CorpusBase):
    """FATF / FFIEC / FinCEN red-flag entries — a SEPARATE TABLE from typology_corpus, on purpose.

    THE REASON IS THE WHOLE POINT, so it is written here and not only in NOTES. `typology_corpus`
    carries an invariant Item 4's Gate 0 rests on: every row's `typology` is a value present in
    `aml_pattern_instances.typology`, so a retrieved definition joins straight back to real ingested
    pattern instances. Regulatory red flags do NOT map to IBM's four typologies. Putting them in
    `typology_corpus` with a nullable `typology` would have kept that invariant true only for
    callers who remember to pass `source=`, and `retrieve_typology(source=None)` is the DEFAULT.

    Every one of the 12 row-returning `retrieve_typology()` call sites does pass `source=SOURCE`
    today — so the rule holds, and it holds BY DISCIPLINE. ARCHITECTURE §7 is an entire section on
    what happens to rules held by discipline in this codebase. A separate table makes
    `retrieve_typology()` — whose SQL says `FROM typology_corpus` — physically incapable of
    returning a regulatory chunk, whatever arguments it is handed. Guard, not convention.

    Worth naming precisely, because the failure would NOT have looked like a false flag: the agent's
    query is a neutral structural summary (degrees, path lengths). At k=3 over ~270 rows, three
    regulatory chunks could plausibly outrank all four typology definitions — `_doc_for()` returns
    None for every claim and EVERY verdict collapses to INSUFFICIENT_COVERAGE/typology_not_retrieved.
    The brake would not become unsafe; it would become a WALL — Item 4's MARGIN_FLOOR mistake,
    reintroduced. And no test would have caught it: test_aml_brake, test_aml_interrogate and
    verify_corpus all pass `source=SOURCE`.

    Still on CorpusBase, so `Base.metadata.create_all` cannot reach it (the demo database stays
    clean, the five-table moat stays five) and no FK crosses into the moat or into aml_*.
    """

    __tablename__ = "regulatory_corpus"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # Document provenance, e.g. 'ffiec-bsa-aml-appendix-f'. Scopes retrieval to one authority.
    source: Mapped[str] = mapped_column(Text, nullable=False)
    doc_label: Mapped[str] = mapped_column(Text, nullable=False)
    # The document's own hierarchy, recovered by a spine rule — NOT by markdown heading levels,
    # which LlamaParse flattens to H1 across every one of the five documents. See
    # app/services/regulation.py. `part` is null where a document has no top-level split.
    part: Mapped[str | None] = mapped_column(Text, nullable=True)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A lead-in bullet ("...in combination with one or more of the following indicators:") whose
    # children are meaningless without it. Carried so the child never loses its qualifier.
    lead_in: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # The red-flag text, VERBATIM from the regulator. Never paraphrased.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # EXACTLY the string that was embedded (section path + body). Stored, not reconstructed:
    # Item 8's grounding-representation bug was caused by re-rendering evidence differently from
    # how the model saw it. What was embedded must be recoverable byte-for-byte, not re-derived.
    embed_input: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(get_settings().embedding_dim), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(_TS, nullable=False, server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("source", "ordinal", name="uq_regulatory_corpus_source_ordinal"),
    )
