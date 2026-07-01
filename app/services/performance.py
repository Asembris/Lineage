"""belief_performance — staleness MEASURED from real decisions (never hardcoded).

recompute_belief_performance() aggregates a belief's driven decisions into time windows and
writes belief_performance rows. Per window, over rows WHERE driving_belief_id = the belief:

  confidence          = correct / total
                        correct = (approve AND NOT fraud) OR (decline/blocked AND fraud)
  false_positive_rate = (declined/blocked legit) / (legit total)
  frauds_approved     = approve AND is_fraud

Nothing here writes a confidence number by hand — it is a pure function of the stored
verdicts and ground-truth labels. "Valid then / rotten now" is therefore the FIRST vs. LAST
window of this table, and it moves when the underlying data moves (the counterfactual test
proves exactly that).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import text

from app.db import SessionLocal
from app.models import BeliefPerformance

_WINDOW_AGG = text(
    """
    SELECT
      COUNT(*)                                                        AS n,
      SUM(CASE WHEN verdict='approve' AND is_fraud THEN 1 ELSE 0 END) AS frauds_approved,
      SUM(CASE WHEN (verdict='approve' AND NOT is_fraud)
                 OR (verdict IN ('decline','blocked') AND is_fraud)
               THEN 1 ELSE 0 END)                                     AS correct,
      SUM(CASE WHEN verdict IN ('decline','blocked') AND NOT is_fraud THEN 1 ELSE 0 END)
                                                                      AS fp,
      SUM(CASE WHEN NOT is_fraud THEN 1 ELSE 0 END)                   AS legit
    FROM decisions
    WHERE driving_belief_id = :belief
      AND decided_at >= :start AND decided_at < :end
    """
)


async def recompute_belief_performance(
    belief_id: uuid.UUID, windows: list[tuple[dt.datetime, dt.datetime]]
) -> list[dict]:
    """Recompute belief_performance for `belief_id` over `windows`. Idempotent per belief.

    Deletes this belief's existing rows and rewrites one row per non-empty window. Returns the
    computed rows (ordered as given) for convenience.
    """
    computed: list[dict] = []
    async with SessionLocal() as s:
        async with s.begin():
            await s.execute(
                text("DELETE FROM belief_performance WHERE belief_id = :b"),
                {"b": belief_id},
            )
            for start, end in windows:
                r = (
                    await s.execute(
                        _WINDOW_AGG, {"belief": belief_id, "start": start, "end": end}
                    )
                ).mappings().first()
                n = r["n"] or 0
                if n == 0:
                    continue  # no belief-driven decisions in this window
                confidence = (r["correct"] or 0) / n
                legit = r["legit"] or 0
                fp_rate = (r["fp"] or 0) / legit if legit else 0.0
                frauds_approved = int(r["frauds_approved"] or 0)
                s.add(
                    BeliefPerformance(
                        belief_id=belief_id,
                        window_start=start,
                        window_end=end,
                        confidence=confidence,
                        false_positive_rate=fp_rate,
                        frauds_approved=frauds_approved,
                    )
                )
                computed.append(
                    {
                        "window_start": start,
                        "window_end": end,
                        "n": n,
                        "confidence": confidence,
                        "false_positive_rate": fp_rate,
                        "frauds_approved": frauds_approved,
                    }
                )
    return computed
