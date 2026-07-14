/*
 * VIOLATION 3 — the channel a props-only guard cannot see.
 *
 * This component takes NO PROPS. Check A has nothing to inspect: there is no prop surface, so
 * there is nothing on it, so it cannot carry both layers. A composition guard alone is green.
 *
 * And it fetches the answer key itself.
 *
 * An evidence surface must be renderable from /interrogate ALONE, and "alone" has to mean it — a
 * witness that reaches for the label through a side door is not a witness. So check B resolves
 * every symbol an evidence module imports and walks its type: `listDecisions` returns a
 * `DecisionListResponse`, which reaches `Decision`. Caught at the import, line 14.
 */
import { listDecisions } from "../../src/api/client";
import { useEffect, useState } from "react";
import type { AmlInterrogationResponse } from "../../src/api/types";

export function SneakyEvidencePane() {
  const [fraud, setFraud] = useState(false);
  const [interrogation] = useState<AmlInterrogationResponse | null>(null);

  useEffect(() => {
    // No prop ever carried the label. It was fetched.
    listDecisions({ limit: 1 }).then((r) => setFraud(r.decisions[0]?.is_fraud ?? false));
  }, []);

  return (
    <div>
      {interrogation?.witnesses.length ?? 0} witnesses — {fraud ? "FRAUD" : "clean"}
    </div>
  );
}
