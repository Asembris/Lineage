"""THE RESTORE-INSTRUCTION SWEEP, AS AN ASSERTION. It used to be a grep run by hand, by eye.

Restore instructions in this repo have lied EIGHT times, and the count is the argument:

  1-3. Prose a human wrote (README setup, DEMO pre-flight, DEMO reset note).
  4-5. Values a COMPONENT RENDERS — the honesty ledger's two empty states, each naming a single
       command. Prose review could never have found them, and did not.
  6.   `DecisionFeed`'s empty state: "rerun the backfill", singular and unnamed.
  7-8. `ConsistencyDemo`'s confirmation gate — BOTH branches — telling the operator who is about to
       TRUNCATE AND RESEED the cluster that the fleet reads empty "until re-backfilled
       (python -m seed.backfill_decisions)". One command, at the moment of maximum consequence, for
       a demo that destroys the AML decisions too. Following it leaves the grounding seam dead.

Every one of these was found by a human sweep, AFTER the previous fix was declared complete. The
lesson each time was the same and it was never structural: "grep for the SHAPE, not the sentence."
So here is the shape, as a test.

THE RULE: in a file that INSTRUCTS (a doc a reader follows, or a component a user reads), the two
backfills are ONE PROCEDURE and may not be separated. Naming `backfill_decisions` without
`backfill_aml_decisions` nearby is the destructive half-restore — it reseeds, DELETEs every row of
`decisions`, and silently leaves the seam empty.

NOT swept: NOTES.md (an append-only engineering LOG — its historical entries quote the old, wrong
procedures on purpose, and rewriting history to satisfy a grep would be the actual dishonesty), and
Python source (module docstrings that cross-reference a backfill by filename are references, not
instructions to a reader rebuilding the world).
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# Files that INSTRUCT: a reader follows them, or a user reads them on screen.
_DOCS = ["README.md", "DEMO.md"]
_COMPONENT_GLOB = "frontend/src/**/*.tsx"

_CARD = "backfill_decisions"
_AML = "backfill_aml_decisions"
# How close the counterpart must be. Generous: the point is that the two commands travel together,
# not that they sit on adjacent lines.
_WINDOW = 14


def _instruction_files() -> list[Path]:
    files = [_ROOT / d for d in _DOCS]
    files += sorted(_ROOT.glob(_COMPONENT_GLOB))
    return [f for f in files if f.exists()]


def _counterpart_present(line: str) -> bool:
    """The card backfill's counterpart: the AML backfill named literally, OR the one shared
    definition imported. A component that renders <RestoreCommands /> IS naming both commands —
    punishing it for not repeating the literal string would punish the exact fix this guard
    demands, and push the next author back into writing the procedure out by hand."""
    return _AML in line or "RestoreCommands" in line or "RestoreHint" in line


def _lone_card_mentions_in_prose(path: Path) -> list[tuple[int, str]]:
    """Markdown: a line naming the card backfill with NO counterpart within _WINDOW lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, line in enumerate(lines):
        # A bare `backfill_decisions` — NOT the tail of `backfill_aml_decisions`.
        if not re.search(rf"(?<!aml_){_CARD}", line):
            continue
        lo, hi = max(0, i - _WINDOW), min(len(lines), i + _WINDOW + 1)
        if not any(_counterpart_present(ln) for ln in lines[lo:hi]):
            offenders.append((i + 1, line.strip()))
    return offenders


def _lone_card_mentions_in_jsx(path: Path) -> list[tuple[int, str]]:
    """Components: the counterpart must be in the SAME RENDERED PARAGRAPH, not merely nearby.

    A LINE-PROXIMITY RULE IS NOT ENOUGH HERE, AND THAT WAS PROVEN BY BREAKING IT. `ConsistencyDemo`
    renders the destructive gate TWICE (a strong branch and an eventual branch). When the eighth-site
    bug was deliberately reintroduced into ONE branch, a ±14-line window found the OTHER branch's
    correct `<RestoreCommands />` and passed the file — the guard would have shipped blind to the
    exact bug it was written for. Proximity is a proxy; the real invariant is that the two commands
    travel IN THE SAME BREATH, i.e. the same <p> a user actually reads.
    """
    text = path.read_text(encoding="utf-8")
    offenders = []
    for block in re.finditer(r"<p\b[^>]*>.*?</p>", text, re.DOTALL):
        body = block.group(0)
        if not re.search(rf"(?<!aml_){_CARD}", body):
            continue
        if not _counterpart_present(body):
            line_no = text[: block.start()].count("\n") + 1
            offenders.append((line_no, " ".join(body.split())[:120] + " …"))
    return offenders


@pytest.mark.parametrize("path", _instruction_files(), ids=lambda p: str(p.relative_to(_ROOT)))
def test_the_two_backfills_are_never_separated(path: Path) -> None:
    """The card backfill may never be named alone in a file a reader or a user follows.

    This is the guard the eight lies earned. It fails on the SHAPE — a lone `backfill_decisions` —
    not on any particular wrong sentence, which is why it can catch a NINTH site that nobody has
    thought of yet, in a file that does not exist yet.
    """
    is_jsx = path.suffix == ".tsx"
    offenders = _lone_card_mentions_in_jsx(path) if is_jsx else _lone_card_mentions_in_prose(path)
    scope = "the same rendered <p>" if is_jsx else f"{_WINDOW} lines"
    assert not offenders, (
        f"{path.relative_to(_ROOT)} names the CARD backfill without its counterpart in {scope}. "
        f"That is the destructive half-restore: it reseeds, DELETEs every row of "
        f"`decisions`, and silently leaves the grounding seam empty.\n"
        + "\n".join(f"  line {n}: {t}" for n, t in offenders)
        + "\n\nIn a component, import RestoreCommands/RestoreHint from components/RestoreHint.tsx "
        "rather than writing the procedure again."
    )


def test_the_frontend_has_exactly_one_definition_of_the_procedure() -> None:
    """Only RestoreHint.tsx may spell the commands out; everyone else imports it.

    Sites 4-8 were all components. A shared definition is only a defence if it is the ONLY
    definition — otherwise the next component writes its own, and the sweep starts over.
    """
    home = _ROOT / "frontend/src/components/RestoreHint.tsx"
    assert home.exists(), "the procedure's one definition is missing"

    writers = []
    for tsx in sorted(_ROOT.glob(_COMPONENT_GLOB)):
        if tsx == home:
            continue
        text = tsx.read_text(encoding="utf-8")
        # A component RE-DEFINES the procedure if it renders BOTH commands as its own markup. Naming
        # the card backfill alone in a warning ("`backfill_decisions` alone is NOT a restore") is
        # legitimate prose — that hazard is already covered by the pairing test above.
        if re.search(rf"<code>[^<]*{_AML}", text):
            writers.append(tsx.relative_to(_ROOT))

    assert not writers, (
        "these components render the restore command themselves instead of importing the one "
        f"definition from components/RestoreHint.tsx: {[str(w) for w in writers]}"
    )
