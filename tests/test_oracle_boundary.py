"""THE ORACLE BOUNDARY — the deciding path provably cannot see a ground-truth label (G4).

`aml_transactions.is_laundering` and the `aml_pattern_members` / `aml_pattern_instances` pair
are the ANSWER KEY. The grounding seam's whole claim is that the azure belief's verdicts were
produced by a LABEL-FREE structural witness and that `is_fraud` was attached only AFTERWARDS,
from the real label the rule never saw — exactly the way `seed/backfill_decisions.py` computes a
verdict from observable features (`_decision_from`) before stamping the world's ground truth. If
any module on the deciding path could read a label, that claim is hollow and the "grounded
agent" is a ground-truth lookup wearing a graph's clothes (NOTES "Roadmap Item 4").

WHY THIS TEST IS AN AST WALK **INCLUDING STRING CONSTANTS**, AND WHY THAT IS THE WHOLE POINT
--------------------------------------------------------------------------------------------
The obvious guard — the one this project already ships in `test_aml_routes.py` for the paid LLM
path — walks `ast.Name` / `ast.Attribute` / `ast.Import`. Applied here it would PASS WHILE
PROVING NOTHING, because the deciding path reads the database through RAW SQL:

    _LOAD_SQL = text("SELECT id, from_account_id, to_account_id, ts, amount_paid, payment_format
                      FROM aml_transactions")

Adding `is_laundering` to that SELECT creates no `Name` node and no `Attribute` node — it edits a
STRING. A Name/Attribute-only guard is green, the witness is reading the answer key, and the
project's central integrity claim is silently false. So this walk also inspects every string
constant that is not a docstring.

Docstrings are excluded DELIBERATELY and precisely: five modules on the path discuss the oracle
in prose in order to state that they refuse to read it (`aml_graph`'s "READS NO LABEL COLUMN",
`models.Decision`'s "is_fraud from aml_transactions.is_laundering"). Banning the WORDS would
force those modules to stop explaining themselves. Docstring nodes are identified structurally
(the first statement of a Module/Class/Function whose value is a string), never by content.

`app/aml_models.py` is ALLOWLISTED, with a reason: it is the ORM that DEFINES these columns.
A schema definition is not a read. Nothing on the deciding path imports it for a label.

Both failure modes are demonstrated, not assumed: the guard was made to trip on a real SQL-string
reference AND on a real Python attribute access, each failing at the exact file and line, and
then reverted. A guard that cannot be shown to fail is theatre — this project has now proven that
twice (Item E's tripwire; G2's guard 3, which nearly passed for the wrong reason).
"""

from __future__ import annotations

import ast
from pathlib import Path

# The answer key. `is_fraud` is NOT here: it is a column of the moat's own `decisions` table and
# the seam WRITES it (after the verdict). What must never be READ on the deciding path is the
# AML evidence layer's label and its pattern membership.
ORACLE_TOKENS = (
    "is_laundering",
    "aml_pattern_members",
    "aml_pattern_instances",
    "AmlPatternMember",
    "AmlPatternInstance",
)

_ROOT = Path(__file__).resolve().parents[1]

# Every module a VERDICT flows through. None of these may reference the answer key, at all.
# `aml_seam` is the decider itself; the rest are the surfaces a decision is read back through.
DECIDING_PATH = (
    _ROOT / "app" / "services" / "aml_seam.py",         # the seam's decider (G4)
    _ROOT / "app" / "services" / "aml_graph.py",        # the witness
    _ROOT / "app" / "services" / "aml_evidence.py",     # pure evidence rendering
    _ROOT / "app" / "services" / "aml_interrogate.py",  # deterministic interrogation
    _ROOT / "app" / "services" / "verdict_guard.py",    # the brake
    _ROOT / "app" / "services" / "performance.py",      # the aggregation
    _ROOT / "app" / "services" / "catalog.py",          # the read surface
    _ROOT / "app" / "services" / "counterfactual.py",
    _ROOT / "app" / "services" / "certificate.py",
    _ROOT / "app" / "models.py",
    _ROOT / "app" / "schemas.py",
    _ROOT / "seed" / "seed.py",
    # J1 — the LIVE governed decision route's service. It is on the deciding path (it runs the
    # witness) AND it must read the label (it writes the NOT NULL `is_fraud`), so it is listed here
    # to be WALKED and allowlisted below to be permitted, under a pinned test of its own. Listing it
    # only in ALLOWLIST would leave it unwalked, which is worse than a violation: silent.
    _ROOT / "app" / "services" / "aml_live_decide.py",
)

# Two files, each with a stated reason, each pinned by its own test below so the allowlist cannot
# be quietly widened to make a violation go away.
#
#   aml_models.py             — the ORM that DEFINES these columns. A definition is not a read.
#   backfill_aml_decisions.py — MUST read the label: it writes `decisions.is_fraud`. That is the
#                               seam's entire purpose. It is guarded instead by
#                               `test_the_backfill_reads_the_label_only_to_attach_ground_truth`,
#                               which pins the read to a single named phase-2 SQL constant.
#   aml_live_decide.py        — MUST read the label for the same reason, at HTTP request time:
#                               `decisions.is_fraud` is NOT NULL, so the live route cannot write a
#                               row without one. Guarded identically, by
#                               `test_the_live_decider_reads_the_label_only_to_attach_ground_truth`.
BACKFILL = _ROOT / "seed" / "backfill_aml_decisions.py"
LIVE_DECIDER = _ROOT / "app" / "services" / "aml_live_decide.py"
ALLOWLIST = {_ROOT / "app" / "aml_models.py", BACKFILL, LIVE_DECIDER}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id()s of every Constant node that is a docstring (structurally, never by content)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.add(id(first.value))
    return out


def oracle_references(path: Path) -> list[tuple[int, str, str]]:
    """Every real reference to an oracle token in `path`. (line, token, how).

    Catches BOTH shapes a label read can take:
      * Python — a Name / Attribute / import of the token  (ORM access, model import)
      * SQL    — the token inside a non-docstring string constant  (a raw SELECT)
    Comments are invisible to the AST; docstrings are skipped structurally.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    hits: list[tuple[int, str, str]] = []

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)

        if isinstance(node, ast.Name) and node.id in ORACLE_TOKENS:
            hits.append((line, node.id, "python-name"))
        elif isinstance(node, ast.Attribute) and node.attr in ORACLE_TOKENS:
            hits.append((line, node.attr, "python-attribute"))
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in ORACLE_TOKENS:
                    hits.append((line, a.name, "python-import"))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if any(t in a.name for t in ORACLE_TOKENS):
                    hits.append((line, a.name, "python-import"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue  # prose that REFUSES to read the label is not a read
            for token in ORACLE_TOKENS:
                if token in node.value:
                    hits.append((line, token, "string-literal (SQL?)"))

    return hits


def test_the_deciding_path_cannot_see_a_label():
    """ZERO modules on the deciding path reference the answer key — in Python OR in SQL."""
    violations: list[str] = []
    for path in DECIDING_PATH:
        assert path.exists(), f"deciding-path module missing: {path}"
        if path in ALLOWLIST:
            continue
        for line, token, how in oracle_references(path):
            rel = path.relative_to(_ROOT).as_posix()
            violations.append(f"{rel}:{line}  reads the ORACLE `{token}`  [{how}]")

    assert not violations, (
        "the deciding path can see a ground-truth label — the grounded-agent claim is hollow:\n  "
        + "\n  ".join(violations)
        + "\n\nThe witness must recompute structure from the UNLABELED edge set. If you need the "
        "label to SCORE a result, read it in a test/eval as an oracle, after the fact."
    )


def test_the_witness_query_selects_no_label_column():
    """The one SELECT the decider issues, asserted column-for-column.

    Belt and braces with the walk above: this pins the actual projection, so a label arriving via
    `SELECT *` (which contains no oracle token and would slip past a token scan) fails here.
    """
    from app.services import aml_graph

    sql = str(aml_graph._LOAD_SQL)
    assert "*" not in sql, f"the witness must not SELECT *, it would pull the label:\n{sql}"
    projected = {
        "id",
        "from_account_id",
        "to_account_id",
        "ts",
        "amount_paid",
        "payment_format",
    }
    select_clause = sql.split("FROM")[0]
    named = {c.strip() for c in select_clause.replace("SELECT", "").split(",") if c.strip()}
    assert named == projected, f"the witness's projection moved: {named}"
    for token in ORACLE_TOKENS:
        assert token not in sql, f"the witness's SELECT reads the oracle `{token}`"


def test_the_seam_decider_is_label_free_BY_TYPE_not_by_convention():
    """`decide()` takes a Graph and an Edge. Neither has anywhere to put a label.

    This is the structural claim the whole seam rests on, and it is stronger than any text scan:
    the decider cannot read a label because its inputs do not carry one. If a future session adds
    a label field to `Edge` (say, to "enrich" the graph), this fails — and it should, because the
    verdict would then be one attribute access away from being a ground-truth lookup.
    """
    import inspect

    from app.services import aml_seam
    from app.services.aml_graph import Edge, Graph

    for field in Edge.__dataclass_fields__:
        assert not any(t in field for t in ORACLE_TOKENS), f"Edge.{field} carries a label"
    assert set(Edge.__dataclass_fields__) == {
        "id", "src", "dst", "ts", "amount", "payment_format",
    }, Edge.__dataclass_fields__

    # The decider's entire input surface is (Graph, Edge). Both modules use
    # `from __future__ import annotations`, so annotations are strings — compare them as such.
    params = list(inspect.signature(aml_seam.decide).parameters.values())
    assert [(p.name, p.annotation) for p in params] == [("g", "Graph"), ("e", "Edge")], params
    assert list(inspect.signature(aml_seam.decide_all).parameters) == ["g"]

    # And nothing a Graph exposes is a label.
    for attr in ("by_id", "out_edges", "in_edges", "accounts", "pair"):
        assert hasattr(Graph([]), attr)
    assert not any(
        any(t in a for t in ORACLE_TOKENS) for a in dir(Graph([])) if not a.startswith("__")
    )


def unpinned_oracle_references(path: Path, constant: str) -> list[str]:
    """Every oracle reference in `path` that is NOT the value of the named SQL constant.

    The shared machinery behind both allowlist tests below. An allowlisted module is permitted to
    read the answer key for exactly one purpose — stamping `decisions.is_fraud` after the verdict
    already exists — and this is what confines that permission to ONE named, reviewable query.
    A second label read, a witness that peeks, a filter on the decide path: all appear here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)

    sanctioned: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == constant for t in node.targets
        ):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    sanctioned.add(id(sub))
    assert sanctioned, f"{constant} not found in {path.name} — the label read must be a named constant"

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings or id(node) in sanctioned:
                continue
            for token in ORACLE_TOKENS:
                if token in node.value:
                    violations.append(f"{path.name}:{node.lineno}  reads `{token}` outside {constant}")
        elif isinstance(node, ast.Attribute) and node.attr in ORACLE_TOKENS:
            violations.append(f"{path.name}:{node.lineno}  attribute access `{node.attr}`")
        elif isinstance(node, ast.Name) and node.id in ORACLE_TOKENS:
            violations.append(f"{path.name}:{node.lineno}  name reference `{node.id}`")
    return violations


def test_the_live_decider_reads_the_label_only_to_attach_ground_truth():
    """J1's live route MAY read `is_laundering` — it writes the NOT NULL `is_fraud`. ONLY as
    phase-2 SQL, exactly the terms the backfill is allowlisted on.

    This is what makes allowlisting `aml_live_decide.py` safe rather than a hole. The module runs
    the witness AND touches the answer key, so without this the route could quietly grow a second
    label read — one that reached the verdict — and the walk would wave it through on the strength
    of the allowlist entry alone.

    THE ORDER IS ENFORCED BY CONSTRUCTION, not by this test: `decide()` takes only a `Graph` and an
    `Edge`, neither of which can carry a label, so no verdict can depend on what this query returns
    whenever it runs. This test pins the SURFACE — one named query, and nothing else.
    """
    violations = unpinned_oracle_references(LIVE_DECIDER, "_LABELS_SQL")
    assert not violations, (
        "the live decision route touches the answer key outside its one sanctioned ground-truth "
        "query:\n  " + "\n  ".join(violations)
    )


def test_the_backfill_reads_the_label_only_to_attach_ground_truth():
    """The backfill MAY read `is_laundering` — it writes `is_fraud`. But ONLY as phase-2 SQL.

    This is what makes allowlisting the backfill safe. Every oracle reference in the module must
    be the value assigned to the single constant `_LABELS_SQL`; a label appearing anywhere else
    (a second query, a witness that peeks, a filter on the decide loop) fails here.

    The ORDER — decide everything, then label — is the seam's integrity argument, and it is
    enforced by construction: `decide_all()` takes only the unlabeled Graph, so no verdict can
    depend on a row this query returns, whenever it runs.
    """
    tree = ast.parse(BACKFILL.read_text(encoding="utf-8"), filename=str(BACKFILL))
    docstrings = _docstring_nodes(tree)

    # Locate the string(s) assigned to _LABELS_SQL — the one sanctioned home for the label.
    sanctioned: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_LABELS_SQL" for t in node.targets
        ):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    sanctioned.add(id(sub))
    assert sanctioned, "_LABELS_SQL not found — the backfill's label read must be a named constant"

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings or id(node) in sanctioned:
                continue
            for token in ORACLE_TOKENS:
                if token in node.value:
                    violations.append(
                        f"{BACKFILL.name}:{node.lineno}  reads `{token}` outside _LABELS_SQL"
                    )
        elif isinstance(node, ast.Attribute) and node.attr in ORACLE_TOKENS:
            violations.append(f"{BACKFILL.name}:{node.lineno}  attribute access `{node.attr}`")
        elif isinstance(node, ast.Name) and node.id in ORACLE_TOKENS:
            violations.append(f"{BACKFILL.name}:{node.lineno}  name reference `{node.id}`")

    assert not violations, (
        "the backfill touches the answer key outside its one sanctioned ground-truth query:\n  "
        + "\n  ".join(violations)
    )


def test_the_allowlist_is_exactly_three_files_each_of_which_earns_it():
    """Pinned so a future session cannot widen the allowlist to make a violation go away.

    It went from two to three for J1's live decision route, and that widening was deliberate: the
    route must write the NOT NULL `is_fraud`, which no label-free module can do. The entry earns its
    place the same way the backfill does — by being covered by a dedicated pinned test, asserted
    below rather than asserted in prose.
    """
    assert ALLOWLIST == {_ROOT / "app" / "aml_models.py", BACKFILL, LIVE_DECIDER}, ALLOWLIST

    # aml_models.py earns it by DEFINING the columns (if that stops being true, its reason is gone).
    hits = oracle_references(_ROOT / "app" / "aml_models.py")
    assert any(t == "is_laundering" for _, t, _ in hits), hits

    # The two WRITERS earn it by each being pinned to a single named ground-truth query. Asserted,
    # not trusted: an allowlisted file whose pinned test was deleted would pass the identity check
    # above and be silently unguarded.
    for path in (BACKFILL, LIVE_DECIDER):
        assert path.exists(), path
        assert unpinned_oracle_references(path, "_LABELS_SQL") == []

    # The live decider is additionally WALKED. It has to be: it is application code that a route
    # calls, so it is on the deciding path in a way the backfill is not (that one is a seed script,
    # imported by nothing the app serves, and its pinned test above is its whole guard). Listing
    # aml_live_decide.py in ALLOWLIST but NOT in DECIDING_PATH would grant a permission that no walk
    # ever exercises — the exact silent hole the allowlist exists to prevent.
    assert LIVE_DECIDER in DECIDING_PATH, "the live decider is allowlisted but never walked"
    assert BACKFILL not in DECIDING_PATH  # a seed script; guarded by its pinned test alone
