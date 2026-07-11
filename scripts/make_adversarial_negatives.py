"""Hand-authored adversarial negatives for the Item-8 grounding eval — CLAUDE-CODE-AUTHORED.

PROVENANCE, STATED LOUDLY: every tuple this emits was written by Claude Code (the assistant),
NOT produced by gpt-4o-mini and NOT drawn from any dataset. They are deliberate perturbations:
each takes a REAL subject's real grounding (pulled verbatim from eval/grounding/goldenset.json so
the context cannot drift) and wraps it in a rationale that injects a claim the grounding does not
support. Each carries `label_faithful=False` and an `adversarial_note` naming exactly which
unsupported claim was injected and why the grounding cannot support it.

Why they exist: gpt-4o-mini at temperature 0 under a tight prompt mostly writes faithful prose,
so a faithful-only golden set would only measure the judge's false-positive rate, never its
ability to CATCH a hallucination. These supply labeled positives-for-hallucination that exercise
distinct failure modes — timing, corporate form, intent, fabricated aggregates, fabricated hops,
reversed flow direction, and injected external references — none of which verdict_guard.py can
catch, because it validates that citations resolve to real rows, not that the prose is accurate.
That is exactly the prose-entailment gap Item 8 tests. See NOTES.md "Roadmap Item 8".

The natural hallucinations already in the golden set (the model confabulating scatter-gather on
NO_WITNESS benign edges) are the OTHER source of negatives; these hand-authored ones are the
controlled, single-failure-mode complement.

Run:
  PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/make_adversarial_negatives.py
"""

from __future__ import annotations

import json
import pathlib
import sys

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "eval" / "grounding"
GOLDENSET_PATH = OUT_DIR / "goldenset.json"
NEGATIVES_PATH = OUT_DIR / "adversarial_negatives.json"

# Two real, verified-faithful FLAG anchors to perturb. Their grounding is real; only the
# actual_output prose is rewritten to inject an unsupported claim.
CYCLE_BASE = "185f748d-f99e-58f4-bcab-e1b7114a8d3a"   # verified directed cycle of length 6
SG_BASE = "0bc572e6-694a-53e7-ae62-6e3731d3d5f5"      # verified 7-intermediary scatter-gather

# (base_subject_id, failure_mode, adversarial_note, unfaithful_actual_output)
# The prose stays structurally plausible; the single injected unsupported clause is what makes it
# unfaithful. Notes state precisely why the grounding cannot support the claim.
SPECS: list[tuple[str, str, str, str]] = [
    (
        CYCLE_BASE,
        "timing",
        "Injects a 24-hour completion window. The grounding carries no timestamps at all — the "
        "structural facts and edge list report accounts, amounts and formats only — so no claim "
        "about how quickly the transfers occurred can follow from it.",
        "Claimed typology: CYCLE.\nThe six transfers form a directed cycle returning to account "
        "41ce7e, and all six were completed within a single 24-hour window, the funds racing back "
        "to their origin overnight — a textbook rapid-cycling laundering signature.",
    ),
    (
        CYCLE_BASE,
        "corporate-form",
        "Injects a shell-company / secrecy-jurisdiction claim. The grounding names only account "
        "identifiers and banks; it carries no corporate-registration, ownership, or jurisdiction "
        "data, so nothing supports characterizing any node as a shell company.",
        "Claimed typology: CYCLE.\nThe cycle routes funds from account 41ce7e through a chain that "
        "includes a shell company incorporated in a well-known offshore secrecy jurisdiction before "
        "returning to the origin, confirming a deliberate layering scheme.",
    ),
    (
        SG_BASE,
        "intent-mens-rea",
        "Injects deliberate intent to evade a specific reporting threshold. Intent is not "
        "observable in the grounding, and the cited amounts are individually well above $10,000, "
        "so a claim of structuring-to-stay-under-$10,000 contradicts the visible amounts too.",
        "Claimed typology: SCATTER-GATHER.\nThe source account 0de6aa deliberately split the funds "
        "across ten intermediaries specifically to keep each transfer under the $10,000 CTR "
        "reporting threshold and evade detection, then gathered them into one destination.",
    ),
    (
        SG_BASE,
        "fabricated-aggregate",
        "Injects a $2.4M total. The cited scatter amounts in the grounding are on the order of "
        "$13k-$19k each across ~10 legs (~$150k), so a $2.4 million aggregate is not derivable "
        "from — and contradicts — the visible figures.",
        "Claimed typology: SCATTER-GATHER.\nThe subject initiates a scatter from account 0de6aa to "
        "multiple intermediaries that gather into a single destination, laundering a combined total "
        "of over $2.4 million across the scatter legs in this single burst of activity.",
    ),
    (
        CYCLE_BASE,
        "fabricated-hop",
        "Injects an account id (9f3a1100) that appears nowhere in the grounding's edge list or "
        "cited path. The cited cycle runs 41ce7e -> dc9cc4 -> 6e804f -> ca932b -> d30dab -> 41ce7e; "
        "there is no such intermediary, so the hop is fabricated.",
        "Claimed typology: CYCLE.\nThe funds leave account 41ce7e and pass through intermediary "
        "account 9f3a11 before continuing around the ring and returning to 41ce7e after six "
        "transfers, completing the cycle.",
    ),
    (
        SG_BASE,
        "reversed-direction",
        "Reverses the flow. The structural facts state the source account SENDS to 10 distinct "
        "accounts (a fan-out); this rationale claims funds are gathered INTO 0de6aa from many "
        "sources and then scattered out, contradicting the stated out-degree.",
        "Claimed typology: SCATTER-GATHER.\nFunds are first gathered into account 0de6aa from ten "
        "distinct source accounts and are then scattered outward from 0de6aa to further "
        "destinations, forming the scatter-gather pattern.",
    ),
    (
        CYCLE_BASE,
        "external-reference",
        "Injects a specific regulatory reference (a FinCEN advisory number) that appears nowhere "
        "in the grounding. The retrieval context is the Altman et al. typology definitions plus "
        "the neutral structural facts; no advisory is present to cite.",
        "Claimed typology: CYCLE.\nThe six-hop return-to-origin structure matches the cyclical "
        "trade-based laundering pattern described in FinCEN Advisory FIN-2019-A003, and the cited "
        "transactions form exactly that documented cycle back to account 41ce7e.",
    ),
    (
        SG_BASE,
        "fabricated-recurrence",
        "Injects a monthly-recurrence claim. The grounding is a single-snapshot neighbourhood with "
        "no repetition or periodicity information, so nothing supports the pattern recurring over "
        "time.",
        "Claimed typology: SCATTER-GATHER.\nThe scatter from account 0de6aa into a common "
        "destination recurs on a monthly cadence, a hallmark of an established, routinized "
        "laundering pipeline rather than a one-off transfer.",
    ),
]


def main() -> None:
    if not GOLDENSET_PATH.exists():
        sys.exit(f"no golden set at {GOLDENSET_PATH}; run build_grounding_goldenset.py generate first")
    gold = {t["subject_id"]: t for t in json.loads(GOLDENSET_PATH.read_text(encoding="utf-8"))}

    out: list[dict] = []
    for i, (base_id, mode, note, unfaithful) in enumerate(SPECS, 1):
        base = gold.get(base_id)
        if base is None:
            sys.exit(f"base subject {base_id} not in golden set")
        out.append(
            {
                "subject_id": f"{base_id}::adv-{mode}",
                "base_subject_id": base_id,
                "category": "ADVERSARIAL_NEGATIVE",
                "failure_mode": mode,
                "provenance": "claude-code-authored-adversarial",
                "adversarial_note": note,
                # Real grounding, pulled verbatim so the injected claim is provably unsupported.
                "input": base["input"],
                "actual_output": unfaithful,
                "retrieval_context": base["retrieval_context"],
                "context": base["context"],
                # Ground truth by construction: these are unfaithful.
                "label_faithful": False,
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    NEGATIVES_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {len(out)} Claude-Code-authored adversarial negatives -> {NEGATIVES_PATH}")
    for o in out:
        print(f"  {o['failure_mode']:22s} <- {o['base_subject_id']}")


if __name__ == "__main__":
    main()
