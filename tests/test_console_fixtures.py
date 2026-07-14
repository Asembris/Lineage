"""THE PIN — the browser guard's mock, held to the real API by the workflow that can see it change.

===================== WHY THIS FILE IS THE OTHER HALF OF A GUARD =====================

`frontend/tests-e2e/geometry.spec.ts` renders the real console in a real browser and asserts the one
invariant that only rendering can prove: Confirm must NOT land on the arm button's footprint (two
clicks of muscle memory in one screen position would otherwise be an irreversible fleet-wide write).

To reach the armed state it needs data, and it gets that data from a MOCK. That is the obvious place
for this to rot into the sixth proxy: a mock that drifts from the real API would let the geometry
guard stay green while the real console breaks.

**IT CANNOT DRIFT SILENTLY, AND THIS FILE IS WHY.** The fixtures are captured verbatim from the live
cluster (`scripts/capture_console_fixtures.py`), and this test REPLAYS every captured request
against the live cluster and asserts the recorded body still has the shape the live one does.

The split is the same one the composition guard arrived at, mirrored:

    frontend-ci.yml         RUNS the geometry guard against the fixtures  (it has Node)
    tests/test_console_fixtures.py  PINS the fixtures to the real API      (it has DATABASE_URL)

And the halves cover each other's blind spots for a structural reason, not a hopeful one:

  * A FRONTEND change (the CSS flip this all exists to catch) fires `frontend-ci`, which runs the
    geometry guard.
  * A BACKEND change — the only thing that can make the mock a lie — fires `ci.yml`, because
    `paths-ignore` skips ONLY frontend-only pushes. So every schema change, route change or
    serializer change that could break the mock runs this pin.

Neither workflow is the one that misses.

============================ WHAT THIS PINS, AND WHAT IT DOES NOT ============================

It pins SHAPE: the recursive key structure of every captured response against a freshly-fetched one,
plus validation against the route's real Pydantic `response_model` (resolved from the live FastAPI
app, never a hand-copied list of routes — a hand-copied list is a second source of truth waiting to
disagree).

It does NOT pin SEMANTICS. A backend field that keeps its name and type while changing its meaning
would pass here. That is a real limit and it is written down rather than left for someone to
discover. It cannot affect the geometry invariant, which is what the browser guard measures.

CLUSTER: read-only. Never calls seed(). Issues the same GETs the console does; no writes, and the
governed write (POST /invalidate) is not among them — the browser guard stops at the armed gate.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest

from app.main import app

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = _ROOT / "frontend" / "tests-e2e" / "fixtures" / "console.json"
_SPEC = _ROOT / "frontend" / "tests-e2e" / "geometry.spec.ts"
_MOCK = _ROOT / "frontend" / "tests-e2e" / "mock.ts"


def _load() -> dict:
    assert _FIXTURES.exists(), (
        f"the console fixtures are gone: {_FIXTURES}\n"
        "The browser geometry guard renders the console from these. Re-capture with "
        "`python -m scripts.capture_console_fixtures`."
    )
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


def _split(key: str) -> tuple[str, list[tuple[str, str]]]:
    """`GET /decisions?kind=card&limit=200` -> ('/decisions', [('kind','card'), ('limit','200')])."""
    assert key.startswith("GET "), f"unexpected fixture key (only GETs are captured): {key}"
    target = key[4:]
    path, _, qs = target.partition("?")
    return path, parse_qsl(qs)


def _shape(value: object) -> object:
    """The recursive FIELD STRUCTURE of a response — field names and types, never values.

    Values must not be compared: ids, timestamps and counts legitimately differ between the capture
    and any later cluster state. What must NOT differ is the shape the console renders against.

    A list collapses to the shape of its FIRST element (the feed is homogeneous). `int`/`float`
    collapse to `number`: JSON has one numeric type, and an amount that happens to serialize as
    `180` rather than `180.0` is not drift.
    """
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return ["<empty>"] if not value else [_shape(value[0])]
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _route_model(path: str):
    """The route's REAL Pydantic response_model, resolved from the live FastAPI app.

    Read from `app.routes` rather than a hand-written path->model table: a hand-written table is a
    second source of truth, and a second source of truth is a thing that can disagree with the first
    while everything stays green.
    """
    for route in app.routes:
        model = getattr(route, "response_model", None)
        if model is None:
            continue
        # `path_format` is the templated path ('/agents/{agent_id}/beliefs'); match it structurally.
        template = getattr(route, "path_format", getattr(route, "path", ""))
        t_parts, p_parts = template.strip("/").split("/"), path.strip("/").split("/")
        if len(t_parts) != len(p_parts):
            continue
        if all(t.startswith("{") or t == p for t, p in zip(t_parts, p_parts)):
            return model
    return None


def test_every_captured_response_is_exactly_what_its_response_model_serializes():
    """THE LOAD-BEARING ONE. Each captured body must round-trip through the route's REAL model
    unchanged in shape — so the mock cannot describe a console the backend no longer serves.

    ===== WHY THIS IS A ROUND-TRIP AND NOT A COMPARISON AGAINST THE LIVE CLUSTER =====

    The first draft of this test DID replay each request against the live cluster and compare
    shapes. It passed standalone and FAILED in the full suite — because the suite calls `seed()`,
    which DELETEs every decision and every `belief_performance` row. By the time this test ran,
    `/decisions` returned `[]` and `/beliefs/{id}/performance` returned no windows.

    The tempting fix was to treat an empty list as a wildcard. **That would have been the sixth
    proxy, and a perfect one.** In backend CI the suite ALWAYS reseeds, so the rows would ALWAYS
    have been empty by the time this ran — and the row shape, the only shape the console actually
    renders, would have been checked NEVER. Green for its entire life, checking nothing. That is the
    exact disease of `tsc --noEmit` (zero files, exit 0, cited by nine gates), and it was avoided
    only because the FULL suite was run instead of this file alone.

    So the pin does not depend on cluster CONTENTS at all. It round-trips the captured body through
    the route's real Pydantic `response_model` — which is the thing that actually decides the wire
    shape — and that catches every drift that matters:

      * a field ADDED to the model     -> round-trip emits it, the fixture lacks it   -> FAILS
      * a field REMOVED from the model -> round-trip drops it, the fixture has it     -> FAILS
      * a field RENAMED or RETYPED     -> ValidationError, or the shapes differ       -> FAILS

    Note the second one: plain `model_validate` would NOT catch a removal (Pydantic ignores extra
    keys by default). The round-trip is what closes that, and it is deterministic — no cluster, no
    ordering, no flake.

    `test_every_captured_route_still_serves_200` covers what this cannot: that the route still
    EXISTS and still answers. Between them, a model change and a route change both fail here.
    """
    fx = _load()
    drifted: list[str] = []

    for key, captured in fx["responses"].items():
        path, _ = _split(key)
        model = _route_model(path)
        assert model is not None, (
            f"no route with a response_model matches {path!r}.\n"
            "The console calls an endpoint this app no longer serves — the mock is now fiction. "
            "Re-capture, or fix the route."
        )

        served = model.model_validate(captured).model_dump(mode="json")
        want, got = _shape(captured), _shape(served)
        if want != got:
            drifted.append(
                f"{key}  ({model.__name__})\n"
                f"    fixture: {json.dumps(want, sort_keys=True)[:280]}\n"
                f"    model:   {json.dumps(got, sort_keys=True)[:280]}"
            )

    assert not drifted, (
        "THE CONSOLE FIXTURES NO LONGER MATCH THE API'S RESPONSE MODELS.\n\n"
        "frontend/tests-e2e/ renders the real console against these captured responses to prove\n"
        "Confirm does not land on the arm button's footprint. If the API moved and they did not,\n"
        "that guard is now measuring a console that does not exist — it would stay GREEN while the\n"
        "real one broke. Re-capture:  python -m scripts.capture_console_fixtures\n\n"
        + "\n".join(drifted)
    )
    assert len(fx["responses"]) >= 16, "the fixture set shrank — did a capture silently drop routes?"


def test_every_captured_route_still_serves_200():
    """The routes the console calls must still EXIST and still answer, on the real app.

    Deliberately cluster-STATE-independent: it asserts a 200, not a body. An empty cluster still
    serves `{"decisions": [], "total": 0}` with a 200, so this passes wherever it lands in the
    suite's reseed order — while a deleted route, a renamed path or a 500 fails it. Shape is the
    round-trip test's job; existence is this one's.
    """
    fx = _load()

    async def run() -> list[str]:
        broken: list[str] = []
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pin") as client:
            for key in fx["responses"]:
                path, params = _split(key)

                # `as_of` is the ONE normalized param: TimeTravel computes `Date.now() - 20s` at
                # click time, so no captured value could ever match. Replaying the ORIGINAL captured
                # instant would be worse than useless — days later it falls outside the GC window and
                # the cluster returns 400, rotting the fixture into a false failure. A FRESH past
                # instant is substituted, exactly as the browser computes one.
                fresh = (datetime.now(UTC) - timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
                params = [(k, fresh if k == "as_of" else v) for k, v in params]

                url = f"{path}?{urlencode(params)}" if params else path
                res = await client.get(url)
                if res.status_code != 200:
                    broken.append(f"{key}\n    -> {res.status_code}: {res.text[:160]}")
        return broken

    broken = asyncio.run(run())
    assert not broken, (
        "THE CONSOLE CALLS A ROUTE THE BACKEND NO LONGER SERVES.\n\n"
        "The browser guard's mock replays these requests; if the real app cannot answer them, the\n"
        "mock is fiction and the console is broken for real users.\n\n" + "\n".join(broken)
    )


def test_the_fixtures_cover_the_states_the_guard_actually_drives():
    """A fixture set that cannot reach the armed gate is a fixture set that guards nothing.

    The browser guard's liveness property (it must DRIVE to the armed state before it can measure
    anything) is what stops it being vacuous — the disease `tsc --noEmit` had, typechecking zero
    files and exiting 0 for nine gates. That property depends on these four subjects existing, so
    they are asserted here too, in the suite that runs on backend changes.
    """
    fx = _load()
    s = fx["subjects"]
    r = fx["responses"]

    belief = next(b for b in r["GET /beliefs"]["beliefs"] if b["id"] == s["belief_id"])
    assert belief["status"] == "active", (
        "the guard's subject belief is no longer active — an invalidated belief renders a NOTE, "
        "not the arm button, and the geometry under test would never appear."
    )

    perf = r[f"GET /beliefs/{s['belief_id']}/performance"]
    assert len(perf["windows"]) >= 2, (
        "the subject belief has no measured curve — Time-travel would have nothing to draw, and the "
        "kill-shot's geometry only becomes visible once Time-travel is OPEN."
    )

    feed = r["GET /decisions?kind=card&limit=200&offset=0"]["decisions"]
    subject = next((d for d in feed if d["id"] == s["decision_id"]), None)
    assert subject is not None, (
        "the guard's subject decision is not on the card feed's first page — the guard clicks it "
        "without paging, so it must be reachable there."
    )
    assert subject["driving_belief_id"] == s["belief_id"]

    aml = r["GET /decisions?kind=aml&limit=200&offset=0"]["decisions"]
    assert any(d["aml_transaction_id"] == s["aml_transaction_id"] for d in aml), (
        "the guard's interrogation subject is not on the AML feed's first page."
    )

    # THE ORACLE BOUNDARY, ON THE WIRE. The browser guard asserts the label is absent from the
    # rendered PIXELS; this asserts it was never even served. Both, because they fail differently:
    # a component could render a label the API never sent, and an API could send one no component
    # renders today (and something would render it tomorrow). Every interrogation is swept, not just
    # the original one — the geometry subjects are three more served payloads.
    for txn in (
        s["aml_transaction_id"],
        s["aml_ring_txn_id"],
        s["aml_parallel_txn_id"],
        s["aml_omits_subject_txn_id"],
    ):
        interrogation = json.dumps(r[f"GET /aml/transactions/{txn}/interrogate"])
        for label in ("is_fraud", "is_laundering"):
            assert label not in interrogation, (
                f"/interrogate now serves `{label}` for {txn}. The evidence layer must be renderable "
                "WITHOUT the ground truth — that is the whole meaning of the precision exhibit."
            )


def _matches(response: dict) -> list[dict]:
    return [w for w in response["witnesses"] if w["outcome"] == "MATCH" and w["kind"] != "NONE"]


def test_the_geometry_fixtures_still_exhibit_the_invariants_they_were_chosen_for():
    """THE GEOMETRY GUARD'S SUBSTRATE MUST NOT SILENTLY GO BLANK.

    ============ THE FIXTURE WAS NEVER CHOSEN. IT WAS A SIDE EFFECT. ============

    Before Rung 3, `capture_console_fixtures.py` picked its AML subject with
    `ORDER BY decided_at DESC, id DESC LIMIT 1`. All 1,500 AML rows share ONE `decided_at` (the
    base-rate-mirage guard put it there on purpose), so that ORDER BY collapses to "whatever has the
    max id" — and the row it landed on witnesses NOTHING. All four of its witnesses are NONE.

    A geometry guard written against that fixture RENDERS NO GEOMETRY AND PASSES. Green forever,
    measuring nothing — the `tsc --noEmit` disease, arriving through the back door of a fixture
    rather than a command.

    So this asserts the three geometry subjects still EXHIBIT the properties they were picked for. A
    re-capture that quietly lands on witness-less rows fails HERE, in the backend suite, instead of
    leaving a browser guard that renders an empty page and reports success.

    THIS IS NOT A DUPLICATE OF THE BROWSER ASSERTION. The browser compares the RENDER to the WIRE;
    this asserts the WIRE still has something worth comparing. A guard and its substrate fail
    differently, and only one of them is checked by the other.
    """
    fx = _load()
    s, r = fx["subjects"], fx["responses"]

    aml_feed = {
        d["aml_transaction_id"] for d in r["GET /decisions?kind=aml&limit=200&offset=0"]["decisions"]
    }

    for key in ("aml_ring_txn_id", "aml_parallel_txn_id", "aml_omits_subject_txn_id"):
        txn = s[key]
        assert txn in aml_feed, (
            f"{key} ({txn}) is not on the AML feed's FIRST PAGE. The geometry guard clicks through "
            "the feed and cannot page, so it could never reach this subject."
        )
        assert _matches(r[f"GET /aml/transactions/{txn}/interrogate"]), (
            f"{key} ({txn}) now witnesses NO structure, so the geometry guard would render an empty "
            "page and pass. Re-capture with `python -m scripts.capture_console_fixtures`, which "
            "picks these subjects on purpose."
        )

    # THE RING: a CYCLE whose drawing is the WHOLE cycle, and which cites its own subject.
    ring = _matches(r[f"GET /aml/transactions/{s['aml_ring_txn_id']}/interrogate"])
    cycle = next((w for w in ring if w["kind"] == "RING"), None)
    assert cycle is not None, "the ring subject no longer has a RING witness."
    assert s["aml_ring_txn_id"] in cycle["transaction_ids"], (
        "the ring witness no longer cites its own subject, so the guard's subject-marker assertion "
        "would be asserting 0 where it means to assert 1."
    )

    # THE MULTIGRAPH: two DISTINCT transactions on ONE account pair. Without this the edge-count
    # assertion cannot fail — a layout keying edges by (from,to) would pass it.
    par = r[f"GET /aml/transactions/{s['aml_parallel_txn_id']}/interrogate"]
    txns = par["transactions"]
    assert any(
        Counter(
            (txns[i]["from_account_id"], txns[i]["to_account_id"]) for i in w["transaction_ids"]
        ).most_common(1)[0][1]
        > 1
        for w in _matches(par)
    ), (
        "no witness of the `parallel` subject cites two transactions on one account pair any more. "
        "The edge-count assertion is now UNFALSIFIABLE: a layout that merges parallel edges would "
        "pass it. Re-pick a subject that exhibits the multigraph."
    )

    # THE OMITTED SUBJECT: 75 of 107 GATHER-SCATTER witnesses do not cite the subject at all.
    # Without this, "mark the subject only when cited" cannot fail either.
    omits = r[f"GET /aml/transactions/{s['aml_omits_subject_txn_id']}/interrogate"]
    assert any(
        s["aml_omits_subject_txn_id"] not in w["transaction_ids"] for w in _matches(omits)
    ), (
        "every witness of the `omits` subject now cites the subject. The subject-marker assertion "
        "can no longer catch a renderer that marks an edge the witness never cited."
    )


def test_frontend_ci_actually_invokes_the_geometry_guard():
    """THE META-GUARD. A guard nobody runs is a file.

    Same reasoning as tests/test_composition_guard.py, and it works for the same structural reason:
    workflow files are NOT in ci.yml's `paths-ignore`, so a push that deletes the step from
    frontend-ci.yml DOES run this suite — and fails here.

    pytest cannot run the geometry guard itself (ci.yml has no Node, and it does not even fire on
    frontend-only pushes — the changes that can violate the invariant). So it guards that CI does.
    """
    ci = (_ROOT / ".github" / "workflows" / "frontend-ci.yml").read_text(encoding="utf-8")
    assert "guard:geometry" in ci, (
        "frontend-ci.yml no longer runs the geometry guard (`npm run guard:geometry`).\n"
        "That guard is the ONLY thing standing between this console and a `.kill__actions`\n"
        "flex-direction flip that would put `Confirm invalidation` on the arm button's footprint —\n"
        "making two clicks of muscle memory in one screen position an irreversible fleet-wide write.\n"
        "It is only provable by rendering, and pytest cannot render. If you removed the step, put it back."
    )
    assert "playwright install" in ci, (
        "frontend-ci.yml no longer installs a browser. `playwright test` without one does not fail "
        "loudly — it fails to run, and a guard that cannot run is a guard that cannot fail."
    )

    scripts = json.loads((_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert scripts["scripts"].get("guard:geometry") == "playwright test"
    assert "@playwright/test" in scripts["devDependencies"], (
        "@playwright/test is not a declared devDependency.\n"
        "Rung 2 installed playwright ad-hoc (it sat in node_modules, EXTRANEOUS, absent from the "
        "lockfile) — so `npm ci` in CI would not have had it at all. An undeclared dependency is a "
        "dependency that exists only on the machine that installed it."
    )


def test_the_geometry_guard_asserts_the_property_not_the_pixels():
    """A guard that breaks on innocent change teaches people to weaken it.

    The measured rects (arm y=749..786, confirm y=694..729 at 1280x800) are EVIDENCE for the
    invariant, not the invariant. Pinning them would fail on any legitimate layout change — a longer
    rule text, a font metric, a padding tweak — and the first person to hit that would loosen the
    assertion rather than investigate it. What must be asserted is the property the numbers were
    evidence FOR: Confirm ∩ arm = empty, Cancel ∩ arm = non-empty.
    """
    spec = _SPEC.read_text(encoding="utf-8")
    assert "intersects" in spec, "the geometry guard must assert rect INTERSECTION, not coordinates"

    # No absolute pixel coordinate from the measurement may appear in an assertion.
    for pixel in ("749", "786", "694", "729", "737", "772"):
        for line in spec.splitlines():
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue  # a comment may RECORD the measurement; an assertion may not depend on it
            assert pixel not in line, (
                f"the geometry guard hardcodes the measured pixel {pixel}:\n    {stripped}\n"
                "Assert the PROPERTY (disjoint / covers), never the coordinates."
            )

    mock = _MOCK.read_text(encoding="utf-8")
    assert "misses" in mock, (
        "the mock no longer records unmocked requests. An unmocked request must FAIL the guard — "
        "otherwise the fixture set is assumed to cover the console's call surface rather than "
        "asserted to, and the guard can silently stop covering a state."
    )


@pytest.mark.parametrize("path", [_FIXTURES, _SPEC, _MOCK, _ROOT / "frontend" / "playwright.config.ts"])
def test_the_guard_exists(path: Path):
    assert path.exists(), f"the geometry guard lost a load-bearing file: {path}"
