"""THE REGULATORY CORPUS'S GATES. A document that silently contributes nothing must FAIL LOUDLY.

Two fidelity questions, and they are NOT the same question. Keeping them apart is the point of this
module's split with `scripts/verify_regulatory.py`:

  * CHUNKER fidelity — does the chunker reproduce the parsed markdown verbatim, or does it mangle,
    paraphrase, truncate or silently drop the regulator's text? Gated HERE, hermetically, against
    the COMMITTED markdown in data/corpus/. Runs in CI.
  * PARSE fidelity — did LlamaParse reproduce the PDF faithfully in the first place? That cannot be
    gated here: `data/raw/*.pdf` is gitignored, so CI does not have the PDFs. It is gated in
    `scripts/verify_regulatory.py`, which re-extracts the PDF text independently (pypdf) and checks
    the markdown against it. Asserting parse fidelity in CI would be asserting it against the very
    artifact whose fidelity is in question — a check that cannot fail is theatre.

Silently corrupted regulatory text is uniquely dangerous: it would be embedded, retrieved and cited
as authoritative FATF/FFIEC language while being garbage, and NOTHING downstream would catch it. The
chunk looks authoritative precisely because of the section path we attach to it.

Hermetic except where stated: the chunking tests touch no DB, no network, no OpenAI. Three tests at
the bottom read the live cluster's CATALOG (not its data), so they depend on migration 0009 having
been applied — never on an ingest having been run. NO TEST HERE DEPENDS ON A BACKFILL.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import engine
from app.services.regulation import CORPUS_DIR, PROFILES, chunk_all, chunk_document

# ============================ THE PINNED CENSUS ============================
# Measured, then pinned. These are not aspirations — they are what the shipped chunker produces
# against the committed markdown. A document dropping to 0 (the exact failure the first prototype
# hit on FIN-2014-A005, whose red flags are NUMBERED, not bulleted) fails here with its name on it.
#
# If a count MOVES, that is a capability change to notice, not a number to update. Same posture as
# Item 4's FLAG_CAPABLE soundness test and Item 5's CYCLE-inter-SCATTER-GATHER invariant.
EXPECTED: dict[str, int] = {
    "Appendix-F-Money-Laundering-and-Terrorist-Financing": 129,
    "Virtual-Assets-Red-Flag-Indicators": 59,
    "Trade-Based-Money-Laundering-Risk-Indicators": 33,
    "FIN-2014-A005": 5,
    "fin-2010-a001": 7,
}
TOTAL = 233


def test_every_document_contributes_its_pinned_number_of_red_flags():
    """A document silently contributing ZERO is the failure this exists to make impossible."""
    actual = {stem: len(chunk_document(stem)) for stem in PROFILES}
    empty = [s for s, n in actual.items() if n == 0]
    assert not empty, (
        f"DOCUMENT(S) CONTRIBUTED NOTHING: {empty}. A parsed document that yields no chunks is not "
        "an empty document — it is an extraction rule that stopped matching (FIN-2014-A005 numbers "
        "its red flags; the others bullet them). It would ship as a silently absent authority."
    )
    assert actual == EXPECTED, (
        f"THE CHUNK CENSUS MOVED.\n  expected: {EXPECTED}\n  actual:   {actual}\n"
        "This is a capability change, not a number to bump. Either the committed markdown changed "
        "(a re-parse drifted) or a spine profile changed. Find out which before touching this."
    )
    assert sum(actual.values()) == TOTAL == len(chunk_all())


def test_the_chunker_never_paraphrases_the_regulator():
    """EXTRACTION FIDELITY, executable: every chunk is composed ONLY of verbatim source lines.

    This is the gate against the quiet disaster — regulatory text mangled in transit, then embedded,
    retrieved and cited as authoritative FATF/FFIEC language while being garbage, with nothing
    downstream able to catch it. A chunk is a QUOTE or it is nothing.

    A chunk's body is the concatenation of its `source_lines`, and every one of those must appear
    verbatim in the markdown. That covers the 7 COMPOSITE chunks too — the ones where a regulator's
    sub-clauses arrived flattened into peer bullets and were reassembled. Reassembly is exactly the
    operation that could invent text, so it is the operation this checks hardest.
    """
    bad: list[str] = []
    for stem in PROFILES:
        md = (CORPUS_DIR / f"{stem}.md").read_text(encoding="utf-8")
        for c in chunk_document(stem):
            assert c.source_lines, f"{c.source}[{c.ordinal}] has no source lines"
            for line in c.source_lines:
                if line not in md:
                    bad.append(f"{c.source}[{c.ordinal}] line not in source: {line[:70]!r}")
            # The body introduces NOTHING beyond its source lines joined by a single space.
            if c.body != " ".join(c.source_lines).strip():
                bad.append(f"{c.source}[{c.ordinal}] body is not its source lines: {c.body[:70]!r}")
    assert not bad, (
        "CHUNK TEXT IS NOT VERBATIM REGULATORY TEXT — the chunker is inventing or rewriting:\n  "
        + "\n  ".join(bad[:10])
    )


def test_flattened_sub_clauses_are_reassembled_into_one_indicator():
    """FATF's sub-clauses arrive as peer bullets. Alone, three of the four are not indicators.

    The real text is one red flag with three sub-clauses. Flattened, 'by more than one person;'
    becomes a standalone chunk asserting nothing, and the actual indicator is decapitated. The
    signal is typographic — a clause starts LOWERCASE — and a colon rule would miss this one
    outright, because the parent ends with an EN-DASH.
    """
    composites = [c for c in chunk_all() if len(c.source_lines) > 1]
    assert len(composites) == 7, [c.body[:40] for c in composites]

    hit = [c for c in composites if c.body.startswith("Making frequent transfers")]
    assert len(hit) == 1, "the FATF flattened sub-list is not being reassembled"
    c = hit[0]
    assert len(c.source_lines) == 4
    assert c.body.endswith("or concerning large amounts.")
    assert "by more than one person;" in c.body

    # And the clauses never survive as chunks in their own right.
    bodies = [x.body for x in chunk_all()]
    assert "by more than one person;" not in bodies
    assert "concerning large amounts." not in bodies


def test_fincen_footnotes_are_not_ingested_as_authoritative_red_flags():
    """The contaminated-numbering hazard, pinned.

    FIN-2014-A005 emits 0 bullets and 21 numbered items; only 5 are red flags. Items "6." and "7."
    sit in the SAME block and are footnote DEFINITIONS; a trailing block is a numbered REFERENCE
    list. A naive `^\\d+\\.` regex embeds 'Often termed "operating outside the geographic footprint."'
    as a FinCEN red flag. The rule that excludes them is structural — a list is a CONTIGUOUS RUN, and
    a blank line ends it — so this test pins the behaviour, not the regex.
    """
    chunks = chunk_document("FIN-2014-A005")
    assert len(chunks) == 5, [c.body[:50] for c in chunks]

    bodies = " || ".join(c.body for c in chunks)
    for footnote in (
        "Often termed",                       # footnote 6
        "This differs from traditional",      # footnote 7
        "See, FinCEN",                        # the numbered reference list
        "Advisory FIN-2011-A009",
    ):
        assert footnote not in bodies, (
            f"a FOOTNOTE/REFERENCE was ingested as an authoritative FinCEN red flag: {footnote!r}"
        )

    # And the five real ones are the five real ones.
    assert chunks[0].body.startswith("An account opened in one state")
    assert chunks[4].body.startswith("In the case of a business account receiving out-of-state")


def test_the_ffiec_funds_transfers_collision_is_disambiguated_by_the_part():
    """THE REASON THE SECTION PATH EXISTS, asserted rather than described.

    FFIEC Appendix F has TWO sections titled "Funds Transfers" — one under Money Laundering, one
    under Terrorist Financing — and two titled "Activity Inconsistent with the Customer's Business".
    Same heading string, different meaning. Strip the PART from the path and a terrorist-financing
    query can retrieve a money-laundering red flag while looking perfectly well-sourced.
    """
    chunks = chunk_document("Appendix-F-Money-Laundering-and-Terrorist-Financing")
    ft = [c for c in chunks if c.section == "Funds Transfers"]
    assert len(ft) == 14, len(ft)

    ml = [c for c in ft if c.part and c.part.endswith("Money Laundering")]
    tf = [c for c in ft if c.part and c.part.endswith("Terrorist Financing")]
    assert len(ml) == 9 and len(tf) == 5, (len(ml), len(tf))

    # The collision is REAL: identical section, distinct paths, and the paths are what get embedded.
    assert {c.section for c in ml} == {c.section for c in tf} == {"Funds Transfers"}
    assert len({c.section_path for c in ml} | {c.section_path for c in tf}) == 2
    assert all(c.section_path in c.embed_input for c in ft)

    # The same collision, independently, on a second section title.
    inconsistent = [c for c in chunks if c.section and c.section.startswith("Activity Inconsistent")]
    assert len({c.part for c in inconsistent}) == 2, "the second collision vanished"


def test_a_lead_in_bullet_is_carried_onto_its_children_never_emitted_alone():
    """A bullet ending in ':' QUALIFIES what follows; alone it asserts nothing.

    LlamaParse flattens nesting, so "Unusual deposits occurring in combination with one or more of
    the following indicators:" and its children arrive as peers. Embedded alone, each child reads as
    an UNCONDITIONAL indicator — a stronger claim than the regulator made.
    """
    chunks = chunk_document("fin-2010-a001")
    carried = [c for c in chunks if c.lead_in]
    assert len(carried) == 3, [c.lead_in for c in carried]

    # The lead-in itself is never a chunk of its own.
    assert not any(c.body.endswith(":") for c in chunk_all())

    c = carried[0]
    assert c.lead_in == (
        "Unusual deposits occurring in combination with one or more of the following indicators"
    )
    # And it reaches the embedded string, which is the only place it can do any work.
    assert c.lead_in in c.section_path
    assert c.lead_in in c.embed_input
    assert c.embed_input.endswith(c.body)


def test_no_page_furniture_or_case_study_reaches_any_chunk():
    """Running headers, cover-page graphics artifacts, and narrative are not red flags.

    FATF's cover page parses to literal `# NODE 02` / `# BLOCK 01` headings; both FATF documents
    fuse the page number into the running header (`# 6 | VIRTUAL ASSETS ...`); FFIEC repeats its
    title as an H1 on every body page. None is a section boundary and none is an indicator.
    """
    forbidden = (
        "NODE 0", "BLOCK 0", "FFIEC BSA/AML Examination Manual", "Table of Contents",
        "Case Study", "Citing reference", "| VIRTUAL ASSETS", "| TRADE-BASED",
    )
    leaks = [
        f"{c.source}[{c.ordinal}] contains {f!r}: {c.body[:60]!r}"
        for c in chunk_all()
        for f in forbidden
        if f in c.body
    ]
    assert not leaks, "PAGE FURNITURE WAS INGESTED AS REGULATORY TEXT:\n  " + "\n  ".join(leaks)

    # AND no chunk may hang off a NON-RED-FLAG SECTION. This is a separate failure from the one
    # above and it needs its own assertion: dropping `Table of Contents` from a profile's `exclude`
    # does not orphan its bullets — it promotes "Table of Contents" to a SECTION, and the entries
    # sail through every body-text check while carrying a perfectly well-formed provenance path.
    # (Verified by breaking it: the orphan gate stayed green. Only this catches it directly.)
    NOT_A_SECTION = (
        "Table of Contents", "Acronyms", "Introduction", "Conclusion", "References",
        "Case Study", "Citing reference", "FATF REPORT", "NODE ", "BLOCK ",
    )
    bad_sections = sorted({
        f"{c.source}: section={c.section!r}"
        for c in chunk_all()
        for n in NOT_A_SECTION
        if (c.section or "").startswith(n) or (c.part or "").startswith(n)
    })
    assert not bad_sections, (
        "CHUNKS ARE FILED UNDER A NON-RED-FLAG SECTION — they will retrieve as authoritative "
        "indicators and their provenance path will look impeccable:\n  " + "\n  ".join(bad_sections)
    )


def test_every_chunk_carries_a_resolvable_section_path_and_a_real_provenance():
    """No chunk may be an ORPHAN: a retrieved fragment must carry where it came from.

    This gate earned its place immediately. It caught FATF's TABLE OF CONTENTS being ingested as red
    flags — its entries are bullets, they arrived with no section, and they would have been embedded
    and retrieved as authoritative FATF indicators.

    A chunk hangs off a SECTION, or off the PART itself where a document files red flags directly
    under a top-level category with no sub-heading — which three of FATF's six categories do. The
    first version of this test demanded a section, and silently deleted those three whole categories
    (86 -> 43 chunks) while passing. The assertion is `section OR part`, and it is deliberately not
    `section` alone.
    """
    for c in chunk_all():
        assert c.doc_label and c.source, c
        assert c.section or c.part, (
            f"chunk {c.source}[{c.ordinal}] has neither section nor part — its path is a bare "
            f"document label and its provenance is unresolvable: {c.body[:60]!r}"
        )
        assert c.section_path.startswith(c.doc_label)
        assert c.embed_input == f"{c.section_path}: {c.body}"

    # The three FATF categories that file red flags directly under the part, with no sub-section.
    fatf = chunk_document("Virtual-Assets-Red-Flag-Indicators")
    part_only = {c.part for c in fatf if c.section is None}
    assert len(part_only) == 3, sorted(p or "" for p in part_only)


def test_the_deciding_path_never_imports_the_regulatory_corpus():
    """THE BRAKE MUST NOT SEE REGULATORY CHUNKS — and the separate table is only half of that.

    The table makes `retrieve_typology()` (whose SQL says FROM typology_corpus) physically incapable
    of returning a regulatory chunk. This makes the converse true: no module on the deciding path may
    reach for `retrieve_regulation()` either. A retrieved red flag is CONTEXT, never evidence — Item
    4 measured that retrieval distance gates NOTHING, in either direction. Wiring this into a verdict
    would rebuild MARGIN_FLOOR: evidence that gates nothing must not start gating.

    Same AST shape as tests/test_oracle_boundary.py, and the same reason: a comment would not hold.
    """
    from tests.test_oracle_boundary import DECIDING_PATH

    banned = {"retrieve_regulation", "regulatory_corpus", "RegulatoryCorpus"}
    violations: list[str] = []
    for path in DECIDING_PATH:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            hit = None
            if isinstance(node, ast.Name) and node.id in banned:
                hit = node.id
            elif isinstance(node, ast.Attribute) and node.attr in banned:
                hit = node.attr
            elif isinstance(node, ast.ImportFrom) and (node.module or "").endswith("regulation"):
                hit = f"from {node.module}"
            if hit:
                violations.append(f"{path.name}:{node.lineno} reaches for `{hit}`")

    assert not violations, (
        "THE DECIDING PATH CAN SEE THE REGULATORY CORPUS. A red flag is provenance, not a witness; "
        "only structure over the money-flow graph may authorize a FLAG:\n  " + "\n  ".join(violations)
    )


# ==================== CATALOG TESTS — schema only, never data ====================

@pytest.mark.asyncio
async def test_exactly_one_vector_index_exists_and_it_is_the_one_that_works():
    """THE WHOLE VECTOR-INDEX STORY, PINNED — read from the live catalog, never from a migration.

    This test used to pin the OPPOSITE state (`..._and_the_two_legacy_ones_are_still_l2`). It was
    written to FAIL the day someone touched the two dead indexes, so that the change could not
    happen silently. It did its job: migration 0010 is that day, and this is the decision it forced.

    THE HISTORY, because it is the reason this project distrusts its own documents:
      * `beliefs` (0002) and `typology_corpus` (0005) were created with a bare
        `CREATE VECTOR INDEX ... (embedding)`. CockroachDB's default opclass is `vector_l2_ops`,
        which accelerates the L2 operator `<->` ONLY — while BOTH of their queries rank with `<=>`,
        COSINE. Neither index was ever selected by the planner. Not at 4 rows. Not ever.
      * Item 3 recorded the true FULL-SCAN observation with an INVENTED cause ("only 4 rows"), and a
        green check stood behind it for months.
      * The correction that replaced it then claimed `typology_corpus`'s query "has no WHERE and no
        JOIN", so an opclass flip would revive the index. That was ALSO false: every one of the 12
        row-returning `retrieve_typology()` call sites passes `source=SOURCE`, so the real query
        filters `WHERE source = :source`, and a vector index needs its PREFIX columns constrained.
        `(embedding)` has none. The flip changes NO PLAN.

    So neither index could be repaired by the repair everyone assumed. They were DROPPED (0010).
    The one that could have been made to work — `typology_corpus` via a `(source, embedding
    vector_cosine_ops)` prefix index — was rejected on purpose: it activates an APPROXIMATE C-SPANN
    search, and Item 4's Gate 0 is a set-membership test on exactly the top-3 of a k=3 retrieval over
    a FOUR-document corpus. Measured safe today (scripts/probe_vector_opclass.py: 0 top-3 changes
    over 1,572 adversarial queries AND over the real agent queries) — but safe only because 4 rows
    sit in ONE C-SPANN partition. That is luck of scale, not a property of the design.

    What is left is checkable and true: THREE vector indexes were declared, TWO could never be used
    by their own queries, they are gone, and the ONE that works remains.
    """
    async with engine.connect() as c:
        found: dict[str, str] = {}
        for table in ("beliefs", "typology_corpus", "regulatory_corpus"):
            ddl = str((await c.execute(text(f"SHOW CREATE TABLE {table}"))).all()[0][1])
            for line in ddl.splitlines():
                if "VECTOR INDEX" in line:
                    found[table] = line.strip()

    for dead in ("beliefs", "typology_corpus"):
        assert dead not in found, (
            f"A VECTOR INDEX IS BACK ON `{dead}`: {found.get(dead)}\n"
            f"Migration 0010 dropped it because NO query in this system can use it — its WHERE "
            f"clause is not a prefix constraint, so the planner cannot select it whatever the "
            f"opclass. If you have made it selectable, you have made retrieval APPROXIMATE where it "
            f"was EXACT. For typology_corpus that silently changes the brake's input (Item 4's "
            f"Gate 0 reads the top-3 of a 4-document corpus) and invalidates Item 8's golden set. "
            f"Re-measure with scripts/probe_vector_opclass.py before you accept this."
        )

    assert "vector_cosine_ops" in found.get("regulatory_corpus", ""), (
        "regulatory_corpus's index is NOT cosine — so it cannot serve the `<=>` query it exists "
        f"for, and this project is back to ZERO working vector indexes: {found.get('regulatory_corpus')}"
    )


@pytest.mark.asyncio
async def test_regulatory_corpus_is_fk_isolated_in_both_directions():
    """No FK into the five-table moat, into aml_*, or into typology_corpus. Same as Item 3."""
    async with engine.connect() as c:
        fks = (await c.execute(text(
            "SELECT tc.table_name AS child, ccu.table_name AS parent "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY'"
        ))).all()
    touching = sorted({(ch, pa) for ch, pa in fks if "regulatory_corpus" in (ch, pa)})
    assert not touching, f"an FK touches regulatory_corpus: {touching}"


@pytest.mark.asyncio
async def test_regulatory_corpus_is_absent_from_the_demo_databases_metadata():
    """CorpusBase, not Base — so `Base.metadata.create_all` PHYSICALLY cannot create it.

    The demo database (Roadmap Item 0) is provisioned by Base.metadata.create_all. If this table
    were on Base, a demo run would silently create an empty `regulatory_corpus` and the five-table
    moat would quietly become six. Isolation by Python object identity, not by discipline —
    ARCHITECTURE §7, guard 1.
    """
    from app.corpus_models import CorpusBase, RegulatoryCorpus
    from app.db import Base

    assert "regulatory_corpus" in CorpusBase.metadata.tables
    assert "regulatory_corpus" not in Base.metadata.tables
    assert RegulatoryCorpus.__table__.metadata is CorpusBase.metadata
    assert RegulatoryCorpus.__table__.metadata is not Base.metadata
