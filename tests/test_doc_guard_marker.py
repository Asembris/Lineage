"""THE SAFETY-PIN for the CI split — the only thing standing between a docs commit and a cluster wipe.

`docs-ci.yml` runs `pytest -m doc_guard` on every docs-only push, offline, so a NOTES/README commit
no longer re-fires the 211-test cluster suite (measured: a docs-only push wiped the cluster in
~7-8 min, NOTES "A FABRICATED DESCRIPTION OF A PRIMARY SOURCE"). That is safe ONLY as long as no
`@doc_guard` test touches the cluster. If one did, it would call `seed.seed()` — which DELETEs every
decision — on every docs commit. This module forbids that.

The subprocess pin runs green on the Linux runner too, not just locally (CI af3cd46, 215 passed).

============================ WHY THIS IS NOT A grep, AND WHY THAT MATTERS ============================

The tempting pin is "no @doc_guard test's body mentions `engine` or `seed`". That is a PROXY, and it
misses the real risk exactly: a marked test that does not name the cluster itself but CALLS a helper
or uses a FIXTURE that connects. A grep of the function body cannot see through a call. This project
has shipped that class of mistake repeatedly (the 14-line proximity window that passed its own bug;
the composition guard's check C, blind to a render-graph it did not walk).

So the pin does not READ the tests — it RUNS them, against a DATABASE_URL that points at a dead host
(`127.0.0.1:1`, nothing listening, connection refused instantly). A pure guard never connects and
passes. A test that touches the cluster — directly, through a helper, through a fixture, at any
depth — attempts a connection, is refused, and FAILS, naming itself. **Transitivity is caught by
CONSTRUCTION: we observe the connection attempt rather than reasoning about the code that makes it.**
The SQLAlchemy async engine is lazy (`create_async_engine` does not dial), so importing `app.db`
under the dead URL is fine — only an actual `engine.connect()` fails, which is precisely the line we
want to forbid in a marked test.

This is the same discipline as the geometry guard (render, don't grep the CSS) and the composition
guard (walk the type graph, don't match the type name).

============================ WHAT IS DIRECT-ONLY, STATED HONESTLY ============================

The runtime pin is transitive. The one thing it does NOT prove is intent: a marked test that is pure
TODAY but tomorrow grows a cluster call will be caught the moment that call runs (here and in
docs-ci), not at the moment someone writes it. That is a test-time guarantee, not an edit-time one —
and it is the strongest available, because an edit-time guarantee would require the static call-graph
analysis this pin deliberately avoids. The rule for a human is simple and documented on the marker in
pyproject.toml: never mark a test that connects. The pin is what makes the rule non-optional.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: A DATABASE_URL whose host is guaranteed dead: localhost:1, where nothing listens. A connection is
#: refused in milliseconds (no TCP timeout hang, unlike an unreachable remote host).
_DEAD_DB = "postgresql+psycopg://nobody:nobody@127.0.0.1:1/nothing"

#: The load-bearing guards that MUST stay in the marked set. If a rename or a dropped marker removed
#: one, docs-ci would silently stop running it on docs pushes — a doc guard skipped by the exact
#: pushes it protects, which is the ninth-vacuous-check shape. Substrings, matched against the
#: collected node ids, so they survive a file move but not a silent un-marking.
_REQUIRED = (
    "test_citations.py",
    "test_restore_instructions.py",
    "test_frontend_typecheck.py",
    "test_env_template.py",   # .env.example documents every no-default Settings field
    "test_doc_anchors.py",    # no [text](#anchor) in the judge-facing docs is dead
    "test_no_surface_describes_conclusive_no_as_463_searches",  # the gloss guard
    "test_frontend_ci_actually_invokes_the_guard",              # composition meta-guard
    "test_frontend_ci_actually_invokes_the_geometry_guard",     # geometry meta-guard
)


def _run_pytest(*args: str) -> subprocess.CompletedProcess[str]:
    """Run `pytest -m doc_guard` in a child process under the DEAD DATABASE_URL.

    The environment is the REAL one with DATABASE_URL OVERWRITTEN to the dead host — so a marked test
    cannot reach the real cluster even by reading the ambient secret, while PATH / SYSTEMROOT / the
    venv stay intact. `-p no:cacheprovider` keeps the child from fighting the parent's cache; the
    real DATABASE_URL secret is never passed.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = _DEAD_DB
    env["OPENAI_API_KEY"] = "sk-dummy-doc-guard-pin"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "doc_guard", "-p", "no:cacheprovider", *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def test_the_doc_guard_set_contains_the_load_bearing_guards():
    """A silent shrink of the marked set = a doc guard that stops running on docs pushes."""
    collected = _run_pytest("--collect-only", "-q")
    assert collected.returncode == 0, (
        "collecting `-m doc_guard` failed:\n" + collected.stdout[-2000:] + collected.stderr[-1000:]
    )
    out = collected.stdout
    missing = [name for name in _REQUIRED if name not in out]
    assert not missing, (
        "these guards are no longer in the @doc_guard set, so `docs-ci.yml` would stop running them "
        "on a docs-only push (a guard skipped by exactly the push it protects):\n  "
        + "\n  ".join(missing)
        + "\n\nRe-add the marker, or update _REQUIRED with a reason if the guard was intentionally "
        "moved out of docs-ci."
    )


def test_no_doc_guard_test_touches_the_cluster():
    """THE PIN. Run the marked set against a dead-host DB; a cluster touch (direct OR transitive)
    fails here rather than reseeding the cluster on a docs commit."""
    result = _run_pytest("-q")
    assert result.returncode == 0, (
        "A @doc_guard TEST TOUCHED THE CLUSTER.\n\n"
        "It ran against a dead-host DATABASE_URL and failed — meaning it (or a helper or fixture it "
        "uses, at any depth) tried to open a real connection. `docs-ci.yml` runs exactly this set on "
        "EVERY docs-only push, so a cluster-touching test here would call seed.seed() and WIPE every "
        "decision on a NOTES/README commit. Remove the @doc_guard marker from whatever failed below "
        "— an offline guard is the only thing that may carry it.\n\n"
        + result.stdout[-4000:]
        + "\n"
        + result.stderr[-1500:]
    )


# ---- the WORKFLOW meta-guards: read the REAL yaml, like test_composition_guard.py does ----------

_DOCS_CI = _ROOT / ".github" / "workflows" / "docs-ci.yml"
_BACKEND_CI = _ROOT / ".github" / "workflows" / "ci.yml"


def test_docs_ci_runs_only_the_marked_guards_never_the_whole_suite():
    """The split's whole safety rests on docs-ci running `pytest -m doc_guard`, not a bare `pytest`.

    A future edit that drops the `-m doc_guard` filter would silently run all 211 tests in docs-ci —
    and with docs-ci's dead-host DATABASE_URL the 185 cluster tests would fail the job (loud, not a
    wipe), but the DOC guards would stop being the point and the offline contract would be broken. So
    this reads the real yaml and pins the filter. (Reads the file, like the composition/geometry
    meta-guards — a docstring promise is not a guard.)
    """
    assert _DOCS_CI.exists(), "docs-ci.yml is gone — the offline doc-guard job no longer exists."
    ci = _DOCS_CI.read_text(encoding="utf-8")

    assert "pytest -m doc_guard" in ci, (
        "docs-ci.yml no longer runs `pytest -m doc_guard`. A bare `pytest` there runs the whole "
        "211-test suite; the split's offline guarantee is gone."
    )
    # The DATABASE_URL the job actually SETS must be the dead host, not a real secret — an offline
    # job that reaches the real cluster could reseed it on a docs push. Inspected per-line and
    # comment-blind ON PURPOSE: a substring scan of the whole file would trip on a comment that
    # merely NAMES `secrets.DATABASE_URL` to explain why it is absent (it did, on the first draft —
    # the same mention-vs-use bug the gloss guard exists for).
    env_lines = [
        ln for ln in ci.splitlines()
        if "DATABASE_URL:" in ln and not ln.lstrip().startswith("#")
    ]
    assert env_lines, "docs-ci.yml sets no DATABASE_URL — app.config would fail to import."
    for ln in env_lines:
        assert "secrets" not in ln, (
            f"docs-ci.yml wires a real DATABASE_URL secret:\n    {ln.strip()}\n"
            "This job MUST stay offline — a real connection here reseeds the cluster on a docs push."
        )
        assert "127.0.0.1:1" in ln, (
            f"docs-ci.yml's DATABASE_URL is not the dead host:\n    {ln.strip()}\n"
            "The dead host is the backstop that turns a stray cluster touch into a loud failure "
            "instead of a silent wipe."
        )
    # It fires on root docs.
    assert "'*.md'" in ci, "docs-ci.yml no longer triggers on root *.md pushes."


def test_ci_yml_skips_the_cluster_suite_on_root_doc_pushes():
    """The other half of the split: the cluster suite must be OFF for root-doc-only pushes, or the
    docs commit still wipes the cluster (the problem this split exists to fix)."""
    assert _BACKEND_CI.exists()
    ci = _BACKEND_CI.read_text(encoding="utf-8")
    # A cheap structural check: '*.md' must be in the paths-ignore list. (The full semantics —
    # 'skips only when EVERY changed file matches' — are GitHub's; what we pin is that root docs are
    # listed, and that the glob is the ROOT one, not '**/*.md' which would wrongly skip
    # data/corpus/*.md.)
    assert "paths-ignore:" in ci, "ci.yml no longer uses paths-ignore."
    assert "'*.md'" in ci, (
        "ci.yml's paths-ignore no longer lists '*.md', so a root-doc-only push still fires the "
        "211-test cluster suite and WIPES the cluster — the exact regression this split removes."
    )
    assert "'**/*.md'" not in ci, (
        "ci.yml ignores '**/*.md' (recursive), which would wrongly skip the suite on a "
        "data/corpus/*.md change — ingested content that test_regulatory_corpus pins. Use the ROOT "
        "glob '*.md'."
    )
