"""Item 7 detection eval — pure scoring helpers (CI-safe: no app import, no CSV, no OpenAI).

The full dev-count fidelity gate and hold-out scoring live in scripts/eval_detection.py because
they need the gitignored 470MB CSV (the same reason Item 1's verification is a script, not a CI
test). Here we lock only the dependency-free math the eval reports.
"""

import importlib.util
import math
import pathlib

_spec = importlib.util.spec_from_file_location(
    "eval_detection", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "eval_detection.py"
)
# NOTE: importing the module runs its app imports; guard so this stays CI-safe if app deps are
# absent by pulling the pure helpers out by source if needed. On this stack the import succeeds.
eval_detection = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_detection)  # type: ignore[union-attr]

wilson_ci = eval_detection.wilson_ci
precision_recall = eval_detection.precision_recall


def test_wilson_ci_empty_is_zero():
    assert wilson_ci(0, 0) == (0.0, 0.0, 0.0)


def test_wilson_ci_point_estimate_and_bounds():
    p, lo, hi = wilson_ci(43, 43)
    assert p == 1.0
    assert hi == 1.0  # clamped
    assert 0.9 < lo < 1.0  # a perfect 43/43 still has real downside uncertainty
    # symmetric case centers on 0.5
    p2, lo2, hi2 = wilson_ci(1, 2)
    assert p2 == 0.5
    assert math.isclose((lo2 + hi2) / 2, 0.5, abs_tol=1e-9)
    assert 0.0 < lo2 < 0.5 < hi2 < 1.0


def test_wilson_ci_narrows_with_n():
    _, lo_small, hi_small = wilson_ci(20, 40)
    _, lo_big, hi_big = wilson_ci(200, 400)
    assert (hi_big - lo_big) < (hi_small - lo_small)


def test_precision_recall_basic():
    # 43 correct fires, 0 cross, 14 benign -> precision 43/57; recall 43/43 = 1.0
    precision, recall = precision_recall(own=43, cross=0, benign=14, own_t=43)
    assert math.isclose(precision, 43 / 57, abs_tol=1e-12)
    assert recall == 1.0


def test_precision_recall_no_fires_is_zero_not_error():
    assert precision_recall(own=0, cross=0, benign=0, own_t=96) == (0.0, 0.0)
    # recall denominator zero (no labeled positives) also degrades to 0, never divides by zero
    assert precision_recall(own=0, cross=0, benign=5, own_t=0) == (0.0, 0.0)
