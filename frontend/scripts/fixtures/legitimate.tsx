/*
 * THE LEGITIMATE ARRANGEMENT. The guard must leave every component in this file ALONE.
 *
 * A guard that trips on everything is as useless as one that trips on nothing, and it is the more
 * dangerous of the two because it gets disabled. Both halves are asserted: the violations below are
 * caught at their exact lines, and these are not.
 *
 * NOT part of the app build — `tsconfig.app.json` includes only `src`.
 */
import type { Decision } from "../../src/api/types";
import type { AmlInterrogationResponse } from "../../src/api/types";

/* The evidence pane: renders from an interrogation ALONE. This is the shape Rung 2 ships. */
export function LegitEvidencePane({ interrogation }: { interrogation: AmlInterrogationResponse }) {
  return <div>{interrogation.witnesses.length} witnesses</div>;
}

/* An audit-layer row: a Decision, and the label with it. This is LEGITIMATE — `is_fraud` is an
   audit fact, attached to a decision that was already made without it, and the feed is exactly
   where it belongs. The guard must not object. */
export function LegitFeedRow({ d }: { d: Decision }) {
  return <div>{d.is_fraud ? "labelled fraud" : "clean"}</div>;
}

/* The composition root's shape: it holds both layers, and hands the evidence surface an ID.
   Zero props, so no prop surface to violate — exempt BY STRUCTURE, not by allowlist. */
export function LegitRoot() {
  const txnId = "00000000-0000-0000-0000-000000000000";
  const showEvidence = true;
  return showEvidence ? <LegitEvidencePane interrogation={{} as AmlInterrogationResponse} /> : <span>{txnId}</span>;
}
