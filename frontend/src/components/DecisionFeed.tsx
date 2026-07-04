/*
 * DecisionFeed — the left region. The fleet-wide decision stream, newest first.
 *
 * STAGE A STUB: proves the region wires up and the (possibly empty) decision data
 * arrives. The real feed rows land in Stage C.
 */

import type { DecisionsData } from "../hooks/useConsoleData";

export function DecisionFeed({ data }: { data: DecisionsData }) {
  if (data.total === 0) {
    return (
      <p className="panel__note">
        No decisions on the cluster yet. Rerun the backfill to populate the feed.
      </p>
    );
  }
  return (
    <p className="panel__note">
      Stage A: {data.decisions.length} of {data.total} decisions loaded. Feed rows
      render in Stage C.
    </p>
  );
}
