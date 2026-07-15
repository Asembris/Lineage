"""THE META-GUARD — pytest cannot RUN the composition guard, so it guards that CI does.

===================== THE FINDING THAT PUT THIS FILE HERE =====================

The composition guard (`frontend/scripts/composition-guard.mjs`) is the most important check in the
AML console: it is what stops a React component from receiving both an `AmlInterrogationResponse`
and a `Decision` — the exam and the answer key in one component tree, which would reintroduce the
oracle-boundary collapse IN PIXELS, permanently, with nothing failing.

The obvious home for it was this suite. **It cannot live here, and finding out why is the point.**

  1. `ci.yml` has NO Node step. It is setup-python -> pip install -> pytest. There is no
     `node_modules`, so a pytest test that shelled out to `tsc` would ERROR in CI, not detect
     anything.

  2. FAR WORSE: `ci.yml` declares `paths-ignore: ['frontend/**']`. A push that changes only
     frontend files **does not run this suite at all.** So a pytest composition guard would have
     been SKIPPED BY EXACTLY THE PUSHES THAT CAN VIOLATE IT. Someone adds the offending component
     in a frontend-only push; the guard never executes; the build is green.

**A check that cannot fail on the change it protects against is not a check.** That is the
`tsc --noEmit` disease — cited as evidence by nine gates, green for its entire life, checking zero
files — and this is the same disease one layer up. The guard for the oracle boundary was one
workflow-file read away from being theatre.

============================== SO THE SPLIT IS: ==============================

    frontend-ci.yml  RUNS the guard      (it triggers on frontend/**, and it has Node)
    this file        GUARDS THAT IT RUNS (workflow files are NOT in ci.yml's paths-ignore,
                                          so deleting the step fails the backend suite)

Each covers the other's blind spot. Neither alone is sufficient, and that asymmetry is the whole
design.

MADE TO TRIP, not assumed: deleting the CI step fails `test_frontend_ci_actually_invokes_the_guard`;
deleting the script fails `test_the_guard_script_exists_and_is_the_real_thing`; deleting a fixture
fails `test_the_guard_ships_its_own_demonstration`.

CI-SAFE: pure file reads. No cluster, no npm, no node, no network. Never calls run_seed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# OFFLINE (pure file reads — this whole module verifies the composition guard's integrity and that
# frontend-ci.yml still invokes it; no cluster). The geometry/composition META-guards MUST keep
# running under the CI split, so they run in `docs-ci.yml` too. Offline-ness enforced by
# tests/test_doc_guard_marker.py.
pytestmark = pytest.mark.doc_guard

_ROOT = Path(__file__).resolve().parents[1]
_FE = _ROOT / "frontend"
_GUARD = _FE / "scripts" / "composition-guard.mjs"
_CI = _ROOT / ".github" / "workflows" / "frontend-ci.yml"
_BACKEND_CI = _ROOT / ".github" / "workflows" / "ci.yml"

#: The npm script frontend CI must invoke.
GUARD_SCRIPT = "guard:composition"

#: Every fixture the guard re-analyzes on each run. The demonstration ships INSIDE the guard: a
#: gutted analyzer stops finding these and fails its own self-test. Proving a guard trips once, in
#: a session, proves it about a build nobody will run again.
FIXTURES = (
    "legitimate.tsx",
    "violation-direct.tsx",
    "violation-indirect.tsx",
    "violation-channel.tsx",
    "violation-adjacency.tsx",
)


def test_the_backend_suite_cannot_see_frontend_only_pushes():
    """THE PREMISE, PINNED. This is why the guard does not live in pytest.

    If a future session removes `paths-ignore` from ci.yml, the backend suite WOULD run on
    frontend-only pushes, and the reasoning above changes. That must be a decision, not a drift —
    so the premise is asserted rather than described. (Pinning the CAUSE, not the symptom: exactly
    the correction tests/test_frontend_typecheck.py had to make for `tsc --noEmit`.)
    """
    ci = _BACKEND_CI.read_text(encoding="utf-8")
    assert "paths-ignore" in ci and "frontend/**" in ci, (
        "ci.yml no longer skips frontend-only pushes.\n"
        "The composition guard was put in frontend-ci BECAUSE the backend suite could not see "
        "those pushes. If that changed, re-derive where the guard belongs — do not delete this "
        "test because it 'looks stale'."
    )
    # And it still has no Node — the other half of why a pytest guard could not even run.
    assert "setup-node" not in ci, (
        "ci.yml now sets up Node. A pytest-hosted guard becomes RUNNABLE — but it would still be "
        "skipped on frontend-only pushes, so it is still the wrong home. Re-read the docstring."
    )


def test_the_guard_script_exists_and_is_the_real_thing():
    """Not a stub. It must be a TYPE walk, not a text scan — the distinction is the guard."""
    assert _GUARD.exists(), f"the composition guard is gone: {_GUARD}"
    src = _GUARD.read_text(encoding="utf-8")

    # It uses the real TypeScript compiler API. A regex over .tsx would be a PROXY — it cannot see
    # `import type { Decision as Answer }`, and it cannot see a props type that merely CONTAINS a
    # Decision (an `Investigation`). A proxy guard passes while the thing it protects is broken.
    assert 'from "typescript"' in src, "the guard must use the TypeScript compiler API"
    assert "getTypeChecker" in src, "the guard must resolve TYPES, not match text"

    # All three checks. Props are not the only channel (a zero-prop component can just fetch the
    # label), and composition is not the only hazard (two legal siblings still co-locate the
    # answer key with the witness).
    for check in ("A/composition", "B/channel", "C/adjacency"):
        assert check in src, f"the guard no longer performs check {check}"


def test_the_guard_ships_its_own_demonstration():
    """A guard that cannot be SHOWN to fail is theatre — so the showing is part of the guard.

    The fixtures are re-analyzed on every run and the guard asserts it still finds the known
    violations at the known lines. Delete one and the self-test's expectations no longer match.

    `violation-adjacency.tsx` is here for a reason worth recording: the guard's first draft had
    checks A and B fixtured and check C NOT fixtured — and check C did not work. It coloured
    components by their PROPS, and the evidence surface takes `{ txnId: UUID }` (colourless, by
    design — the join between the layers is a bare id), so mounting it beside the decision feed
    produced nothing at all. Green, and broken. The fixture is what keeps the fix fixed.
    """
    for f in FIXTURES:
        assert (_FE / "scripts" / "fixtures" / f).exists(), f"the guard's fixture {f} is missing"

    src = _GUARD.read_text(encoding="utf-8")
    assert "selfTest" in src, "the guard must re-run its own demonstration on every invocation"
    for f in FIXTURES:
        assert f in src, f"{f} is no longer analyzed by the self-test"


def test_frontend_ci_actually_invokes_the_guard():
    """THE LOAD-BEARING ONE. A guard nobody runs is a file.

    This is the only thing standing between the project and a silently-deleted CI step. It works
    because workflow files are NOT in ci.yml's paths-ignore: a push that removes the step from
    frontend-ci.yml therefore DOES run this suite, and fails here.
    """
    assert _CI.exists()
    ci = _CI.read_text(encoding="utf-8")
    assert GUARD_SCRIPT in ci, (
        f"frontend-ci.yml no longer runs the composition guard (`npm run {GUARD_SCRIPT}`).\n"
        "That guard is what keeps a React component from holding both an interrogation and a "
        "decision — the answer key beside the exam, permanently, in pixels. It is not optional, "
        "and pytest CANNOT run it (see this module's docstring). If you removed the step, put it "
        "back."
    )

    scripts = json.loads((_FE / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert scripts.get(GUARD_SCRIPT) == "node scripts/composition-guard.mjs", (
        f"package.json must expose `{GUARD_SCRIPT}` — one named command, so nobody has to remember "
        f"which invocation is the real one. (The lesson of `tsc -b` vs `tsc --noEmit`.)"
    )


def test_the_evidence_surface_never_names_the_label():
    """Belt and braces with the type walk: the evidence modules must not even TYPE `is_fraud`.

    The type walk above is the real guard — this is the cheap, blunt second opinion, and it is
    deliberately NOT the primary check. On its own it would be a proxy (it cannot see `d.verdict`,
    an alias, or an `Investigation` wrapping a Decision), and a proxy guard is how you get a green
    build over a broken invariant. But a *redundant* scan costs nothing and catches the crudest
    mistake — a raw string, a comment promising the label, a stray field — in the one place where
    the label must never appear at all.
    """
    evidence = [
        _FE / "src" / "components" / "AmlConsole.tsx",
        _FE / "src" / "hooks" / "useInterrogation.ts",
        _FE / "src" / "lib" / "basis.ts",
    ]
    violations = []
    for f in evidence:
        assert f.exists(), f"declared an evidence module that does not exist: {f}"
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            # A comment may DISCUSS the boundary (the modules explain why they refuse the label —
            # banning the words would force them to stop explaining themselves, the same reasoning
            # test_oracle_boundary.py applies to docstrings). Code may not.
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue
            if "is_fraud" in line or "is_laundering" in line:
                rel = f.relative_to(_ROOT).as_posix()
                violations.append(f"{rel}:{i}  {stripped[:90]}")

    assert not violations, (
        "THE EVIDENCE SURFACE NAMES THE LABEL. It must be renderable from /interrogate ALONE:\n  "
        + "\n  ".join(violations)
    )
