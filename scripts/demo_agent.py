"""LIVE end-to-end: the OpenAI agent scores a current-window transaction on crimson-7.

Restores clean state, embeds the belief for real, then picks a WINDOW-7 target-pattern
transaction whose ground truth is FRAUD and lets the living agent (crimson-7) score it with
a genuine OpenAI call. The money moment: crimson-7 approves fraud because of a belief a
long-dead ancestor (crimson-0) formed under conditions that no longer hold.

Run:  PYTHONPATH=. .venv/Scripts/python.exe -m scripts.demo_agent
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db import engine
from app.services import agent_brain
from app.sim.transactions import generate_all
from scripts.embed_beliefs import embed_active_beliefs
from seed.seed import aid, seed as run_seed


def _pick_current_fraud_txn():
    """A window-7 (living generation) target-pattern txn whose ground truth is fraud."""
    window7 = generate_all()[7]
    for t in window7:
        if t.on_pattern and t.is_fraud:
            return t
    return next(t for t in window7 if t.on_pattern)


async def main() -> None:
    print("restoring clean state + embedding belief with a real OpenAI vector...")
    await run_seed()
    await embed_active_beliefs()

    txn = _pick_current_fraud_txn()
    crimson7 = aid("crimson-7")
    print(f"\nLIVE: crimson-7 scoring current-window transaction {txn.txn_ref}")
    print(
        f"  merchant={txn.merchant!r}  mcc={txn.mcc}  amount=${txn.amount}  "
        f"account_age_months={txn.account_age_months:.1f}"
    )
    print(f"  ground-truth is_fraud={txn.is_fraud}  (hidden from the agent)\n")

    result = await agent_brain.score_transaction(
        crimson7,
        txn_ref=txn.txn_ref,
        merchant=txn.merchant,
        mcc=txn.mcc,
        amount=txn.amount,
        account_age_months=txn.account_age_months,
        is_fraud=txn.is_fraud,
    )

    print("retrieved beliefs (CockroachDB vector search, cosine distance):")
    for c in result["candidates"]:
        print(f"  dist={c['distance']:.4f}  id={c['id']}  {c['rule_text']}")

    print("\n=== LIVE OPENAI VERDICT (structured) ===")
    print(f"  verdict           : {result['verdict']}")
    print(f"  confidence        : {result['confidence']:.2f}")
    print(f"  driving_belief_id : {result['driving_belief_id']}")
    print(f"  rationale         : {result['rationale']}")
    print(f"  persisted decision: {result['decision_id']}")

    if result["verdict"] == "approve" and result["is_fraud"]:
        print(
            "\n  >>> The living agent APPROVED a FRAUDULENT transaction because of the "
            "inherited belief.\n  >>> This is the stale-belief harm the supervisor will "
            "trace back to crimson-0."
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
