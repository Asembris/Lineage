"""The regulatory corpus: structure-aware chunking of FATF/FFIEC/FinCEN red flags, + retrieval.

Two halves. `chunk_document()` is PURE (filesystem + string work, no DB, no network, no OpenAI), so
every structural claim below is a hermetic test rather than a promise. `retrieve_regulation()` is
the CockroachDB cosine search — the same `<=>` path as beliefs and the typology corpus, on the same
cluster and the same AOST timeline, but over the FIRST vector index in this project whose opclass
actually matches its operator (migration 0009, `vector_cosine_ops`).

============================ WHY THE OBVIOUS DESIGN IS IMPOSSIBLE ============================
The structure-aware constraint (NOTES, Item 3) says: derive each chunk's section path from the
document's headings and prepend it before embedding. The obvious reading — read the path off the
markdown heading LEVELS — cannot be implemented on this input.

MEASURED, on all five parsed documents: **every heading LlamaParse emits is `#` (H1).** The running
page header, the two real top-level parts, and their subsections are all the same level. Heading
levels carry ZERO hierarchy.

The Agentic tier (10 credits/page vs 3) DOES emit real `##`/`###` levels — and it is WORSE. Measured
on FFIEC: the red-flag payload is identical (129 bullets = 129 bullets, zero loss either way), but
6 of the 29 section headings are not headings in its output — four are demoted to **bold body text**
and two vanish as strings entirely, their red flags silently absorbed into the preceding section.
Its hierarchy is also internally inconsistent, and it drops the Money-Laundering "Funds Transfers"
section while keeping the Terrorist-Financing one — which would resolve the collision below the
WRONG way. Complete-and-flat beats hierarchical-and-lossy when you are going to apply a
deterministic rule to it. So: Cost-effective tier, and the spine is recovered here.

============================== THE PATH IS LOAD-BEARING, NOT DECOR ==============================
FFIEC Appendix F contains TWO sections both titled "Funds Transfers" — one under Money Laundering
(9 entries), one under Terrorist Financing (5). Likewise two titled "Activity Inconsistent with the
Customer's Business". Same string, different meaning. Without the PART in the path they are
indistinguishable provenance, and a query about terrorist-financing wire activity can retrieve a
money-laundering red flag while appearing perfectly well-sourced. That is the whole reason the
constraint exists, and `test_regulatory_corpus.py` asserts the collision rather than describing it.

===================================== TWO REAL HAZARDS =====================================
Both found by RUNNING the extraction, not by reading the documents:

1. FinCEN numbers its red flags, and the numbering is CONTAMINATED. FIN-2014-A005 emits 0 bullets
   and 21 numbered items — of which only 5 are red flags. Items "6." and "7." sit in the same block
   and are FOOTNOTE DEFINITIONS ("Often termed 'operating outside the geographic footprint.'"); a
   trailing block is a numbered REFERENCE list ("See, FinCEN (April 2011) Advisory FIN-2011-A009").
   A naive `^\\d+\\.` regex embeds those as authoritative FinCEN red flags. The rule that separates
   them is structural, not a hardcoded cutoff: **a list is a CONTIGUOUS RUN; a blank line ends it.**
   The five red flags are five consecutive lines; each footnote is a detached paragraph that merely
   begins with a number. Verified against the real file, and asserted.

2. LEAD-IN bullets. fin-2010-a001 has bullets ending in ":" whose children lose their qualifier when
   embedded alone ("Unusual deposits occurring in combination with one or more of the following
   indicators:" followed by items that read as unconditional on their own). LlamaParse flattened the
   nesting, so the child looks like a peer. A lead-in is therefore NOT emitted as a chunk of its own
   (it asserts nothing) and is instead CARRIED onto each child, in the path and in `embed_input`.

===================================== CURATION =====================================
Case studies, acronym lists, tables of contents, citing references, introductions and conclusions
are NOT red flags. They are dropped. A corpus that answers a red-flag query with "Case Study 5. Use
of IP address associated with Darknet Marketplace - Alpha Bay" is polluted, and the pollution is
invisible at retrieval time because the chunk looks authoritative.

===================================== WHAT THIS IS NOT =====================================
The regulatory corpus CANNOT AUTHORIZE A FLAG, ever. Item 4 measured that retrieval distance is not
a coverage signal in either direction and gates nothing; a description of a typology the corpus does
not even contain retrieved CLOSER than every in-corpus query. Nothing here changes that. A retrieved
red flag is CONTEXT — provenance a human reads — never evidence, and never a gate. Only a structural
witness over the money-flow graph may authorize a FLAG. If a future session wires this into a
verdict, it has rebuilt MARGIN_FLOOR. `retrieve_regulation()` is not imported by the deciding path,
and tests/test_oracle_boundary.py's DECIDING_PATH is where that stays true.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import engine
from app.services.time_travel import _AOST_RANGE_ERRORS, normalize_as_of

CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "corpus"

# Deterministic namespace for regulatory chunk ids — distinct from the typology corpus namespace.
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "lineage.regulation")

_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBER = re.compile(r"^\s*\d+\.\s+(.*)$")
# Inline footnote markers LlamaParse leaves in the text ("...in China.7", "<sup>302</sup>").
_SUP = re.compile(r"<sup>\d+</sup>")
_TRAIL_FOOTNOTE = re.compile(r"(?<=[.\"”])\d{1,3}$")

# A red-flag entry shorter than this is a list artifact, not an indicator. The shortest REAL entry
# across all five documents is 89 characters with its path; the shortest raw body is well above 25.
_MIN_BODY = 25


@dataclass(frozen=True)
class Chunk:
    source: str
    doc_label: str
    part: str | None
    section: str | None
    lead_in: str | None
    ordinal: int
    body: str
    # The VERBATIM markdown lines this chunk was composed from. Usually one. More than one where
    # LlamaParse flattened a regulator's sub-clauses into peer bullets (see `_group`), in which case
    # the body is their concatenation and nothing else. Kept so the fidelity gate can prove the
    # chunker introduced no text that is not in the source, rather than merely asserting it.
    source_lines: tuple[str, ...] = ()

    @property
    def section_path(self) -> str:
        parts = [self.doc_label, self.part, self.section, self.lead_in]
        return " > ".join(p for p in parts if p)

    @property
    def embed_input(self) -> str:
        """EXACTLY what gets embedded, and exactly what is stored in `embed_input`."""
        return f"{self.section_path}: {self.body}"

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(_NS, f"{self.source}:{self.ordinal}")


@dataclass(frozen=True)
class Profile:
    """A document's spine. Declarative, because heading LEVELS are unusable (see module docstring).

    parts     — a heading matching one of these opens a top-level PART; sections nest under it.

    furniture — TRANSPARENT. A running page header. It is skipped and the section state is
                PRESERVED, because a section CONTINUES ACROSS THE PAGE BREAK: FFIEC's "Funds
                Transfers" is interrupted by the running header and then resumes with three more
                red flags. Treating furniture as a boundary silently truncates that section.

    exclude   — OPAQUE. A heading that opens NON-RED-FLAG matter: a table of contents, an acronym
                list, a case study, a reference list, front matter. It suppresses everything under
                it until the next real section. This distinction is not cosmetic — collapsing it
                into `furniture` ingested FATF's TABLE OF CONTENTS as red-flag chunks, and the
                orphan gate in tests/test_regulatory_corpus.py is what caught it.

    marker    — which list marker carries this document's red flags. FIN-2014-A005 is the only
                'numbered' document, and it is the only one with footnote contamination.

    only      — if set, ONLY these sections contribute. Used where the red flags are confined to
                one section and the rest of the document is narrative.
    """

    source: str
    label: str
    parts: tuple[str, ...] = ()
    furniture: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    marker: str = "bullet"
    only: tuple[str, ...] = ()


PROFILES: dict[str, Profile] = {
    "Appendix-F-Money-Laundering-and-Terrorist-Financing": Profile(
        source="ffiec-bsa-aml-appendix-f",
        label="FFIEC BSA/AML Examination Manual, Appendix F",
        parts=(r"^Potentially Suspicious Activity That May Indicate ",),
        # TRANSPARENT: the running page header repeats as an H1 on every body page, and it lands in
        # the MIDDLE of "Funds Transfers", which resumes with three more red flags after it. If this
        # were a boundary, those three would be silently lost.
        furniture=(r"^Appendix F: Money Laundering",),
    ),
    "Virtual-Assets-Red-Flag-Indicators": Profile(
        source="fatf-va-red-flags-2020",
        label="FATF Virtual Assets Red Flag Indicators (2020)",
        parts=(r"^Red Flag Indicators? (Related|about|in the)",),
        # TRANSPARENT: running header, including the variant with the page number fused into it
        # ("# 6 | VIRTUAL ASSETS RED FLAG INDICATORS ...").
        furniture=(r"^\d+\s*\|", r"^VIRTUAL ASSETS RED FLAG"),
        # OPAQUE: everything under these is not a red flag. The Table of Contents is the one that
        # actually bit — its entries are bullets, and they were ingested as indicators.
        exclude=(
            # Cover-page GRAPHICS artifacts: LlamaParse read the cover's design elements as headings.
            r"^NODE ", r"^BLOCK ", r"^FATF REPORT$",
            r"^The Financial Action Task Force", r"^Citing reference", r"^Table of Contents$",
            r"^Acronyms$", r"^Introduction$", r"^Conclusion$", r"^References$",
            r"^Case Study", r"^September 2020$", r"^Virtual Assets$",
            r"^Red Flag Indicators of", r"^Red Flag Indicators$",
        ),
    ),
    "Trade-Based-Money-Laundering-Risk-Indicators": Profile(
        source="fatf-egmont-tbml-2021",
        label="FATF/Egmont Trade-Based Money Laundering Risk Indicators (2021)",
        furniture=(r"^\d+\s*\|", r"^TRADE-BASED MONEY LAUNDERING"),
        exclude=(
            r"^Trade-Based Money Laundering$", r"^March 2021$", r"^The Financial Action Task Force",
            r"^EGMONT GROUP", r"^Citing reference",
            r"^Trade-Based Money Laundering: Risk Indicators$",
            r"^Trade-Based Money Laundering .{0,3} Trends", r"^Risk Indicators$",
        ),
    ),
    "FIN-2014-A005": Profile(
        source="fincen-2014-a005",
        label="FinCEN Advisory FIN-2014-A005 (funnel accounts and trade-based money laundering)",
        marker="numbered",
        # This advisory is mostly narrative; its red flags are confined to ONE section. Everything
        # else — including a numbered list of "typical steps", which is a process description and
        # not an indicator — is deliberately excluded.
        only=(r"^Funnel Accounts and Trade-Based Money Laundering Red Flags$",),
    ),
    "fin-2010-a001": Profile(
        source="fincen-2010-a001",
        label="FinCEN Advisory FIN-2010-A001 (trade-based money laundering indicators)",
        only=(r"^Indicators of Potential Money Laundering Activities$",),
    ),
}


def _matches(pats: tuple[str, ...], s: str) -> bool:
    return any(re.search(p, s) for p in pats)


def _clean(s: str) -> str:
    """Strip the parser's inline footnote residue. Never rewords the regulator's text."""
    s = _SUP.sub("", s).strip()
    return _TRAIL_FOOTNOTE.sub("", s).strip()


def _group(items: list[str]) -> list[tuple[str, list[str]]]:
    """One contiguous list run -> (head, clauses) pairs. This is where the regulator's sentence is
    put back together.

    LlamaParse flattens a regulator's SUB-CLAUSES into peer bullets. FATF really wrote:

        - Making frequent transfers in a certain period of time (...) to the same VA account -
            - by more than one person;
            - from the same IP address by one or more persons; or
            - concerning large amounts.

    and it arrives as four sibling bullets. Embedded as four chunks, three of them are not
    indicators at all — 'by more than one person;' asserts nothing — and the real red flag has been
    decapitated. The signal that separates them is TYPOGRAPHIC and reliable: a clause starts with a
    LOWERCASE letter; an indicator starts with a capital. (A colon rule would miss this one entirely:
    the parent ends with an EN-DASH.)

    Note this is NOT the same as fin-2010-a001's lead-in, whose children start with CAPITALS and ARE
    independent indicators that merely need the qualifier carried onto them. Both shapes are real,
    they are distinguished here, and both are asserted in tests/test_regulatory_corpus.py.
    """
    groups: list[tuple[str, list[str]]] = []
    for it in items:
        first = next((ch for ch in it if ch.isalpha()), "")
        if first and first.islower() and groups:
            groups[-1][1].append(it)      # a continuation clause of the indicator above it
        else:
            groups.append((it, []))
    return groups


def chunk_document(stem: str) -> list[Chunk]:
    """Parsed markdown -> ordered red-flag chunks. Pure: no DB, no network, no OpenAI."""
    prof = PROFILES[stem]
    md = (CORPUS_DIR / f"{stem}.md").read_text(encoding="utf-8")
    item_re = _NUMBER if prof.marker == "numbered" else _BULLET

    chunks: list[Chunk] = []
    part: str | None = None
    section: str | None = None
    suppressed = False      # inside an EXCLUDED region (table of contents, case study, references)
    run: list[str] = []     # the CONTIGUOUS list run being accumulated (a blank line ends it)
    run_done = False        # for `numbered`: only the FIRST run in a section is the real list

    def flush() -> None:
        """Emit the accumulated run. A run is grouped, THEN emitted, because whether a bullet is an
        indicator or a clause of the one above it is only knowable in the run's context."""
        nonlocal run
        items, run = run, []
        if not items or suppressed:
            return
        # A chunk hangs off a SECTION, or — where a document's red flags sit directly under a
        # top-level category with no sub-heading, as three of FATF's six do — off the PART itself.
        # Requiring a section silently deleted those three whole categories.
        if section is None and part is None:
            return
        if prof.only and not (section and _matches(prof.only, section)):
            return

        lead_in: str | None = None
        for head, clauses in _group(items):
            body = " ".join([head, *clauses]).strip()
            if body.endswith(":"):
                # A lead-in QUALIFIES what follows and asserts nothing alone. Carry it, don't emit
                # it; its children would otherwise read as unconditional indicators.
                lead_in = body.rstrip(":").strip()
                continue
            # The length floor applies only to a standalone indicator. A clause-bearing or
            # lead-in-qualified chunk draws its meaning from context, not from its own length.
            if lead_in is None and not clauses and len(body) < _MIN_BODY:
                continue
            chunks.append(
                Chunk(
                    source=prof.source,
                    doc_label=prof.label,
                    part=part,
                    section=section,
                    lead_in=lead_in,
                    ordinal=len(chunks),
                    body=body,
                    source_lines=(head, *clauses),
                )
            )

    for raw in md.splitlines():
        stripped = raw.strip()

        if stripped.startswith("#"):
            head = stripped.lstrip("# ").strip()
            # TRANSPARENT. Page furniture is not a boundary: the section it interrupts CONTINUES.
            # FFIEC's "Funds Transfers" resumes with three more red flags after the running header.
            if _matches(prof.furniture, head):
                continue
            flush()
            if _matches(prof.parts, head):
                part, section, run_done, suppressed = head, None, False, False
            elif _matches(prof.exclude, head):
                # OPAQUE. Everything under this is suppressed until the next real section. Collapsing
                # this into `furniture` ingested FATF's TABLE OF CONTENTS as red-flag chunks.
                section, run_done, suppressed = None, False, True
            else:
                section, run_done, suppressed = head, False, False
            continue

        if not stripped:
            flush()                            # a blank line ENDS the run — this is what separates
            continue                           # FinCEN's 5 red flags from its footnotes 6 and 7

        m = item_re.match(raw)
        if not m:
            flush()
            continue

        # A numbered document takes only the FIRST contiguous run in a section: the later detached
        # numbered paragraphs are footnote definitions and reference lists, not indicators.
        if prof.marker == "numbered":
            if run_done and not run:
                continue
            run_done = True

        run.append(_clean(m.group(1)))

    flush()
    return chunks


def chunk_all() -> list[Chunk]:
    """Every red-flag chunk across all five documents, in document order."""
    return [c for stem in PROFILES for c in chunk_document(stem)]


# ---------------------------------------------------------------------------------------------
# Retrieval — CockroachDB cosine search, on the same MVCC timeline as the genealogy.
# ---------------------------------------------------------------------------------------------

def _retrieval_sql(source: str | None):
    dim = get_settings().embedding_dim
    where = "WHERE source = :source" if source is not None else ""
    return text(
        f"""
        SELECT id, source, doc_label, part, section, lead_in, ordinal, body, embed_input,
               embedding <=> (:qvec)::VECTOR({dim}) AS distance
        FROM regulatory_corpus
        {where}
        ORDER BY distance
        LIMIT :k
        """
    )


def vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def retrieve_regulation(
    query_vec: list[float],
    *,
    k: int = 5,
    as_of: str | None = None,
    source: str | None = None,
) -> list[dict]:
    """Cosine-nearest regulatory red flags. `as_of` time-travels the retrieval with real AOST.

    ADVISORY ONLY. A returned red flag is provenance a human reads, never evidence and never a gate
    — see the module docstring. Out-of-window/malformed `as_of` -> ValueError (caller maps to 400),
    never a 500: the same contract as the deposition, the replay, and retrieve_typology.
    """
    ts_literal = normalize_as_of(as_of) if as_of else None
    params: dict = {"qvec": vec_literal(query_vec), "k": k}
    if source is not None:
        params["source"] = source
    sql = _retrieval_sql(source)
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                if ts_literal is not None:
                    # Must be the transaction's first statement; only the validated timestamp is
                    # inlined, the SELECT stays parameterized (the time_travel.py rule).
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
