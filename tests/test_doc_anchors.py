"""EVERY INTRA-DOCUMENT ANCHOR MUST RESOLVE. A section rename must not silently orphan a link.

WHY THIS EXISTS. Commit b414a21 restructured the README's `### Backend` heading into numbered steps.
That DELETED the `#backend` anchor — and the honesty ledger's `decisions` / `belief_performance` row
still linked to it, so the one link a judge follows to find the restore procedure went nowhere. It
shipped. It was caught in the NEXT session by a manual sweep, and repaired in b7c0a3e.

That was not the first time: 80deeda repaired "two dead evidence links a public clone would hit". So
this defect has now shipped THREE times, and the reason it keeps shipping is structural — the damage
is done by editing a HEADING, while the broken thing is a LINK somewhere else entirely. Nothing
about renaming a section shows you what pointed at it.

`tests/test_citations.py` does not cover this. It checks that cited REPO PATHS exist (scripts/,
tests/, app/ ...) — a different failure with a different mechanism. An anchor is not a path, so a
dead anchor sails past it. This file closes that gap and nothing else.

THE RULE: in README.md, DEMO.md and ARCHITECTURE.md, every `[text](#anchor)` must resolve to a
heading in the SAME file.

=========================== SCOPE, AND WHY NOTES.md IS EXCLUDED ===========================

NOTES.md is an APPEND-ONLY ENGINEERING LOG. Its historical entries deliberately quote superseded
structure — that is the point of a log, and rewriting history to satisfy a grep would be the actual
dishonesty. Forcing its old anchors to resolve would fight its purpose. Same exclusion, and the same
reasoning, as `tests/test_restore_instructions.py`.

The three files here are the JUDGE-FACING set: a reader follows them, and a dead link costs them
directly. ARCHITECTURE.md carries the most intra-doc links of the three (10), which is exactly why
it is in scope rather than assumed safe.

=========================== GETTING THE SLUG RIGHT IS THE WHOLE GAME ===========================

A slug function that computes differently from GitHub is not a weaker guard — it is a BROKEN one, in
both directions at once: it false-passes real dead links and false-fails live ones, and the second
failure mode trains people to delete it. So the rules below are pinned to REAL heading -> anchor
pairs from these very documents (`test_the_slug_function_matches_github_on_real_pairs`), not to a
reading of GitHub's docs.

GitHub's slugification, as exercised here:
  * lowercase;
  * drop anything that is not a letter, digit, underscore, space or hyphen — so `&`, `·` and `—` all
    VANISH, each leaving its two surrounding spaces behind;
  * spaces -> hyphens, which is why those three characters each yield a DOUBLE hyphen
    ("Tests & evals" -> `tests--evals`, "1 · Three ..." -> `1--three-...`);
  * a repeated slug gets `-1`, `-2` appended in document order.

The duplicate rule is implemented although NO file currently has a duplicate heading — an
unimplemented rule is a bug that waits for the first collision, and collisions arrive silently.

TWO PARSING TRAPS, both hit while writing this:

  1. CODE FENCES. A ```bash block full of `# comment` lines parses as a pile of H1 headings, and a
     `[x](#y)` inside a fence is a code sample, not a link. Fences are stripped FIRST. Measured: the
     README has 30 headings, not the 35 a naive scan reports.
  2. `](` IS REQUIRED. A loose `\\(#[a-z0-9-]+\\)` also matches a hex colour in prose — `(#e5484d)` —
     and would report it as a dead anchor. The link form is what is matched, never a bare paren.

Hermetic: pure file reads + regex. No cluster, no network, no OpenAI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# OFFLINE (pure file reads), so this runs in `docs-ci.yml` on a docs-only push — which is exactly
# the push that renames a heading. Offline-ness is ENFORCED by tests/test_doc_guard_marker.py.
pytestmark = pytest.mark.doc_guard

_ROOT = Path(__file__).resolve().parents[1]

#: The judge-facing set. NOT NOTES.md — see the module docstring.
_DOCS = ("README.md", "DEMO.md", "ARCHITECTURE.md")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$", re.M)
#: The LINK form. The `](` is load-bearing: without it this matches `(#e5484d)` in prose.
_LINK_RE = re.compile(r"\[([^\]]*)\]\(#([^)]+)\)")


def _strip_code_fences(text: str) -> str:
    """Blank out fenced blocks, preserving line count so reported line numbers stay honest."""
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def slugify(heading: str) -> str:
    """GitHub's heading -> anchor transformation. Pinned to real pairs by the test below."""
    s = heading.strip().lower()
    s = re.sub(r"<[^>]+>", "", s)        # inline HTML (<sub>, <br/>) contributes nothing
    s = re.sub(r"[^\w\s-]", "", s)       # keep letters/digits/underscore/space/hyphen
    return re.sub(r"\s", "-", s)


def _anchors_of(text: str) -> list[str]:
    """Every anchor a heading defines, in document order, with GitHub's -1/-2 duplicate suffixes."""
    seen: dict[str, int] = {}
    anchors: list[str] = []
    for m in _HEADING_RE.finditer(text):
        base = slugify(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        anchors.append(base if n == 0 else f"{base}-{n}")
    return anchors


def _links_of(text: str) -> list[tuple[int, str, str]]:
    """(line number, link text, anchor) for every intra-document link."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in _LINK_RE.finditer(line):
            out.append((i, m.group(1), m.group(2)))
    return out


def test_the_slug_function_matches_github_on_real_pairs():
    """PIN THE TRANSFORMATION. Every pair is a heading and the anchor a live link already uses.

    If this fails, the guard below is not measuring what it claims and its results mean nothing —
    so this runs first, on the hard characters (`&`, `·`, `—`), each of which yields a DOUBLE hyphen.
    """
    pairs = [
        ("Getting started", "getting-started"),
        ("Tests & evals", "tests--evals"),                                      # &  -> --
        ("Production readiness & security", "production-readiness--security"),  # &  -> --
        ("The baseline is not a strawman", "the-baseline-is-not-a-strawman"),
        ("Investigated and cut", "investigated-and-cut"),
        ("1 · Three deliberately-separated schemas",
         "1--three-deliberately-separated-schemas"),                            # ·  -> --
        ("5 · Data — THE ORDER IS NOT FREE", "5--data--the-order-is-not-free"),  # · and — -> --
    ]
    wrong = [(h, exp, got) for h, exp in pairs if (got := slugify(h)) != exp]
    assert not wrong, (
        "slugify() diverges from GitHub. A wrong slug function false-passes dead links AND "
        "false-fails live ones:\n  "
        + "\n  ".join(f"{h!r}: expected {exp!r}, got {got!r}" for h, exp, got in wrong)
    )


def test_the_guarded_docs_exist():
    """A renamed or deleted doc must fail loudly, not silently shrink the guarded set to nothing."""
    missing = [d for d in _DOCS if not (_ROOT / d).exists()]
    assert not missing, f"guarded documents are missing: {missing}"


@pytest.mark.parametrize("doc", _DOCS)
def test_every_intra_document_anchor_resolves(doc: str):
    """THE GUARD. Rename a heading without repointing its links and this fails, naming both."""
    text = _strip_code_fences((_ROOT / doc).read_text(encoding="utf-8"))
    anchors = set(_anchors_of(text))
    dead = [(ln, txt, a) for ln, txt, a in _links_of(text) if a not in anchors]

    assert not dead, (
        f"{doc} has links to headings that do not exist — a reader clicking them goes nowhere:\n  "
        + "\n  ".join(f"{doc}:{ln}  [{txt}](#{a})" for ln, txt, a in dead)
        + "\n\nA section rename deletes its anchor while every link to it stays valid-looking. "
        "Repoint the link, or restore the heading."
    )


@pytest.mark.parametrize("doc", _DOCS)
def test_the_anchor_check_is_not_vacuous(doc: str):
    """A doc with zero links would pass the guard above by having nothing to check.

    Not a style rule — a coverage floor. If a future edit strips every intra-doc link from a file,
    the guard silently stops guarding it, and a green run would mean 'nothing to check' while
    reading as 'everything resolves'. That is the ninth-vacuous-check shape, so it fails instead.
    """
    text = _strip_code_fences((_ROOT / doc).read_text(encoding="utf-8"))
    assert _links_of(text), (
        f"{doc} now contains NO intra-document links, so test_every_intra_document_anchor_resolves "
        f"passes vacuously for it. If that is deliberate, drop {doc} from _DOCS with a reason "
        "rather than leaving a guard that guards nothing."
    )
