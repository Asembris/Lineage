"""THE SAFETY-PIN for the CI split — the only thing standing between a docs commit and a cluster wipe.

`docs-ci.yml` runs `pytest -m doc_guard` on every docs-only push, offline, so a NOTES/README commit
no longer re-fires the 211-test cluster suite (measured: a docs-only push wiped the cluster in
~7-8 min, NOTES "A FABRICATED DESCRIPTION OF A PRIMARY SOURCE"). That is safe ONLY as long as no
`@doc_guard` test touches the cluster. If one did, it would call `seed.seed()` — which DELETEs every
decision — on every docs commit. This module forbids that.

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
