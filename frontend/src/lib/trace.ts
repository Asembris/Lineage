/*
 * trace.ts — pure derivation for the Trace interaction.
 *
 * GET /beliefs/{id}/lineage returns the belief's ENTIRE inheritance closure (a
 * tree that can fork — a belief inherited by more than one child). Trace, though,
 * animates the single ancestral chain from the ONE investigated agent back to the
 * origin ancestor. deriveChain walks the real `from_agent_id` pointers up that
 * closure to extract exactly that chain — every hop is a real belief_inheritance
 * edge, no order is guessed. (The fork / full-closure reveal is reserved for the
 * Invalidate step, where two living holders correcting atomically lands harder.)
 *
 * React-free and side-effect-free so it is trivially testable.
 */

import type { LineageResponse, UUID } from "../api/types";

/**
 * Ordered chain of agent ids from the investigated agent (index 0) back to the
 * origin ancestor (last index). Returns [] if the agent isn't a holder in the
 * lineage (the caller renders an honest empty-chain fallback rather than crashing).
 * A single-element chain means the investigated agent IS the origin (formed-here).
 */
export function deriveChain(lineage: LineageResponse, agentId: UUID): UUID[] {
  const byId = new Map(lineage.path.map((n) => [n.agent_id, n]));
  let node = byId.get(agentId);
  if (!node) return [];

  const chain: UUID[] = [agentId];
  const seen = new Set<UUID>([agentId]);
  while (node.from_agent_id) {
    const next = node.from_agent_id;
    if (seen.has(next)) break; // cycle guard — provenance should never loop
    chain.push(next);
    seen.add(next);
    const parent = byId.get(next);
    if (!parent) break; // dangling from_agent_id — stop honestly where data ends
    node = parent;
  }
  return chain;
}
