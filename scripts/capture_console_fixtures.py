"""Capture the console's REAL request set from the live cluster, for the browser guard's mock.

============================ WHY THIS SCRIPT EXISTS ============================

`frontend/tests-e2e/geometry.spec.ts` renders the real console in a real browser and asserts the
one geometry invariant that only a browser can see: the Confirm button must NOT land on the arm
button's footprint (two clicks of muscle memory in one screen position would be an irreversible
fleet-wide write). To reach the armed state the console must fetch real data — so the browser guard
serves it from a MOCK, intercepted at `page.route()`.

A mock invites the obvious objection, and this project has been burned by proxies five times: a
mock that drifts from the real API would let the guard pass while the console breaks. So the
fixtures are NOT hand-written. They are CAPTURED, verbatim, from the live cluster through the real
FastAPI app — and `tests/test_console_fixtures.py` REPLAYS every recorded request against the live
cluster on every backend push, asserting the captured body still has the same shape the live one
does. Backend CI sees every backend change (`paths-ignore` skips only frontend-ONLY pushes), so the
mock cannot silently drift from the API it imitates.

    frontend-ci   RUNS the geometry guard against these fixtures  (has Node)
    ci.yml        PINS these fixtures to the real API + cluster    (has DATABASE_URL)

Hand-written fixtures would have been the co-author problem that blinded check C of the composition
guard: a fixture written by the guard's author tests what that author thought of. A capture cannot
flatter the guard, because it did not come from the guard's author. It came from the cluster.

=========================== THE ONE NORMALIZATION ============================

`as_of` is NOT keyed on its value. TimeTravel computes `new Date(Date.now() - 20_000)` at click
time, so the value is different on every render and no captured key could ever match it. The key
therefore normalizes any `as_of` to the literal `<ANY>`, and the replay test substitutes a FRESH
past instant when it re-issues (a captured timestamp replayed days later would fall outside the GC
window and 400 — the fixture would rot into a false failure). This is the only normalization, it is
narrow, and it is applied identically by the capture, the mock and the pin.

Run:  python -m scripts.capture_console_fixtures
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import httpx
from sqlalchemy import text

from app.db import engine
from app.main import app
from app.services.aml_graph import Outcome, load_graph
from app.services.aml_interrogate import TraversalKind, interrogate

OUT = Path(__file__).resolve().parents[1] / "frontend" / "tests-e2e" / "fixtures" / "console.json"

#: The console's page size (`useConsoleData.PAGE_SIZE`; the backend's `Query(le=200)` max).
PAGE = 200


def key(path: str, params: dict[str, object] | None = None) -> str:
    """The canonical request key. Query params SORTED, `as_of` normalized to `<ANY>`.

    The Playwright mock (`tests-e2e/mock.ts`) and the replay pin (`tests/test_console_fixtures.py`)
    canonicalize identically — one key shape, three consumers.
    """
    items = sorted((k, "<ANY>" if k == "as_of" else str(v)) for k, v in (params or {}).items())
    return f"GET {path}?{urlencode(items)}" if items else f"GET {path}"


async def pick_geometry_subjects(c) -> dict[str, str]:
    """The three AML subjects whose GEOMETRY the browser guard measures.

    ============ THE OLD PICK WAS AN ACCIDENT, AND IT WOULD HAVE MADE THE GUARD VACUOUS ============

    `aml_transaction_id` below is chosen by `ORDER BY decided_at DESC, id DESC LIMIT 1`. But ALL
    1,500 AML rows share ONE `decided_at` (the base-rate-mirage guard put it there deliberately), so
    that ORDER BY collapses to "whatever has the max id" — and the row it lands on witnesses NOTHING.
    All four of its witnesses are NONE.

    THE GUARD'S SUBSTRATE WAS NEVER CHOSEN. It was a side effect of a different invariant. A geometry
    guard written against that fixture would render ZERO GEOMETRY and pass — green for its whole life,
    measuring nothing, which is the ninth time this project has come within one step of shipping
    exactly that. So the geometry subjects are picked ON PURPOSE, by running the real witness against
    the real graph, and each one is picked to make a SPECIFIC invariant observable:

      ring      — a CYCLE witness. The drawing is the whole cycle, and the subject is cited.
      parallel  — a witness citing TWO TRANSACTIONS ON ONE ACCOUNT PAIR. The money-flow graph is a
                  MULTIGRAPH; a layout keying edges by (from,to) would draw 3 lines where the
                  evidence has 4. Without this subject the edge-count assertion cannot fail.
      omits     — a witness that does NOT cite the subject (75 of 107 GATHER-SCATTER matches). Without
                  it, "the subject is marked only when cited" cannot fail either.

    ALL THREE MUST BE ON THE FEED'S FIRST PAGE, because the guard clicks through the feed to reach
    them and cannot page. That is asserted, not hoped for: if the extract ever changes so that one is
    unreachable, this raises instead of silently capturing a subject the guard cannot navigate to.
    """
    rows = (
        await c.execute(
            text(
                "SELECT aml_transaction_id FROM decisions WHERE aml_transaction_id IS NOT NULL "
                f"ORDER BY decided_at DESC, id DESC LIMIT {PAGE}"
            )
        )
    ).scalars().all()

    g = await load_graph(c)
    picked: dict[str, str] = {}
    for txn_id in rows:
        edge = g.by_id.get(txn_id)
        if edge is None:
            continue
        witnesses, _ = interrogate(g, edge)
        for w in witnesses:
            if w.outcome is not Outcome.MATCH:
                continue
            ids = list(w.transaction_ids)
            if w.kind is TraversalKind.RING:
                picked.setdefault("aml_ring_txn_id", str(txn_id))
            pairs = Counter((g.by_id[i].src, g.by_id[i].dst) for i in ids if i in g.by_id)
            if any(n > 1 for n in pairs.values()):
                picked.setdefault("aml_parallel_txn_id", str(txn_id))
            if edge.id not in ids:
                picked.setdefault("aml_omits_subject_txn_id", str(txn_id))

    missing = {"aml_ring_txn_id", "aml_parallel_txn_id", "aml_omits_subject_txn_id"} - picked.keys()
    if missing:
        raise SystemExit(
            f"no subject on the feed's FIRST PAGE exhibits: {sorted(missing)}.\n"
            "The geometry guard clicks through the feed and cannot page, so it could not reach one.\n"
            "Do NOT weaken the guard to accept this — the invariant it would stop covering is the\n"
            "one that keeps the drawing from understating the evidence."
        )
    return picked


async def pick_subjects() -> dict[str, str]:
    """The real subjects the guard drives, chosen FROM THE CLUSTER — never hardcoded.

    - the crimson belief: the one with a measured `belief_performance` curve (Time-travel has
      nothing to draw without it, and the armed footer is only reachable through Time-travel being
      open — that is the whole point of the Inspector-fold fix).
    - a card decision it drove: the Investigation subject.
    - an AML transaction: the evidence surface, for the `is_fraud`-absent check.
    - three AML transactions with real GEOMETRY — see pick_geometry_subjects, and read it before
      touching any of this: the obvious pick renders nothing and the guard passes anyway.
    """
    async with engine.connect() as c:
        belief_id = (
            await c.execute(
                text(
                    "SELECT b.id FROM beliefs b JOIN belief_performance p ON p.belief_id = b.id "
                    "WHERE b.status = 'active' GROUP BY b.id "
                    "ORDER BY count(p.id) DESC LIMIT 1"
                )
            )
        ).scalar_one()

        # A decision the crimson belief actually drove. `is_fraud` preferred: that is the decision
        # the console exists to investigate, and it is the one the fold was measured against.
        decision = (
            await c.execute(
                text(
                    "SELECT id, agent_id FROM decisions WHERE driving_belief_id = :b "
                    "AND aml_transaction_id IS NULL "
                    "ORDER BY is_fraud DESC, decided_at DESC LIMIT 1"
                ),
                {"b": belief_id},
            )
        ).one()

        # An AML transaction whose decision is in the feed's first page (the guard clicks through
        # the feed to reach it, so it must be reachable without paging).
        txn_id = (
            await c.execute(
                text(
                    "SELECT aml_transaction_id FROM decisions WHERE aml_transaction_id IS NOT NULL "
                    "ORDER BY decided_at DESC, id DESC LIMIT 1"
                )
            )
        ).scalar_one()

        geometry = await pick_geometry_subjects(c)

    return {
        "belief_id": str(belief_id),
        "decision_id": str(decision.id),
        "agent_id": str(decision.agent_id),
        "aml_transaction_id": str(txn_id),
        **geometry,
    }


async def main() -> None:
    subjects = await pick_subjects()
    belief, agent, txn = (
        subjects["belief_id"],
        subjects["agent_id"],
        subjects["aml_transaction_id"],
    )

    # A real past instant inside the GC window — the same thing TimeTravel computes at click time.
    iso_past = (datetime.now(UTC) - timedelta(seconds=20)).isoformat().replace("+00:00", "Z")

    # EXACTLY the requests the console issues, in the order the guard drives them. Nothing
    # speculative: a fixture the console never asks for is dead weight, and a request the console
    # DOES make that is missing here fails the guard loudly (the mock errors on an unmocked
    # request rather than letting one escape).
    requests: list[tuple[str, dict[str, object] | None]] = [
        # --- mount (App's default filter is `kind = null`: the unfiltered fleet feed) ---
        ("/agents", None),
        ("/beliefs", None),
        ("/decisions", {"limit": PAGE, "offset": 0}),
        # the kind census (`countDecisions`, limit=1, read `total`)
        ("/decisions", {"limit": 1}),
        ("/decisions", {"kind": "card", "limit": 1}),
        ("/decisions", {"kind": "aml", "limit": 1}),
        # the seam census
        ("/decisions", {"kind": "aml", "limit": 1, "witness_outcome": "MATCH"}),
        ("/decisions", {"kind": "aml", "limit": 1, "witness_outcome": "CONCLUSIVE_NO"}),
        ("/decisions", {"kind": "aml", "limit": 1, "witness_outcome": "INCONCLUSIVE"}),
        # --- the filter chips (the crimson belief is ONLY reachable under `card`: all 1,500 AML
        #     rows share one `decided_at` newer than every card row, so they sort above them) ---
        ("/decisions", {"kind": "card", "limit": PAGE, "offset": 0}),
        ("/decisions", {"kind": "aml", "limit": PAGE, "offset": 0}),
        # --- Time-travel OPEN (the state that used to push the kill-shot below the fold) ---
        (f"/beliefs/{belief}/performance", None),
        (f"/agents/{agent}/beliefs", {"as_of": iso_past}),
        (f"/agents/{agent}/beliefs", None),
        # --- ARM (the closure that populates the confirm scope). NOT the write: the guard stops
        #     at the armed gate and never clicks Confirm, so POST /invalidate is never issued. ---
        (f"/beliefs/{belief}/lineage", None),
        # --- the evidence surface (for the `is_fraud`-absent-from-innerText check) ---
        (f"/aml/transactions/{txn}/interrogate", None),
        # --- the GEOMETRY subjects. Each makes one invariant OBSERVABLE; without them the geometry
        #     guard renders nothing and passes. See pick_geometry_subjects. ---
        (f"/aml/transactions/{subjects['aml_ring_txn_id']}/interrogate", None),
        (f"/aml/transactions/{subjects['aml_parallel_txn_id']}/interrogate", None),
        (f"/aml/transactions/{subjects['aml_omits_subject_txn_id']}/interrogate", None),
    ]

    captured: dict[str, object] = {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://capture") as client:
        for path, params in requests:
            res = await client.get(path, params=params)  # type: ignore[arg-type]
            if res.status_code != 200:
                raise SystemExit(f"capture FAILED {res.status_code} for {path} {params}\n{res.text[:300]}")
            captured[key(path, params)] = res.json()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"subjects": subjects, "responses": captured}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    await engine.dispose()

    print(f"captured {len(captured)} responses -> {OUT.relative_to(Path.cwd())}")
    for k in captured:
        print(f"  {k}")
    print("\nsubjects (all uuid5-derived, so they survive a reseed):")
    for k, v in subjects.items():
        print(f"  {k:22} {v}")


if __name__ == "__main__":
    asyncio.run(main())
