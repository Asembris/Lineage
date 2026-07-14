/*
 * basis.ts — the FOUR-WAY basis, re-derived from the evidence, in the client.
 *
 * The decision feed persists THREE basis tags (`MATCH` / `CONCLUSIVE_NO` / `INCONCLUSIVE`) because
 * that is what the agent RECORDED. But `CONCLUSIVE_NO` is not one thing, and rendering it as one
 * thing is a lie the console must not tell:
 *
 *   SELF_LOOP      447 of the 463 — an account paying itself. That is not a transfer between two
 *                  accounts, so `aml_graph.Graph` excludes it from adjacency BY CONSTRUCTION and
 *                  no search was ever possible. Nothing was explored.
 *   CLOSED_SEARCH   16 of the 463 — the only ones where a search actually ran, traversed the
 *                  region, and closed inside the extract without finding a return path.
 *
 * Those two are the SAME persisted tag and they must NEVER render identically. The long-standing
 * gloss ("searched; there is no cycle") described all 463 as searches; it was true of sixteen.
 *
 * HOW THE FOURTH STATE IS READ, AND WHY IT IS NOT THE `detail` STRING.
 * The wire also carries the distinction as PROSE, on the witness's `detail`. Matching that prose
 * here would be a PROXY: a backend reword and this console silently renders a self-loop as a closed
 * search — corrupting in pixels the exact distinction that was corrected in the data — with nothing
 * failing anywhere. So the self-loop is re-derived the way `aml_graph.Edge` itself decides it,
 * from the subject row on the wire: `from_account_id === to_account_id`.
 *
 * That equivalence is not assumed. `tests/test_aml_interrogate.py::
 * test_the_console_four_way_basis_re_derives_from_account_identity_not_from_prose` asserts, over
 * all 1,500 edges, that the wire predicate, the graph's predicate, and the prose agree — and that
 * the four buckets are exactly 57 / 980 / 447 / 16. A reword fails a test instead of a pixel.
 *
 * EVIDENCE-ONLY. Nothing here imports, reaches, or could reach a `Decision`. That is enforced by
 * frontend/scripts/composition-guard.mjs, not by this comment.
 */

import type { AmlInterrogationResponse, AmlWitness } from "../api/types";

/** The basis the laundering belief acted on. FOUR states, because CONCLUSIVE_NO is two. */
export type Basis = "MATCH" | "INCONCLUSIVE" | "SELF_LOOP" | "CLOSED_SEARCH";

/** The typology the seam's belief decides on. The other three witnesses are competing structure. */
export const DECIDING_TYPOLOGY = "CYCLE";

/** The subject pays itself. Re-derived exactly as `aml_graph.Edge.is_self_loop` does. */
export function isSelfLoop(r: AmlInterrogationResponse): boolean {
  return r.subject.from_account_id === r.subject.to_account_id;
}

export function cycleWitness(r: AmlInterrogationResponse): AmlWitness | undefined {
  return r.witnesses.find((w) => w.typology === DECIDING_TYPOLOGY);
}

/** The four-way basis, or null if the deciding witness is absent from the response. */
export function basisOf(r: AmlInterrogationResponse): Basis | null {
  const cycle = cycleWitness(r);
  if (!cycle) return null;
  // A self-loop is never MATCH and never INCONCLUSIVE — it is not in adjacency at all, so no
  // search ran and none could reach a boundary. Checked over all 1,500 edges by the test above.
  if (isSelfLoop(r)) return "SELF_LOOP";
  if (cycle.outcome === "MATCH") return "MATCH";
  if (cycle.outcome === "INCONCLUSIVE") return "INCONCLUSIVE";
  return "CLOSED_SEARCH";
}

/** The one-line headline for each basis. Each states what the graph DID, never what is true. */
export const BASIS_HEADLINE: Record<Basis, string> = {
  MATCH: "A directed cycle was re-derived from the rows.",
  INCONCLUSIVE: "The search ran off the edge of the extract. It could not determine.",
  SELF_LOOP: "The account paid itself. No search was possible.",
  CLOSED_SEARCH: "The search ran, closed inside the extract, and found no return path.",
};

/** The full reading. Deliberately says what was and was not EXPLORED — that is the whole
 *  correction: 447 of the 463 CONCLUSIVE_NO rows are self-loops where nothing was explored, and
 *  only 16 are regions that were actually traversed and closed. */
export const BASIS_DETAIL: Record<Basis, string> = {
  MATCH:
    "The subject closes a directed cycle back to its source account. Corroborated by structure — " +
    "and structure is the only thing permitted to corroborate. 57 edges of the 1,500.",
  INCONCLUSIVE:
    "The traversal reached an account whose outgoing edges are not in this 1,500-edge extract, so " +
    "the region is not closed and no negative can be honest. 980 edges — 65.3% of the extract. " +
    "The belief approved every one of them.",
  SELF_LOOP:
    "The subject's source and destination are the SAME account, which is not a transfer between " +
    "two accounts — so it is excluded from the graph's adjacency by construction and NO SEARCH " +
    "EVER RAN. 447 edges. These are not a region that was explored and found empty.",
  CLOSED_SEARCH:
    "A real search: the traversal explored the reachable region, closed inside the extract without " +
    "touching a boundary account, and found no return path. Only 16 edges of the 1,500 — these " +
    "are the ONLY ones of which 'searched; there is no cycle' was ever true.",
};

/** The census, counted from the cluster and asserted in the backend suite (57/980/447/16). */
export const BASIS_COUNT: Record<Basis, number> = {
  MATCH: 57,
  INCONCLUSIVE: 980,
  SELF_LOOP: 447,
  CLOSED_SEARCH: 16,
};

export const BASIS_LABEL: Record<Basis, string> = {
  MATCH: "MATCH",
  INCONCLUSIVE: "INCONCLUSIVE",
  SELF_LOOP: "CONCLUSIVE_NO · self-loop",
  CLOSED_SEARCH: "CONCLUSIVE_NO · closed search",
};
