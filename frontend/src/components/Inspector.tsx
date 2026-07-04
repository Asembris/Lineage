/*
 * Inspector — the right region. With no selection (interactions are Phase 3), it
 * shows a real default: the belief catalog + a fleet summary.
 *
 * STAGE A STUB: proves the region wires up and the belief data arrives. The real
 * catalog + fleet summary land in Stage C.
 */

import type { BeliefsData } from "../hooks/useConsoleData";

export function Inspector({ data }: { data: BeliefsData }) {
  return (
    <p className="panel__note">
      Stage A: {data.count} belief(s) loaded. Catalog + fleet summary render in Stage C.
    </p>
  );
}
