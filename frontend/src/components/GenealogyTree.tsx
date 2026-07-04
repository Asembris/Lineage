/*
 * GenealogyTree — the center region. A static SVG of the agent genealogy from
 * GET /agents: generation across, bloodlines banded, main line on lane 0 with
 * offshoots below. Cold by default (no warmth — that's the Phase-3 trace); the
 * only signal color is --alive on the (few) living agents, so a living
 * belief-holder reads at a glance even when it sits off the main line.
 *
 * Presentational only — no interactions this phase.
 */

import { useMemo } from "react";
import type { AgentsData } from "../hooks/useConsoleData";
import { computeTreeLayout, type TreeEdge } from "../lib/treeLayout";
import "./GenealogyTree.css";

function edgePath(e: TreeEdge): string {
  if (!e.isBranch) {
    // straight spine step
    return `M ${e.x1} ${e.y1} L ${e.x2} ${e.y2}`;
  }
  // smooth offshoot from the main line down to the sibling lane
  const dx = e.x2 - e.x1;
  const c1 = e.x1 + dx * 0.5;
  const c2 = e.x2 - dx * 0.4;
  return `M ${e.x1} ${e.y1} C ${c1} ${e.y1}, ${c2} ${e.y2}, ${e.x2} ${e.y2}`;
}

export function GenealogyTree({ data }: { data: AgentsData }) {
  const layout = useMemo(() => computeTreeLayout(data.agents), [data.agents]);
  const alive = layout.nodes.filter((n) => n.isAlive).length;

  return (
    <div className="tree">
      <svg
        className="tree__svg"
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`Agent genealogy: ${layout.bands.length} bloodlines, ${data.count} agents, ${alive} alive.`}
      >
        {/* edges under nodes */}
        <g>
          {layout.edges.map((e) => (
            <path
              key={e.key}
              d={edgePath(e)}
              className={`tree__edge${e.isBranch ? " tree__edge--branch" : ""}`}
            />
          ))}
        </g>

        {/* band labels + a faint main-line rule per bloodline */}
        <g>
          {layout.bands.map((b) => (
            <text
              key={b.bloodline}
              x={layout.labelX}
              y={b.labelY}
              className="tree__band-label"
              dominantBaseline="middle"
            >
              {b.bloodline.toUpperCase()}
            </text>
          ))}
        </g>

        {/* nodes */}
        <g>
          {layout.nodes.map((n) => (
            <g
              key={n.agent.id}
              className={n.isAlive ? "tree__node tree__node--alive" : "tree__node tree__node--dead"}
              transform={`translate(${n.x} ${n.y})`}
            >
              {n.isAlive && <circle className="tree__halo" r={18} />}
              <circle className="tree__dot" r={13} />
              <text className="tree__gen" dominantBaseline="central">
                {n.agent.generation}
              </text>
              <text className="tree__id" y={30} dominantBaseline="hanging">
                {n.idFrag}
              </text>
            </g>
          ))}
        </g>

        {/* generation axis */}
        <g>
          <line
            className="tree__axis-rule"
            x1={layout.labelX}
            y1={layout.axisY - 20}
            x2={layout.width - 24}
            y2={layout.axisY - 20}
          />
          <text
            x={layout.labelX}
            y={layout.axisY}
            className="tree__axis-caption"
            dominantBaseline="middle"
          >
            GEN
          </text>
          {layout.generations.map((g) => (
            <text
              key={g.g}
              x={g.x}
              y={layout.axisY}
              className="tree__axis-label"
              textAnchor="middle"
              dominantBaseline="middle"
            >
              {g.g}
            </text>
          ))}
        </g>
      </svg>

      <div className="tree__legend" aria-hidden="true">
        <span className="tree__legend-item">
          <span className="tree__key tree__key--alive" /> alive
        </span>
        <span className="tree__legend-item">
          <span className="tree__key tree__key--dead" /> dead
        </span>
        <span className="tree__legend-item">
          <span className="tree__key tree__key--branch" /> branch
        </span>
      </div>
    </div>
  );
}
