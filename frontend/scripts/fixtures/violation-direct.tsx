/*
 * VIOLATION 1 — the exam and the answer key on one prop surface.
 *
 * Note the import is ALIASED. A guard that scans text for `Decision` in the props type sees
 * `Answer` and finds nothing. The type checker resolves the alias to the symbol DECLARED in
 * src/api/types.ts and catches it.
 *
 * The guard must flag <ExamWithAnswerKey> at line 22. If that stops happening, the guard has been
 * weakened and the build fails on this fixture.
 */
import type { Decision as Answer } from "../../src/api/types";
import type { AmlInterrogationResponse } from "../../src/api/types";

export function ExamWithAnswerKey({
  interrogation,
  decision,
}: {
  interrogation: AmlInterrogationResponse;
  decision: Answer;
}) {
  return (
    <div>
      {interrogation.witnesses.length} witnesses — and the answer is{" "}
      {decision.is_fraud ? "FRAUD" : "clean"}
    </div>
  );
}
