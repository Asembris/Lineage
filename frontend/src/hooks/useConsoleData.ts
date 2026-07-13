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
 *
 * AND THE FILTER ALONE WAS NOT ENOUGH: THE FEED REACHED 200 OF 1,500.
 *
 * `kind=aml` narrowed the feed correctly and then stopped at ONE PAGE — the backend's max, 200 —
 * with no offset paging. So 1,300 of the 1,500 AML decisions, and with them 1,300 of the real
 * money-flow transactions they cite 1:1, were unreachable by any user action. Among them **50 of
 * the 57 CYCLE rings**: the AML console's signature exhibit was invisible in 88% of its instances.
 * That is the same shape as the bug above — a surface that looks complete and silently is not —
 * and it is why pagination is load-bearing for the witness geometry, not hygiene.
 *
 * OFFSET PAGING IS ONLY SAFE BECAUSE THE SORT IS TOTAL, and that was CHECKED, not assumed. All
 * 1,500 AML rows share ONE `decided_at` by design; `ORDER BY decided_at DESC` alone would be a
 * non-total order, and LIMIT/OFFSET over it silently duplicates and skips rows — pagination that
 * looks fine and loses transactions. `catalog.py` orders by `decided_at DESC, id DESC`, and `id`
 * is unique, so every page boundary is deterministic.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, countDecisions, listAgents, listBeliefs, listDecisions } from "../api/client";
import type {
  AgentGenealogy,
  Belief,
  Decision,
  DecisionKind,
  WitnessOutcome,
} from "../api/types";

/** The backend's max page (`Query(le=200)`). Asking for more is a 422, not a bigger page. */
export const PAGE_SIZE = 200;

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

/** The seam's census, COUNTED from the cluster (never retyped — this number has been corrupted by
 *  prose three times). Each is a `total` at `limit=1` through `?witness_outcome=`. It is the BASIS
 *  of an AML decision: two of these three map to `approve`, and the verdict alone cannot tell them
 *  apart. `INCONCLUSIVE` is 65.3% of the extract and means "we could not determine". */
export interface WitnessCounts {
  MATCH: number;
  CONCLUSIVE_NO: number;
  INCONCLUSIVE: number;
}

export interface ConsoleData {
  agents: Loadable<AgentsData>;
  decisions: Loadable<DecisionsData>;
  beliefs: Loadable<BeliefsData>;
  counts: Loadable<KindCounts>;
  witnessCounts: Loadable<WitnessCounts>;
  /** Append the next page. No-op when everything matching the filter is already loaded. */
  loadMore: () => void;
  loadingMore: boolean;
}

const LOADING = { status: "loading" } as const;

function toError(err: unknown): { status: "error"; message: string; code: number } {
  const code = err instanceof ApiError ? err.status : -1;
  const message = err instanceof Error ? err.message : String(err);
  return { status: "error", message, code };
}

/** `kind = null` is the unfiltered fleet-wide feed. `witness = null` is every basis.
 *
 *  The witness filter is only meaningful for AML decisions (a card decision has no witness), so a
 *  caller should only pass one under `kind === "aml"`. Passing a bad value is a 422 from the
 *  backend, never a silent empty page. */
export function useConsoleData(
  kind: DecisionKind | null,
  witness: WitnessOutcome | null = null,
): ConsoleData {
  const [agents, setAgents] = useState<Loadable<AgentsData>>(LOADING);
  const [decisions, setDecisions] = useState<Loadable<DecisionsData>>(LOADING);
  const [beliefs, setBeliefs] = useState<Loadable<BeliefsData>>(LOADING);
  const [counts, setCounts] = useState<Loadable<KindCounts>>(LOADING);
  const [witnessCounts, setWitnessCounts] = useState<Loadable<WitnessCounts>>(LOADING);
  const [loadingMore, setLoadingMore] = useState(false);

  // The feed re-fetches from page 0 whenever a filter changes; the genealogy, the belief catalog
  // and the two censuses do not depend on the filter and are fetched once.
  useEffect(() => {
    let cancelled = false;
    setDecisions(LOADING);

    listDecisions({
      limit: PAGE_SIZE,
      offset: 0,
      kind: kind ?? undefined,
      witnessOutcome: witness ?? undefined,
    })
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
  }, [kind, witness]);

  /** Append the next page. Safe under offset paging ONLY because `catalog.py` orders by
   *  `decided_at DESC, id DESC` — a TOTAL order. All 1,500 AML rows share one `decided_at`, so
   *  without the `id` tiebreak every page boundary would be arbitrary and this would silently
   *  duplicate and drop rows. Checked against the query, not assumed. */
  const loadMore = useCallback(() => {
    if (decisions.status !== "ready" || loadingMore) return;
    const loaded = decisions.data.decisions.length;
    if (loaded >= decisions.data.total) return;

    setLoadingMore(true);
    listDecisions({
      limit: PAGE_SIZE,
      offset: loaded,
      kind: kind ?? undefined,
      witnessOutcome: witness ?? undefined,
    })
      .then((res) => {
        setDecisions((cur) =>
          cur.status === "ready"
            ? {
                status: "ready",
                // `total` comes from the SAME response as the rows, so the denominator can never
                // describe a different world than the page it arrived with.
                data: {
                  decisions: [...cur.data.decisions, ...res.decisions],
                  total: res.total,
                },
              }
            : cur,
        );
      })
      .catch((err: unknown) => setDecisions(toError(err)))
      .finally(() => setLoadingMore(false));
  }, [decisions, loadingMore, kind, witness]);

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

    // The seam's census — the BASIS of each AML decision — likewise counted, never quoted. The
    // honesty ledger already reads it this way for the same reason: a number read from the cluster
    // cannot be wrong about the cluster, and this particular number has been corrupted by prose
    // three times.
    Promise.all([
      countDecisions({ kind: "aml", witnessOutcome: "MATCH" }),
      countDecisions({ kind: "aml", witnessOutcome: "CONCLUSIVE_NO" }),
      countDecisions({ kind: "aml", witnessOutcome: "INCONCLUSIVE" }),
    ])
      .then(([MATCH, CONCLUSIVE_NO, INCONCLUSIVE]) => {
        if (!cancelled) {
          setWitnessCounts({
            status: "ready",
            data: { MATCH, CONCLUSIVE_NO, INCONCLUSIVE },
          });
        }
      })
      .catch((err: unknown) => !cancelled && setWitnessCounts(toError(err)));

    return () => {
      cancelled = true;
    };
  }, []);

  return { agents, decisions, beliefs, counts, witnessCounts, loadMore, loadingMore };
}
