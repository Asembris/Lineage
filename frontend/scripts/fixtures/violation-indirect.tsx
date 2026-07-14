/*
 * VIOLATION 2 — and this is the one that matters, because it is the one that will actually happen.
 *
 * THE WORD "Decision" DOES NOT APPEAR ANYWHERE IN THIS FILE.
 *
 * The component takes an `Investigation` — the console's existing resolved-investigation object,
 * which CONTAINS a `Decision` (lib/investigation.ts), which carries `is_fraud`. Every text-based
 * guard ever proposed for this — grep the token, grep the type name, restrict the imports — sees
 * an innocent file. The type checker walks `Investigation.decision.is_fraud` and sees the answer
 * key sitting on the prop surface next to the witness.
 *
 * This is why the guard is a type walk and not a scan. The guard must flag it at line 21.
 */
import type { Investigation } from "../../src/lib/investigation";
import type { AmlInterrogationResponse } from "../../src/api/types";

export function ExamWithAnswerKeyIndirect({
  interrogation,
  inv,
}: {
  interrogation: AmlInterrogationResponse;
  inv: Investigation;
}) {
  return (
    <div>
      {interrogation.transaction_id} — {inv.decision.verdict}
    </div>
  );
}
