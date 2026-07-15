"""THE TYPECHECK THAT COULD NOT FAIL — and the guard that makes the wrong command unusable.

`npx tsc --noEmit` in `frontend/` typechecks **ZERO FILES** and exits **0, unconditionally.**

WHY, and it is structural rather than a quirk: `frontend/tsconfig.json` is a SOLUTION file —
`"files": []` plus `references` to `tsconfig.app.json` / `tsconfig.node.json`. A bare `tsc`
invocation resolves to it, finds an empty file list, checks nothing, and reports success. Measured:

    npx tsc --noEmit --listFiles | grep -c /src/   ->    0
    npx tsc -p tsconfig.app.json --listFiles ...   ->  446

Nine frontend gates in this project cited that command as evidence (NOTES:4319 through 6580 — the
entire grounding-seam arc). It was a check that could not fail, cited as proof that a check had
passed. It only ever surfaced because a missing import (`WitnessOutcome` in `App.tsx`) slipped past
it and CI — which runs `npx tsc -b` — caught it in 22 seconds.

**This is the same disease as the two dead vector indexes and the gitignore that never worked:** a
green result attributed to a cause nobody tested. It is the worst variant, because a passing check
feels like corroboration. `verify_corpus.py` EXPLAINed a query the application never runs; the
restore guard's 14-line proximity window passed its own bug; `test_citations.py`'s own docstring
carried the disease it was written to cure. **A check that cannot fail is not a check.**

SO THIS GUARD PINS THE CAUSE, NOT THE SYMPTOM — the correction Item 3's index check needed:
if a future session flattens `tsconfig.json` into a real project (giving it `include`/`files`), then
`--noEmit` stops being vacuous, the reason this rule exists evaporates, and THIS TEST FAILS so that
the change is a decision rather than a silent drift. A true rule resting on a stale premise is one
skeptical reader away from deletion.

CI-SAFE: pure file reads. No cluster, no npm, no network. Never calls run_seed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# OFFLINE (pure file reads — the module docstring says so, and it is enforced): runs in `docs-ci.yml`
# on a docs-only push. See tests/test_doc_guard_marker.py for how that offline-ness is guarded.
pytestmark = pytest.mark.doc_guard

_ROOT = Path(__file__).resolve().parents[1]
_FE = _ROOT / "frontend"

#: The ONE correct invocation. `tsc -b` builds the referenced projects, so it actually descends into
#: the 446 source files. `tsc -p tsconfig.app.json --noEmit` also works; `tsc --noEmit` does NOT.
CANONICAL = "tsc -b"

#: The command that cannot fail. Never cite it as evidence.
VACUOUS = re.compile(r"tsc\s+--noEmit(?!\s+--listFiles)")

#: Judge-facing surfaces + the machinery. NOTES.md is EXCLUDED and that is deliberate: it is an
#: append-only engineering log, and its nine historical gates cite the vacuous command ON PURPOSE
#: now — each is annotated in place with a correction pointer rather than rewritten, which is the
#: precedent this project already set (Phase 2's gen-6 claim, annotated by Item C; Item 2's
#: canonicalizer decision, annotated by Item 6). Rewriting history to satisfy a grep would be the
#: actual dishonesty — the same reason test_restore_instructions.py does not sweep NOTES either.
_SWEPT = [
    _ROOT / "README.md",
    _ROOT / "ARCHITECTURE.md",
    _ROOT / "DEMO.md",
    _ROOT / "FRONTEND.md",
    _ROOT / "CLAUDE.md",
    _ROOT / ".github" / "workflows" / "frontend-ci.yml",
    _ROOT / ".github" / "workflows" / "ci.yml",
    _FE / "package.json",
]


def test_the_solution_tsconfig_is_why_noemit_is_vacuous():
    """PIN THE CAUSE. If this changes, the rule below loses its reason and must be re-decided.

    `"files": []` + `references` is what makes a bare `tsc` check nothing. This is not an
    accident of Vite's template that we are working around — it is the mechanism, and asserting
    it is what keeps the ban on `--noEmit` from becoming a rule nobody can justify.
    """
    cfg = json.loads((_FE / "tsconfig.json").read_text(encoding="utf-8"))

    assert cfg.get("files") == [], (
        "frontend/tsconfig.json is no longer a solution file with an EMPTY file list.\n"
        "That is the entire reason `tsc --noEmit` was vacuous, and the reason this project bans it.\n"
        "If you deliberately flattened the config, `--noEmit` may now be a real check — re-derive "
        "that by running `npx tsc --noEmit --listFiles | grep -c /src/` and confirming it is NOT 0, "
        "then update this guard. Do not delete it because it 'looks stale'."
    )
    assert cfg.get("references"), "a solution file with no references checks nothing at all"


def _load_jsonc(path: Path) -> dict:
    """tsconfigs are JSONC — Vite's templates ship comments in them, and so do ours."""
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return json.loads(src)


def _covered_roots() -> list[str]:
    """Every path `tsc -b` actually descends into, derived from the solution file's references.

    Read from the configs themselves rather than hardcoded: a hardcoded list would be a second
    source of truth, and a second source of truth is a thing that can disagree with the first while
    everything stays green.
    """
    solution = _load_jsonc(_FE / "tsconfig.json")
    roots: list[str] = []
    for ref in solution.get("references", []):
        project = _load_jsonc(_FE / ref["path"].lstrip("./"))
        roots.extend(project.get("include", []))
        roots.extend(project.get("files", []))
    return roots


def test_every_typescript_file_in_the_frontend_is_actually_TYPECHECKED():
    """THE COVERAGE PIN — because a file outside every project reference is checked by NOTHING.

    ===================== THE BUG THIS EXISTS TO STOP RECURRING =====================

    `playwright.config.ts` and `tests-e2e/` — the browser geometry guard, the check standing over
    the only irreversible write in the product — were in NO typecheck project. `tsconfig.app.json`
    includes `src`; `tsconfig.node.json` includes `vite.config.ts`; the guard was in neither. And
    Playwright's transpiler STRIPS types without checking them, so nothing complained.

    It was not hypothetical. `reducedMotion` was set at the top level of `use`, where it is an
    unknown key that JavaScript SILENTLY IGNORES — it belongs under `contextOptions`. The two
    "reduced-motion" projects were therefore byte-identical duplicates of the normal-motion ones,
    and the guard's first green run ("12 passed") was really 2 projects run twice. **A whole
    dimension of the guard was fake and green**, and the typecheck is what found it.

    Fixing that file was the symptom. THIS is the cause: nothing asserted that the frontend's
    TypeScript is covered by the projects `tsc -b` builds. Add a directory tomorrow — `e2e-v2/`, a
    `bench/`, anything outside `src` — and it is silently unchecked again, in exactly the way that
    let a fake test dimension ship. So the coverage is enumerated, not assumed.
    """
    roots = _covered_roots()
    assert "tests-e2e" in roots and "playwright.config.ts" in roots, (
        "the browser geometry guard's own source is no longer in a typecheck project.\n"
        f"covered roots: {roots}\n"
        "It was unchecked once already, and a fake reduced-motion dimension shipped green because "
        "of it. tsconfig.json must reference a project that includes `tests-e2e`."
    )

    covered_dirs = [r for r in roots if not r.endswith((".ts", ".tsx"))]
    covered_files = [r for r in roots if r.endswith((".ts", ".tsx"))]

    unchecked: list[str] = []
    for f in _FE.rglob("*.ts*"):
        if f.suffix not in (".ts", ".tsx"):
            continue
        rel = f.relative_to(_FE).as_posix()
        if rel.startswith(("node_modules/", "dist/")) or ".d.ts" in rel:
            continue
        if rel in covered_files:
            continue
        if any(rel.startswith(f"{d}/") for d in covered_dirs):
            continue
        unchecked.append(rel)

    assert not unchecked, (
        "THESE TYPESCRIPT FILES ARE TYPECHECKED BY NOTHING — `tsc -b` never descends into them:\n  "
        + "\n  ".join(unchecked)
        + f"\n\ncovered roots (from tsconfig.json's references): {roots}\n"
        "This is how the geometry guard's fake reduced-motion dimension stayed green: its own source "
        "was outside every project, and Playwright's transpiler strips types without checking them. "
        "Add the directory to a referenced tsconfig, or reference a new project from tsconfig.json."
    )


def test_ci_typechecks_with_the_command_that_can_actually_fail():
    """frontend-ci must run `tsc -b`. It is the only thing that caught the missing import."""
    ci = (_ROOT / ".github" / "workflows" / "frontend-ci.yml").read_text(encoding="utf-8")
    assert CANONICAL in ci, f"frontend-ci must typecheck with `{CANONICAL}`"
    assert not VACUOUS.search(ci), "frontend-ci must NOT typecheck with the vacuous `tsc --noEmit`"


def test_package_json_exposes_the_canonical_typecheck_and_the_build_gates_on_it():
    """One named command, so nobody has to remember which invocation is the real one.

    `npm run typecheck` is the canonical check. `npm run build` gates on it too (`tsc -b && vite
    build`) — which is why a session that ran `npm run build` was genuinely typechecked, while one
    that ran a bare `npx vite build` was not. That asymmetry is exactly how this hid for nine gates.
    """
    scripts = json.loads((_FE / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert scripts.get("typecheck") == CANONICAL, (
        f"frontend/package.json must expose `typecheck: {CANONICAL}` — the one command a future "
        f"session can run without knowing this file exists."
    )
    assert CANONICAL in scripts.get("build", ""), (
        "`npm run build` must gate on the real typecheck, so `npx vite build` is the only way to "
        "skip it — and skipping it is then a visible choice, not an accident."
    )


def test_no_judge_facing_surface_cites_the_vacuous_typecheck_as_evidence():
    """`tsc --noEmit` must never again be quoted as proof that the frontend typechecks.

    NOTES.md is excluded ON PURPOSE — see the module docstring. Its nine historical gates are
    annotated in place, not rewritten.
    """
    violations: list[str] = []
    for f in _SWEPT:
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if VACUOUS.search(line):
                rel = f.relative_to(_ROOT).as_posix()
                violations.append(f"{rel}:{i}  {line.strip()[:100]}")

    assert not violations, (
        "A SURFACE CITES `tsc --noEmit` — A COMMAND THAT TYPECHECKS ZERO FILES AND CANNOT FAIL.\n"
        f"Use `{CANONICAL}` (or `npm run typecheck`). See tests/test_frontend_typecheck.py.\n  "
        + "\n  ".join(violations)
    )
