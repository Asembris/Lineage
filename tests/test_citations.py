"""EVERY CITATION IN THIS REPO MUST RESOLVE. A verification claim nobody follows is not a claim.

WHY THIS EXISTS. Four load-bearing claims in shipped code and docs were defended by citations to
things that do not exist. None was caught by review, by CI, or by any test — only by someone finally
following the footnote:

  1. `app/services/aml_seam.py` cited a `verify_seam` script for the 57/463/980 witness census — THE
     most important caveat in the project (the 65.3% INCONCLUSIVE->approve disclosure, which silently
     approves 252 of the 300 laundering rows). That script has never existed, and the test it also
     named never contained the numbers. The census was asserted NOWHERE.
  2. `app/services/aml_graph.py` cited a `test_witness_soundness_against_oracle` in test_aml_brake for
     FLAG_CAPABLE's soundness. The test is real; that NAME never existed.
  3. `ARCHITECTURE.md` cited a `probe_closure_hash_parity` script for "Verified: the async and sync
     halves hash identically". It was a one-off probe in the GITIGNORED `scratchpad/`, promoted into a
     `scripts/` path a reader could supposedly run. The measurement was real; the artifact evaporated.
  4. `migrations/versions/0006_grounding_seam.py` cited a scratchpad probe as its evidence
     ("VERIFIED by running it, not by reasoning") — same evaporation, in a migration header.

Four failure modes, one disease: **confident provenance that nobody followed.** A false citation is
worse than no citation — it converts "unverified" into "verified" in the reader's mind at zero cost
to the writer, and prose review reads straight past it. This is doubly true of (1): the 65.3%
disclosure has now been undermined TWICE by something written confidently and never checked (the
phantom "728 / 48.5%" figure was the first). See NOTES "FABRICATED VERIFICATION CITATIONS".

So citations are checked, not trusted. Three properties:

  A. every RUNNABLE-LOOKING repo path cited in code or docs — anything under scripts/, tests/, app/,
     seed/, migrations/, lambda/, eval/, frontend/ — actually EXISTS;
  B. every qualified test citation resolves to a REAL test function in that module;
  C. `scratchpad/` — GITIGNORED, does not survive the session — may be cited ONLY in NOTES.md, the
     engineering log where "I ran this once" is the honest record. Never from application code,
     never from a judge-facing document, where it reads as a runnable artifact and is not one.

Bare filenames (`aml_graph.py`, "see catalog.py") are deliberately NOT checked: they are prose
pointers, not promises of a runnable artifact, and checking them drowns the real signal in noise.
The dangerous citation is the one that looks like something you could go and execute.

Hermetic: pure filesystem + AST. No cluster, no network, no OpenAI.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CODE_DIRS = ["app", "scripts", "seed", "migrations", "tests", "eval"]
DOCS = ["README.md", "ARCHITECTURE.md", "DEMO.md", "NOTES.md", "CLAUDE.md", "FRONTEND.md"]

# Application code + judge-facing surfaces. `scratchpad/` must never be cited from these.
NO_SCRATCHPAD = ("app", "scripts", "seed", "migrations", "README.md", "ARCHITECTURE.md", "DEMO.md")

# Only paths that promise a runnable/inspectable repo artifact are checked. See the docstring.
REPO_DIRS = ("app/", "scripts/", "seed/", "tests/", "migrations/", "lambda/", "eval/", "frontend/")

# ===================================== THE PHANTOM LEDGER =====================================
# The false citations this repo actually shipped. They are NAMED — in NOTES, in ARCHITECTURE, in
# aml_seam's and aml_graph's corrections, and in this file's own docstring — precisely in order to be
# REFUTED. Naming a phantom to correct it is not citing it.
#
# Same discipline as tests/test_oracle_boundary.py excluding docstrings: the modules that refuse the
# oracle must be able to SAY so. A correction that cannot name what it corrects is not a correction.
#
# INVENTING A NEW PHANTOM FAILS. This ledger is closed; adding to it is a deliberate act that says
# "we shipped another false citation", and it should feel like one.
KNOWN_PHANTOMS = {
    "scripts/verify_seam.py": (
        "NEVER EXISTED. Cited by aml_seam.py as the measurement behind the 57/463/980 census — the "
        "project's single most important caveat. Now really asserted in "
        "tests/test_decision_read_surface.py::test_the_witness_census_over_the_real_extract_is_57_463_980."
    ),
    "scripts/probe_closure_hash_parity.py": (
        "NEVER EXISTED under scripts/. It was a one-off probe in the gitignored scratchpad/, promoted "
        "to a repo path when ARCHITECTURE wrote the claim up. The durable backing is the SHARED "
        "canonicalizer plus test_certifier_closure_verification's column-set assertion."
    ),
}

# The same ledger, for the `module.py::test_name` form. A phantom TEST must also be nameable by the
# correction that refutes it (aml_graph.py's comment, and the NOTES entry recording the pattern).
KNOWN_PHANTOM_TESTS = {
    "tests/test_aml_brake.py::test_witness_soundness_against_oracle": (
        "NEVER EXISTED. Cited by aml_graph.py for FLAG_CAPABLE's soundness — the property that lets "
        "the laundering belief be stated as a decision rule at all. The claim is TRUE and the test is "
        "real; it is named test_witness_soundness_and_benign_false_positive_rates, in that same "
        "module. The claim was backed and the citation was false, which is the most insidious variant."
    ),
}

_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w-]+\.(?:tsx|ts|py|md|ya?ml|toml|sql|csv))(?![\w])")
_QUAL = re.compile(r"([\w/]*test_\w+\.py)::(\w+)")
_SCRATCH = re.compile(r"(?<![\w/])scratchpad/[\w./-]+")


def _scanned() -> list[Path]:
    out: list[Path] = []
    for d in CODE_DIRS:
        p = ROOT / d
        if p.is_dir():
            out += [f for f in p.rglob("*.py") if "__pycache__" not in f.parts]
    out += [ROOT / d for d in DOCS if (ROOT / d).exists()]
    return out


def _real_tests() -> dict[str, set[str]]:
    """test-module filename -> the test functions actually defined in it."""
    mods: dict[str, set[str]] = {}
    for f in (ROOT / "tests").rglob("test_*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        mods[f.name] = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
        }
    return mods


def _resolves(tok: str) -> bool:
    return (ROOT / tok).exists() or (ROOT / "frontend" / tok).exists()


def test_every_cited_repo_path_exists():
    """(A) Follow every runnable-looking path citation in code and docs.

    `scripts/verify_seam.py` and `scripts/probe_closure_hash_parity.py` both die here — they are in
    KNOWN_PHANTOMS only so the corrections that refute them may name them. A NEW dangling citation
    fails, loudly, with the file and line that wrote it.
    """
    broken: list[str] = []
    for f in _scanned():
        rel = f.relative_to(ROOT).as_posix()
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for tok in _PATH.findall(line):
                if not tok.startswith(REPO_DIRS) or tok in KNOWN_PHANTOMS:
                    continue
                if not _resolves(tok):
                    broken.append(f"{rel}:{i}  cites `{tok}` — WHICH DOES NOT EXIST")

    assert not broken, (
        "CITATION(S) POINT AT NOTHING. A claim defended by a file that does not exist is not "
        "defended — it merely reads as if it were, which is worse:\n  " + "\n  ".join(broken)
    )


def test_every_cited_test_name_is_a_real_test():
    """(B) A qualified test citation must resolve to a real test function.

    This is how `aml_graph.py`'s FLAG_CAPABLE soundness citation was found to name a test that has
    never existed — while the real test sat two lines away in the same module under another name.
    The claim was TRUE and the citation was FALSE, which is the most insidious version: following it
    yields nothing, and a reader concludes the claim is unbacked when in fact it is backed.
    """
    mods = _real_tests()
    broken: list[str] = []
    for f in _scanned():
        rel = f.relative_to(ROOT).as_posix()
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for mod, name in _QUAL.findall(line):
                if f"{mod}::{name}" in KNOWN_PHANTOM_TESTS:
                    continue  # named in order to be refuted — see the ledger
                base = Path(mod).name
                if base not in mods:
                    broken.append(f"{rel}:{i}  cites `{mod}::{name}` — NO SUCH TEST MODULE")
                elif name not in mods[base]:
                    broken.append(
                        f"{rel}:{i}  cites `{mod}::{name}` — that module defines no such test"
                    )

    assert not broken, (
        "CITATION(S) NAME A TEST THAT DOES NOT EXIST. The claim may still be true and the test may "
        "still exist under another name — but nobody can follow the citation, so the claim reads as "
        "unbacked:\n  " + "\n  ".join(broken)
    )


def test_scratchpad_is_never_cited_from_code_or_a_judge_facing_document():
    """(C) `scratchpad/` is GITIGNORED. Citing it outside NOTES.md promises a runnable artifact.

    This is exactly how ARCHITECTURE came to cite a `scripts/probe_closure_hash_parity.py`: a real
    scratchpad probe, silently promoted to a `scripts/` path when the claim was written up. And
    migration 0006's header cited `scratchpad/probe_fk_isolation.py` as the evidence for its central
    design decision — a file that no longer exists for anyone.

    NOTES.md is exempt, and only NOTES.md: "I ran a one-off probe and here is what it said" is the
    honest content of an engineering log, and NOTES says `scratchpad/...`, which is TRUE. Everywhere
    else a reader reasonably expects to be able to run what you cite. If the measurement matters
    enough to cite in shipped code, COMMIT the probe under scripts/. If it does not, say plainly that
    it was a one-off and name the durable thing that backs the claim now.
    """
    offenders: list[str] = []
    for f in _scanned():
        rel = f.relative_to(ROOT).as_posix()
        if not rel.startswith(NO_SCRATCHPAD):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for hit in _SCRATCH.findall(line):
                offenders.append(f"{rel}:{i}  cites `{hit}`")

    assert not offenders, (
        "A GITIGNORED, EPHEMERAL PROBE IS CITED AS IF IT WERE A REPO ARTIFACT. It will not survive "
        "the session and the reader cannot run it. Either commit it under scripts/ and cite THAT, or "
        "state plainly that the measurement was a one-off and say what DURABLE thing backs the claim "
        "now:\n  " + "\n  ".join(offenders)
    )


def test_the_phantom_ledger_is_closed_and_every_entry_earns_its_place():
    """Pinned, so the ledger cannot be quietly widened to make a new false citation go away.

    Each entry must (a) genuinely not exist — otherwise its reason is gone and it should be removed —
    and (b) carry a stated reason. Same posture as test_oracle_boundary's two-file ALLOWLIST.
    """
    assert set(KNOWN_PHANTOMS) == {
        "scripts/verify_seam.py",
        "scripts/probe_closure_hash_parity.py",
    }, KNOWN_PHANTOMS
    for path, reason in KNOWN_PHANTOMS.items():
        assert not _resolves(path), (
            f"`{path}` NOW EXISTS. It is no longer a phantom — remove it from KNOWN_PHANTOMS so the "
            "real citation is checked like every other."
        )
        assert len(reason) > 40, f"{path} has no stated reason"

    assert set(KNOWN_PHANTOM_TESTS) == {
        "tests/test_aml_brake.py::test_witness_soundness_against_oracle",
    }, KNOWN_PHANTOM_TESTS
    mods = _real_tests()
    for qual, reason in KNOWN_PHANTOM_TESTS.items():
        mod, name = qual.split("::")
        assert name not in mods.get(Path(mod).name, set()), (
            f"`{qual}` NOW EXISTS. It is no longer a phantom — remove it from KNOWN_PHANTOM_TESTS."
        )
        assert len(reason) > 40, f"{qual} has no stated reason"
