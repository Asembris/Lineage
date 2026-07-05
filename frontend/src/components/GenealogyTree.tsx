/*
 * GenealogyTree — the center region. A static SVG of the agent genealogy from
 * GET /agents: generation across, bloodlines banded, main line on lane 0 with
 * offshoots below. Cold by default; the only always-on signal color is --alive on
 * the (few) living agents, so a living belief-holder reads at a glance even off
 * the main line.
 *
 * The one place warmth appears is the Trace overlay (Frontend Phase 3): when a
 * `trace` chain is passed, TraceOverlay animates warmth backward along that real
 * inheritance chain to the igniting origin. Geometry (reversed edge paths + node
 * positions) is derived here from the layout; playback lives in TraceOverlay.
 */

import { useMemo } from "react";
import type { AgentsData } from "../hooks/useConsoleData";
import type { UUID } from "../api/types";
import { computeTreeLayout, type TreeEdge, type TreeLayout } from "../lib/treeLayout";
import { TraceOverlay, type TraceGeo } from "./TraceOverlay";
import { InvalidateOverlay, type InvalGeo } from "./InvalidateOverlay";
import "./GenealogyTree.css";

export interface TreeTrace {
  chain: UUID[]; // ordered leaf (investigated agent) → origin
  playToken: number;
  onComplete: () => void;
}

/** The living holders of the belief being invalidated + the phase to render them in.
 *  livingHolders are the closure's alive agents (the fork: > 1 — the reveal). */
export interface TreeInvalidation {
  livingHolders: UUID[];
  phase: "armed" | "corrected";
  playToken: number;
}

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

/** Reverse an edge so it draws child→parent (the backward trace direction). */
function reversedEdgePath(e: TreeEdge): string {
  if (!e.isBranch) {
    return `M ${e.x2} ${e.y2} L ${e.x1} ${e.y1}`;
  }
  const dx = e.x2 - e.x1;
  const c1 = e.x1 + dx * 0.5;
  const c2 = e.x2 - dx * 0.4;
  return `M ${e.x2} ${e.y2} C ${c2} ${e.y2}, ${c1} ${e.y1}, ${e.x1} ${e.y1}`;
}

/** Map a real inheritance chain onto tree geometry (edges + node positions). */
function computeTraceGeo(layout: TreeLayout, chain: UUID[]): TraceGeo {
  const nodeById = new Map(layout.nodes.map((n) => [n.agent.id, n]));
  const edgeByKey = new Map(layout.edges.map((e) => [e.key, e]));

  const edges: TraceGeo["edges"] = [];
  for (let i = 0; i < chain.length - 1; i++) {
    const child = chain[i];
    const parent = chain[i + 1];
    const edge = edgeByKey.get(`${parent}->${child}`);
    if (!edge) continue; // no such genealogy edge — skip honestly
    edges.push({ key: edge.key, d: reversedEdgePath(edge), index: i });
  }

  const nodes: TraceGeo["nodes"] = [];
  chain.forEach((id, index) => {
    const n = nodeById.get(id);
    if (!n) return;
    nodes.push({
      id,
      x: n.x,
      y: n.y,
      gen: n.agent.generation,
      index,
      isOrigin: index === chain.length - 1,
    });
  });

  return { edges, nodes };
}

function computeInvalGeo(layout: TreeLayout, livingHolders: UUID[]): InvalGeo {
  const nodeById = new Map(layout.nodes.map((n) => [n.agent.id, n]));
  const holders: InvalGeo["holders"] = [];
  for (const id of livingHolders) {
    const n = nodeById.get(id);
    if (!n) continue; // holder not in the rendered layout — skip honestly
    holders.push({ id, x: n.x, y: n.y, gen: n.agent.generation });
  }
  return { holders };
}

export function GenealogyTree({
  data,
  trace,
  invalidation,
}: {
  data: AgentsData;
  trace?: TreeTrace | null;
  invalidation?: TreeInvalidation | null;
}) {
  const layout = useMemo(() => computeTreeLayout(data.agents), [data.agents]);
  const alive = layout.nodes.filter((n) => n.isAlive).length;
  const traceGeo = useMemo(
    () => (trace ? computeTraceGeo(layout, trace.chain) : null),
    [layout, trace],
  );
  const invalGeo = useMemo(
    () => (invalidation ? computeInvalGeo(layout, invalidation.livingHolders) : null),
    [layout, invalidation],
  );

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

        {/* trace overlay — warmth on top of the cold tree, only when tracing */}
        {trace && traceGeo && (
          <TraceOverlay geo={traceGeo} playToken={trace.playToken} onComplete={trace.onComplete} />
        )}

        {/* invalidate overlay — both living holders marked (--alert) then corrected
            (--alive) together at one commit; the closure reveal + atomic correction */}
        {invalidation && invalGeo && (
          <InvalidateOverlay geo={invalGeo} phase={invalidation.phase} playToken={invalidation.playToken} />
        )}

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
