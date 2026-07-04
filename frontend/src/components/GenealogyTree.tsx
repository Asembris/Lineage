/*
 * GenealogyTree — the center region. Renders the agent genealogy as a static SVG:
 * generation across, bloodlines banded, dead-vs-alive visually distinct.
 *
 * STAGE A STUB: proves the region wires up and the agent data arrives. The real
 * SVG layout (src/lib/treeLayout.ts + this component) lands in Stage B.
 */

import type { AgentsData } from "../hooks/useConsoleData";

export function GenealogyTree({ data }: { data: AgentsData }) {
  const alive = data.agents.filter((a) => a.status === "alive").length;
  return (
    <p className="panel__note">
      Stage A: {data.count} agents loaded, {alive} alive. SVG tree renders in Stage B.
    </p>
  );
}
