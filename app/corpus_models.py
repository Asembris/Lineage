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
