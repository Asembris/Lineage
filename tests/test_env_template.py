"""THE ENV TEMPLATE MUST DOCUMENT EVERY KEY THAT IS NOT OPTIONAL. Derived from config.py, never restated.

WHY THIS EXISTS. `.env.example` was committed (1dfda44) to close a real clean-clone failure: a judge
who omits `OPENAI_API_KEY` does not get a helpful error about OpenAI — they get a pydantic
`ValidationError` at IMPORT time, from `app.config`, on scripts that never call OpenAI at all
(`verify_corpus`, `verify_regulatory`, `verify_aml_ingest` all import `app.db` -> `get_settings()`).
That is undiagnosable from prose, which is why the template is a committed FILE and not a README
paragraph.

But a template is only worth what it is worth ON THE DAY IT IS READ. Add a required field to
`Settings` tomorrow and the template silently becomes a lie of omission — it still LOOKS complete,
and the next clean clone dies at import with no clue which key it is missing. That is precisely the
rot class `tests/test_restore_instructions.py` exists for: an instruction that was true when written,
that nobody re-checks, that lies at N sites before anyone follows it.

THE RULE: every field on `app.config.Settings` with NO DEFAULT must appear in `.env.example`.

  * NO DEFAULT means absence is fatal — pydantic raises at import. These are the keys a clean clone
    CANNOT start without, so they are exactly the keys the template owes the reader.
  * A field WITH a default (including `None`) is genuinely optional: `aws_region`, `s3_bucket` and
    friends are absent-safe by construction. The template documents them anyway, as a courtesy, and
    this guard deliberately does NOT require that — requiring it would freeze a courtesy into a
    contract and fail the moment someone adds an internal tunable with a default.

=========================== TWO THINGS THIS GUARD DOES NOT DO, ON PURPOSE ===========================

(1) IT DOES NOT CHECK THE REVERSE. `.env.example` documents `NVIDIA_API_KEY` (the grounding eval),
    `LLAMA_CLOUD_API_KEY` (`scripts/parse_regulatory.py`) and `COCKROACH_CLUSTER_ID` (`.mcp.json`).
    None is a `Settings` field — they are read by scripts and tooling, never by pydantic. Asserting
    "every documented key is a Settings field" would false-fail on all three, and the fix would be to
    DELETE true, useful documentation to satisfy a test. A guard that pressures you to remove
    accurate docs is worse than no guard.

(2) IT DOES NOT HARDCODE THE FIELD LIST. A list of required keys copied into this file is itself a
    thing that rots — it would restate the truth instead of deriving it, and then TWO places could
    disagree with `config.py` instead of one. `Settings.model_fields` is read at runtime, so adding
    a required field to `config.py` makes this test fail immediately, with no edit here.

THE ALIAS IS THE THING TO MATCH, NOT THE FIELD NAME. `Settings` declares `database_url` with
`Field(alias="DATABASE_URL")`; the template documents `DATABASE_URL`. Matching field names would
false-fail on every aliased field in the model. `.upper()` is the fallback for a future field
declared without an alias (pydantic-settings would then read the upper-cased name from the env).

Hermetic: reads `Settings.model_fields` (a CLASS attribute — no instantiation, so no ValidationError
and no .env needed) and one text file. No cluster, no network, no OpenAI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

# OFFLINE (class-attribute introspection + one file read), so this runs in `docs-ci.yml` on a
# docs-only push. Offline-ness is ENFORCED, not promised, by tests/test_doc_guard_marker.py, which
# runs the marked set against a dead-host DATABASE_URL.
pytestmark = pytest.mark.doc_guard

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = _ROOT / ".env.example"


def _required_env_names() -> list[str]:
    """Every env var a clean clone CANNOT start without, derived from `Settings` itself.

    `is_required()` is pydantic's own answer to "does absence raise?" — which is the exact property
    that matters here, and it stays correct across `Field(...)`, bare annotations, and `None`
    defaults without this file needing to know the difference.
    """
    return [
        f.alias or name.upper()
        for name, f in Settings.model_fields.items()
        if f.is_required()
    ]


def _documented_keys(text: str) -> set[str]:
    """Keys the template actually DOCUMENTS: `KEY=` at line start, comments ignored.

    Anchored at line start and comment-blind on purpose. A key that appears only inside a prose
    comment is EXPLAINED but not DOCUMENTED — a reader copying the file gets no slot to fill, which
    is the failure this guard is about. (Same mention-vs-use distinction the gloss guard draws.)
    """
    return {
        m.group(1)
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)=", text, re.M)
    }


def test_the_template_exists():
    """Without the file, every other assertion here is vacuously true."""
    assert _TEMPLATE.exists(), (
        ".env.example is gone. It is the only place the OPENAI_API_KEY-must-be-PRESENT trap is "
        "documented where a judge will actually look; README.md links to it by name."
    )


def test_every_required_setting_is_documented_in_the_template():
    """THE GUARD. Add a no-default field to Settings and this fails until the template documents it."""
    required = _required_env_names()
    assert required, (
        "Settings reports NO required fields. Either config.py gave every field a default (in which "
        "case this guard is now vacuous and should be reconsidered, not deleted quietly), or the "
        "introspection broke. Both need a human."
    )

    documented = _documented_keys(_TEMPLATE.read_text(encoding="utf-8"))
    missing = sorted(set(required) - documented)

    assert not missing, (
        "these env vars are REQUIRED by app/config.py (no default -> pydantic raises at IMPORT) but "
        "are not documented in .env.example:\n  "
        + "\n  ".join(missing)
        + "\n\nA clean clone that omits one dies with a ValidationError from a module that may never "
        "use the key (the verify_* scripts import app.db and never call OpenAI). Add each as a "
        "`KEY=` line with a comment saying why it is required."
    )


def test_the_documented_required_keys_carry_a_reason():
    """A bare `KEY=` teaches nothing. Each required key must have explanatory prose ABOVE it.

    ADJACENCY, NOT MENTION — and the first draft of this test got that wrong. It asserted the key's
    NAME appeared in some comment, and it FAILED on a correct template: good templates explain a key
    in the block directly above it without restating its name (`# CockroachDB Cloud connection
    string...` over `DATABASE_URL=`). The only way to pass was to pad the prose with the key name —
    i.e. the test would have distorted an accurate artifact to satisfy itself, which is the exact
    antipattern this module's docstring warns about two paragraphs up. Fixed by measuring the real
    property: is there a contiguous comment block immediately above this key?

    Weaker than the guard above on purpose: it checks that prose EXISTS, never what it says. Grading
    prose is not something a test can do honestly — but "somebody explained this key where the
    reader will see it" is checkable, and it is the difference between a template and a list.
    """
    lines = _TEMPLATE.read_text(encoding="utf-8").splitlines()
    #: Enough characters that a divider like `# ---- AWS ----` cannot pass as an explanation.
    _MIN_PROSE = 40

    unexplained = []
    for key in _required_env_names():
        idx = next((i for i, ln in enumerate(lines) if ln.startswith(f"{key}=")), None)
        if idx is None:
            continue  # absence is the previous test's finding, not this one's
        block = []
        for ln in reversed(lines[:idx]):
            if not ln.lstrip().startswith("#"):
                break  # a blank line or another key ends the block
            block.append(ln.lstrip("# ").strip())
        if len("".join(block)) < _MIN_PROSE:
            unexplained.append(key)

    assert not unexplained, (
        "these REQUIRED keys have no explanatory comment block directly above them in "
        ".env.example:\n  "
        + "\n  ".join(unexplained)
        + "\n\nThe template's whole value is that it explains what prose could not — a reader who "
        "needs the key is looking at the line, not at the README."
    )
