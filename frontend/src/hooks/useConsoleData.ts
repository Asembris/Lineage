/*
 * useConsoleData — the console's single data entry point.
 *
 * Fires the three read endpoints the shell renders (GET /agents, /decisions,
 * /beliefs) in PARALLEL and exposes each as an independent Loadable slot, so one
 * slow or failed source degrades locally (that panel shows its own error) instead
 * of blanking the whole console. Phase 2 is read-only; nothing here mutates.
 */

import { useEffect, useState } from "react";
import { ApiError, listAgents, listBeliefs, listDecisions } from "../api/client";
import type { AgentGenealogy, Belief, Decision } from "../api/types";

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
  total: number;
}
export interface BeliefsData {
  beliefs: Belief[];
  count: number;
}

export interface ConsoleData {
  agents: Loadable<AgentsData>;
  decisions: Loadable<DecisionsData>;
  beliefs: Loadable<BeliefsData>;
}

const LOADING = { status: "loading" } as const;

function toError(err: unknown): { status: "error"; message: string; code: number } {
  const code = err instanceof ApiError ? err.status : -1;
  const message = err instanceof Error ? err.message : String(err);
  return { status: "error", message, code };
}

export function useConsoleData(): ConsoleData {
  const [agents, setAgents] = useState<Loadable<AgentsData>>(LOADING);
  const [decisions, setDecisions] = useState<Loadable<DecisionsData>>(LOADING);
  const [beliefs, setBeliefs] = useState<Loadable<BeliefsData>>(LOADING);

  useEffect(() => {
    let cancelled = false;

    // Fleet-wide feed, newest first (backend default order); a generous page so
    // the feed reads as a real stream. No agent filter — that's the Phase-3 drill-in.
    listDecisions({ limit: 200 })
      .then((res) => {
        if (!cancelled) {
          setDecisions({
            status: "ready",
            data: { decisions: res.decisions, total: res.total },
          });
        }
      })
      .catch((err: unknown) => !cancelled && setDecisions(toError(err)));

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

    return () => {
      cancelled = true;
    };
  }, []);

  return { agents, decisions, beliefs };
}
