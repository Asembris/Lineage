"""The GROUNDED backfill (G4): the azure belief decides on 1,500 REAL IBM money-flow edges.

The first time any decision in this system cites real external evidence. A real causal chain of
real rows: azure-0 forms a laundering belief -> it is inherited down the azure spine -> the LIVING
azure-7 applies it to a REAL `aml_transactions` row -> the decision cites that row through a real
database-enforced FK -> `is_fraud` comes from the real `is_laundering` label.

    !!  THIS SCRIPT DOES NOT RESEED, AND MUST NEVER LEARN TO.  !!

`seed/backfill_decisions.py` OPENS with `await run_seed()`, and `seed.seed()` DELETEs every row of
`decisions`. So a reseed here would destroy the card backfill, and a later card backfill destroys
these AML rows. The two are not interchangeable and their order is not free:

    RESTORE PROCEDURE — TWO ORDERED COMMANDS, IN THIS ORDER:
        python -m seed.backfill_decisions        # reseeds, then writes 4,000 card decisions
        python -m seed.backfill_aml_decisions    # APPENDS 1,500 AML decisions (no reseed)

Running only the first leaves a world with no AML decisions. Running only the second on a fresh
cluster would leave a world with AML decisions and no staleness curve. This script therefore
REFUSES TO RUN (loudly, non-zero exit) if the card decisions are absent, rather than silently
producing a half-populated world — the failure mode that is hardest to notice and easiest to
demo by accident.

THE DECIDER IS THE FROZEN, LABEL-FREE WITNESS — NO LLM, MARGINAL COST $0
------------------------------------------------------------------------
`app/services/aml_seam.decide_all()` is a pure function of `aml_graph.Graph`, which is built from
a SELECT that projects no label. Item 6 reached this conclusion itself ("it must be the
DETERMINISTIC witness, never the LLM verdict"): a model claim is the one artifact this design
cannot re-derive, and embedding one here would create the persistence requirement the project has
deliberately deferred twice. So this backfill is deterministic, free, and offline-replayable.

TWO PHASES, AND THE ORDER IS THE INTEGRITY ARGUMENT
---------------------------------------------------
  Phase 1 — decide.  Every one of the 1,500 verdicts is computed from the unlabeled graph. The
                     label query has not run. It CANNOT have influenced anything.
  Phase 2 — label.   ONLY THEN is `is_laundering` read and attached as `is_fraud`.

This is `seed/backfill_decisions.py`'s exact discipline (`_decision_from` computes the verdict
from `txn.on_pattern`, an observable feature, before the row is stamped with `is_fraud=txn.is_fraud`,
ground truth the rule never saw). tests/test_oracle_boundary.py asserts that this module's ONLY
reference to the answer key is the phase-2 SQL below.

EVERY DECISION CARRIES ONE FIXED `decided_at`, AND NOT THE TRANSACTION'S OWN TIMESTAMP
--------------------------------------------------------------------------------------
The agent evaluated the whole extract at ONE instant, so every row shares one `decided_at`.

**WHY — DO NOT "IMPROVE" THIS.** Using each transaction's real `ts` would look like a fidelity
improvement and would silently reintroduce the BASE-RATE MIRAGE: Item 1's ingestion walks the CSV
in file order and stops taking benign rows once the 4:1 cap fills, so **1,092 of 1,200 benign rows
(91%) land on the first of the extract's 8.35 days**. Windowing these decisions by transaction time
produces a spectacular, reproducible, statistically overwhelming 0.974 -> 0.000 "rot" curve
(Cochran-Armitage z = -24.90) that is 100% an artifact of the SAMPLE, not the belief — windows 6
and 7 contain literally zero benign transactions. Base-rate-free measures show the belief's
discriminative power is FLAT (per-window recall 1.000 in every window; precision trend p = 0.55).
The belief is IMPERFECT, NOT STALE, and that is what the seam may claim.

With every decision at one instant there are **no time windows to draw a curve from**, so the trap
is not merely warned about — it is STRUCTURALLY UNAVAILABLE. Same move as Item 1 putting `aml_*` on
a separate `AmlBase` metadata so `create_all` physically cannot reach it, and as migration 0007's
CHECK making a fabricated merchant impossible to write: make the wrong thing unrepresentable, do
not rely on the next session reading the note. See NOTES "THE BASE-RATE MIRAGE".

Consequently `recompute_belief_performance` is NEVER called for this belief. Item 6's step 4 is CUT.

`txn_ref` CARRIES THE WITNESS OUTCOME — the 65.3% disclosure, made reachable from the DATA
------------------------------------------------------------------------------------------
For an AML decision the real transaction reference is the `aml_transaction_id` FK, so `txn_ref`
(NOT NULL, free-form) is redundant as a reference and free to carry the decision's BASIS:
`aml:MATCH` | `aml:CONCLUSIVE_NO` | `aml:INCONCLUSIVE`. This matters because TWO outcomes map to
`approve`, so the verdict alone cannot distinguish "we searched and there is no cycle" (463 edges)
from "we could not tell" (980 edges, 65.3%, silently approving 252 of the 300 laundering rows).
Without this, that split is recoverable only by re-running the witness — i.e. only by someone who
already knows to look. With it, the most important caveat about this belief is one GROUP BY away:

    SELECT txn_ref, verdict, count(*), sum(CASE WHEN is_fraud THEN 1 ELSE 0 END)
    FROM decisions WHERE driving_belief_id = <azure> GROUP BY 1, 2;

and it is already served by `GET /decisions` and rendered in the console feed.

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m seed.backfill_aml_decisions
"""

import asyncio
import datetime as dt
import sys
import time
import uuid

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.models import Decision
from app.services.aml_graph import Outcome, load_graph
from app.services.aml_seam import (
    TYPOLOGY,
    SeamDecision,
    census,
    decide_all,
    txn_ref_for,  # the SOLE writer of the basis tag; migration 0008 pins its vocabulary
)
from seed.seed import aid, bid

AML_BELIEF = bid("aml-cycle")
CARD_BELIEF = bid("origin")  # crimson's — its backfill is this script's precondition, not its peer
DECIDING_AGENT = aid("azure-7")  # the LIVING holder, acting on azure-0's belief. Never met it.

# The ONE instant the agent evaluated the extract at. Fixed, not `now()`, so the backfill is
# reproducible byte-for-byte. See the module docstring for why it is a single instant at all.
DECIDED_AT = dt.datetime(2026, 7, 12, 12, 0, 0, tzinfo=dt.timezone.utc)

# `txn_ref_for` is imported from app.services.aml_seam — the decider that owns the outcome
# vocabulary. It used to be defined here, which meant the tag's spelling lived in the WRITER while
# migration 0008's CHECK enforces it and the read surface projects it: three copies, free to drift.
# One home now, and a test asserts the migration's literals match it.

# ---------------------------------------------------------------------------------------------
# PHASE 2 ONLY. This is the module's SOLE reference to the answer key, and it runs strictly after
# every verdict already exists. tests/test_oracle_boundary.py asserts that is still true.
# ---------------------------------------------------------------------------------------------
# The label is ALIASED on the way out, so the answer key's name appears EXACTLY ONCE in this
# module — here. Every consumer below reads `ground_truth`, and the tripwire can therefore pin
# the read to this one constant instead of tolerating the token scattered through the file.
_LABELS_SQL = text("SELECT id, is_laundering AS ground_truth FROM aml_transactions")

# Phase-1 inputs: the row facts a decision needs that the WITNESS does not use. `payment_currency`
# is required by migration 0007's ck_decisions_kind (an AML row must name its currency — the
# extract spans 14, so a bare Numeric would mean a different thing per row). Not a label.
_ROWS_SQL = text("SELECT id, amount_paid, payment_currency FROM aml_transactions")

# The card precondition is checked against the CRIMSON belief specifically, and against its
# measured curve — not against "any non-AML driven decision". A looser check passes on leftover
# rows from an interrupted test run (observed: 8 stray rows satisfied a `driving_belief_id IS NOT
# NULL` count and let this backfill proceed into a world that had no card backfill at all). The
# thing that must exist is `backfill_decisions`' OUTPUT: its 4,000 driven rows and its 8-window
# staleness curve.
_PRECHECK_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM beliefs WHERE id = :b)                       AS belief,
      (SELECT count(*) FROM belief_inheritance WHERE belief_id = :b)     AS edges,
      (SELECT count(*) FROM aml_transactions)                            AS txns,
      (SELECT count(*) FROM decisions WHERE driving_belief_id = :card)   AS card_decisions,
      (SELECT count(*) FROM belief_performance WHERE belief_id = :card)  AS card_windows,
      (SELECT count(*) FROM decisions WHERE driving_belief_id = :b)      AS existing_aml
    """
)


class PreconditionFailed(SystemExit):
    """Refuse loudly rather than build half a world."""


async def precheck() -> dict:
    """Refuse to run unless the world this backfill APPENDS to is actually there.

    The card decisions are the load-bearing check. This script does not reseed (see the module
    docstring), so if `backfill_decisions` has not run, the cluster has no genealogy-scale
    decision history and no staleness curve — and an AML-only world would look complete while
    silently missing the entire Phase-2 story. Fail loudly; never half-populate.
    """
    async with engine.connect() as c:
        r = (
            await c.execute(_PRECHECK_SQL, {"b": AML_BELIEF, "card": CARD_BELIEF})
        ).mappings().one()

    problems = []
    if not r["belief"]:
        problems.append(
            f"the azure belief {AML_BELIEF} does not exist — run `python -m seed.seed` "
            "(or the card backfill, which reseeds) first"
        )
    if r["edges"] != 7:
        problems.append(f"expected 7 azure inheritance edges, found {r['edges']}")
    if r["txns"] != 1500:
        problems.append(
            f"expected 1,500 aml_transactions, found {r['txns']} — run "
            "`scripts/ingest_aml.py`. Do NOT re-ingest or re-sample: that would move Item 4's "
            "asserted constants and Item 7's eval inputs."
        )
    if not r["card_decisions"] or not r["card_windows"]:
        problems.append(
            f"THE CARD BACKFILL HAS NOT RUN (crimson decisions={r['card_decisions']}, "
            f"belief_performance windows={r['card_windows']}).\n"
            "    This script APPENDS; it NEVER reseeds, because a reseed here would DELETE the\n"
            "    card decisions. Run the two backfills IN ORDER:\n"
            "        python -m seed.backfill_decisions       (reseeds + 4,000 card decisions)\n"
            "        python -m seed.backfill_aml_decisions   (this script — appends 1,500)"
        )

    if problems:
        raise PreconditionFailed(
            "\n=== REFUSING TO RUN — the world is not in a state this backfill can append to ===\n"
            + "\n".join(f"  * {p}" for p in problems)
            + "\n"
        )
    return dict(r)


async def backfill() -> None:
    pre = await precheck()
    if pre["existing_aml"]:
        print(
            f"note: {pre['existing_aml']} AML decisions already exist for this belief — "
            "replacing them (idempotent re-run)."
        )
        async with engine.begin() as c:
            await c.execute(
                text("DELETE FROM decisions WHERE driving_belief_id = :b"), {"b": AML_BELIEF}
            )

    t0 = time.perf_counter()

    # ---- PHASE 1: DECIDE. The label query has not run. It cannot have influenced anything. ----
    print("\n=== PHASE 1 — the frozen, label-free witness decides ===", flush=True)
    async with engine.connect() as c:
        graph = await load_graph(conn=c)  # SELECTs no label column
        facts = {
            r["id"]: (r["amount_paid"], r["payment_currency"])
            for r in (await c.execute(_ROWS_SQL)).mappings().all()
        }
    decisions: list[SeamDecision] = decide_all(graph)
    counts = census(decisions)
    print(
        f"  {len(decisions)} verdicts from {len(graph.by_id)} unlabeled edges  "
        f"({time.perf_counter() - t0:.1f}s)",
        flush=True,
    )

    # ---- PHASE 2: LABEL. Ground truth the rule never saw, attached after the fact. ----
    print("=== PHASE 2 — ground truth attached to is_fraud (the rule never saw it) ===", flush=True)
    async with engine.connect() as c:
        labels = {
            r["id"]: bool(r["ground_truth"])
            for r in (await c.execute(_LABELS_SQL)).mappings().all()
        }

    rows = []
    for d in decisions:
        amount, currency = facts[d.txn_id]
        rows.append(
            Decision(
                id=uuid.uuid5(uuid.NAMESPACE_OID, f"aml-decision:{d.txn_id}"),
                agent_id=DECIDING_AGENT,
                txn_ref=txn_ref_for(d.witness_outcome),  # the BASIS — see the module docstring
                merchant=None,      # a bank-to-bank transfer has no merchant (migration 0007)
                amount=amount,
                amount_currency=currency,
                verdict=d.verdict,
                driving_belief_id=AML_BELIEF,
                confidence=None,    # the witness is DETERMINISTIC; it produces no confidence
                decided_at=DECIDED_AT,  # ONE instant. Not the transaction's ts. See the docstring.
                is_fraud=labels[d.txn_id],
                aml_transaction_id=d.txn_id,
            )
        )

    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()

    print(f"=== inserted {len(rows)} AML decisions in {time.perf_counter() - t0:.1f}s ===")
    await report(decisions, labels, counts)


async def report(decisions, labels, counts) -> None:
    total = len(decisions)
    print(f"\n=== THE SEAM: {TYPOLOGY} belief applied to {total} real IBM money-flow edges ===")
    print(f"  formed by azure-0  ->  inherited down 7 edges  ->  applied by the LIVING azure-7")
    print(f"  every decision cites a real aml_transactions row through a real FK\n")

    hdr = f"{'witness outcome':<16}{'verdict':<10}{'edges':>7}{'share':>9}{'laundering':>12}{'benign':>8}"
    print(hdr)
    print("-" * len(hdr))
    approved_fraud = 0
    for o in (Outcome.MATCH, Outcome.CONCLUSIVE_NO, Outcome.INCONCLUSIVE):
        sub = [d for d in decisions if d.witness_outcome is o]
        laund = sum(1 for d in sub if labels[d.txn_id])
        verdict = sub[0].verdict if sub else "-"
        if verdict == "approve":
            approved_fraud += laund
        print(
            f"{o.value:<16}{verdict:<10}{len(sub):>7}{len(sub) / total * 100:>8.1f}%"
            f"{laund:>12}{len(sub) - laund:>8}"
        )

    inc = counts[Outcome.INCONCLUSIVE]
    inc_laund = sum(1 for d in decisions if d.witness_outcome is Outcome.INCONCLUSIVE and labels[d.txn_id])
    total_laund = sum(1 for d in decisions if labels[d.txn_id])
    blocked_laund = sum(1 for d in decisions if labels[d.txn_id] and d.verdict == "blocked")

    print(
        f"\n*** THE DISCLOSURE — quote this wherever these decisions are quoted ***\n"
        f"  INCONCLUSIVE -> approve is {inc}/{total} = {inc / total * 100:.1f}% OF THE EXTRACT.\n"
        f"  It is a DISCLOSED MODELING CHOICE (a real system lets a payment through absent\n"
        f"  evidence), and it is not a corner case: it silently approves {inc_laund} laundering rows.\n"
        f"  Of {total_laund} laundering transactions the belief BLOCKED {blocked_laund} and APPROVED"
        f" {approved_fraud}.\n"
        f"  (This is NOT '728 / 48.5%'. 728 is the benign-only inconclusive subset — an understated\n"
        f"   figure this project has now introduced, and corrected, twice.)\n"
    )
    print(
        "  The belief's honest failure mode is CONSTANT, NOT STALE: its error rate is flat over\n"
        "  time (per-window recall 1.000 in every window). belief_performance is deliberately NOT\n"
        "  computed for this belief — see NOTES 'THE BASE-RATE MIRAGE'.\n"
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
