/*
 * VIOLATION 4 — ADJACENCY. The answer key beside the exam, with every component individually legal.
 *
 * THIS FIXTURE EXISTS BECAUSE THE GUARD WAS ONCE BROKEN HERE AND GREEN.
 *
 * The first draft's fixtures covered composition (A) and channel (B) but not adjacency (C), and C
 * did not work: it coloured a component only by its PROPS, and the evidence SURFACE takes
 * `{ txnId: UUID }` — colourless on purpose, because the join between the layers is a bare id. So
 * the EVIDENCE colour sat on the inner pane and the check never looked at the surface's real mount
 * site. Mounting the evidence surface beside the decision feed produced NOTHING. A check that
 * cannot fail on the change it protects against is not a check — the lesson this project has now
 * learned from `tsc --noEmit`, the 14-line proximity window, and here.
 *
 * The fix was to propagate colour through the RENDER GRAPH. This fixture is what keeps it fixed.
 *
 * Below: `Surface` takes only an id (colourless props, EVIDENCE by what it renders). `AuditFeed`
 * takes only decisions. Neither prop surface holds both layers, so check A is silent — correctly.
 * They are simply mounted side by side, and the reader sees the label while reading the witness.
 *
 * The guard must flag the <Surface> mount at line 39.
 */
import type { AmlInterrogationResponse, Decision, UUID } from "../../src/api/types";

function InnerEvidence({ interrogation }: { interrogation: AmlInterrogationResponse }) {
  return <div>{interrogation.witnesses.length}</div>;
}

/** Colourless props — an id and nothing else. EVIDENCE only by what it renders. */
function Surface({ txnId }: { txnId: UUID }) {
  return <InnerEvidence interrogation={{ transaction_id: txnId } as AmlInterrogationResponse} />;
}

function AuditFeed({ decisions }: { decisions: Decision[] }) {
  return <div>{decisions.filter((d) => d.is_fraud).length} labelled fraud</div>;
}

export function SideBySide({ txnId, decisions }: { txnId: UUID; decisions: Decision[] }) {
  return (
    <div>
      <Surface txnId={txnId} />
      <AuditFeed decisions={decisions} />
    </div>
  );
}
