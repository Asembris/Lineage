/*
 * useConsoleData — the console's single data entry point.
 *
 * Fires the read endpoints the shell renders (GET /agents, /decisions, /beliefs) in PARALLEL and
 * exposes each as an independent Loadable slot, so one slow or failed source degrades locally
 * (that panel shows its own error) instead of blanking the whole console. Read-only; nothing here
 * mutates.
 *
 * THE FEED IS FILTERED BY `kind`, AND THAT IS NOT COSMETIC — IT IS A NAVIGATION-HONESTY FIX.
 *
 * The grounding seam gives every AML decision a SINGLE FIXED `decided_at` (2026-07-12T12:00Z), on
 * purpose: with every decision at one instant there are no time windows to draw a curve from, so
 * the base-rate mirage is UNREPRESENTABLE rather than merely discouraged (NOTES → THE BASE-RATE
 * MIRAGE). That guard works. But the feed is ordered `decided_at DESC`, and that fixed timestamp is
 * newer than every card decision — so all 1,500 AML rows sort ABOVE all 4,000 card rows, and this
 * hook's single page of 200 was 200 AML rows. The crimson belief — the fork, the two living
 * holders, the whole measured 0.924 → 0.528 staleness curve — became UNREACHABLE from the console,
 * silently, and stayed that way for the entire seam arc.
 *
 * A guard's blast radius extends past the thing it guards. The filter is the fix, and it uses the
 * `kind` parameter the backend already serves (structural, per 0007's CHECK) — no new endpoint.
 */

import { useEffect, useState } from "react";
import { ApiError, countDecisions, listAgents, listBeliefs, listDecisions } from "../api/client";
import type { AgentGenealogy, Belief, Decision, DecisionKind } from "../api/types";

export type Loadable<T> =
  | { status: "loading" }
  | { status: "error"; message: string; code: number }
  | { status: "ready"; data: T };

export interface AgentsData {
  agents: AgentGenealogy[];
  count: number;
}
export interface DecisionsData {
  decisions: Decision[];
  /** Total matching the CURRENT filter (not the cluster total — see KindCounts). */
  total: number;
}
export interface BeliefsData {
  beliefs: Belief[];
  count: number;
}

/** Real cluster counts per kind, counted (never retyped) so the filter chips can state what exists
 *  even while you are looking at the other kind. `all` is the honest denominator: a feed showing
 *  200 AML rows with 4,000 card decisions off-screen must SAY so. */
export interface KindCounts {
  all: number;
  card: number;
  aml: number;
}

export interface ConsoleData {
  agents: Loadable<AgentsData>;
  decisions: Loadable<DecisionsData>;
  beliefs: Loadable<BeliefsData>;
  counts: Loadable<KindCounts>;
}

const LOADING = { status: "loading" } as const;

function toError(err: unknown): { status: "error"; message: string; code: number } {
  const code = err instanceof ApiError ? err.status : -1;
  const message = err instanceof Error ? err.message : String(err);
  return { status: "error", message, code };
}

/** `kind = null` is the unfiltered fleet-wide feed. */
export function useConsoleData(kind: DecisionKind | null): ConsoleData {
  const [agents, setAgents] = useState<Loadable<AgentsData>>(LOADING);
  const [decisions, setDecisions] = useState<Loadable<DecisionsData>>(LOADING);
  const [beliefs, setBeliefs] = useState<Loadable<BeliefsData>>(LOADING);
  const [counts, setCounts] = useState<Loadable<KindCounts>>(LOADING);

  // The feed re-fetches when the filter changes; the genealogy, the belief catalog and the
  // per-kind census do not depend on it and are fetched once.
  useEffect(() => {
    let cancelled = false;

    // Newest first (backend default order); a generous page so the feed reads as a real stream.
    listDecisions({ limit: 200, kind: kind ?? undefined })
      .then((res) => {
        if (!cancelled) {
          setDecisions({
            status: "ready",
            data: { decisions: res.decisions, total: res.total },
          });
        }
      })
      .catch((err: unknown) => !cancelled && setDecisions(toError(err)));

    return () => {
      cancelled = true;
    };
  }, [kind]);

  useEffect(() => {
    let cancelled = false;

    listAgents()
      .then((res) => {
        if (!cancelled) {
          setAgents({ status: "ready", data: { agents: res.agents, count: res.count } });
        }
      })
      .catch((err: unknown) => !cancelled && setAgents(toError(err)));

    listBeliefs()
      .then((res) => {
        if (!cancelled) {
          setBeliefs({ status: "ready", data: { beliefs: res.beliefs, count: res.count } });
        }
      })
      .catch((err: unknown) => !cancelled && setBeliefs(toError(err)));

    // Counted from the cluster, not retyped: a census this console states must be one it read.
    Promise.all([
      countDecisions(),
      countDecisions({ kind: "card" }),
      countDecisions({ kind: "aml" }),
    ])
      .then(([all, card, aml]) => {
        if (!cancelled) setCounts({ status: "ready", data: { all, card, aml } });
      })
      .catch((err: unknown) => !cancelled && setCounts(toError(err)));

    return () => {
      cancelled = true;
    };
  }, []);

  return { agents, decisions, beliefs, counts };
}
