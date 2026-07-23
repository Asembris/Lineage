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

import { useMemo, useState } from "react";
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

  // Hover ancestry-preview (design-port S4). Hovering a node dims everything OFF its real ancestry
  // chain and warms the chain to --bone — a cold, momentary "where did this line come from". The
  // chain is walked over REAL parent_id edges (data.agents); no relationship is invented.
  const [hoverNode, setHoverNode] = useState<UUID | null>(null);
  const parentOf = useMemo(() => {
    const m = new Map<UUID, UUID>();
    for (const a of data.agents) if (a.parent_id) m.set(a.id, a.parent_id);
    return m;
  }, [data.agents]);
  const ancestry = useMemo(() => {
    if (!hoverNode) return new Set<UUID>();
    const out = new Set<UUID>();
    let cur: UUID | undefined = hoverNode;
    let guard = 0;
    while (cur && guard++ < 64) {
      out.add(cur);
      cur = parentOf.get(cur);
    }
    return out;
  }, [hoverNode, parentOf]);
  const hovering = hoverNode !== null;
  const chainEdges = useMemo(() => {
    const s = new Set<string>();
    ancestry.forEach((id) => {
      const p = parentOf.get(id);
      if (p) s.add(`${p}->${id}`);
    });
    return s;
  }, [ancestry, parentOf]);
  const dimOf = (id: UUID) => (hovering && !ancestry.has(id) ? 0.16 : 1);

  // Band boxes + GEN gridline extents, derived from the real layout (not the DC's hardcoded
  // 86..606 / GEN 0-7). Bands are cold decorative rects; the GEN axis rides the real generations.
  const bandBoxes = useMemo(
    () =>
      layout.bands.map((b, i) => {
        const ns = layout.nodes.filter((n) => n.bandIndex === i);
        const xs = ns.map((n) => n.x);
        const ys = ns.map((n) => n.y);
        return {
          bloodline: b.bloodline,
          x0: Math.min(...xs) - 40,
          x1: Math.max(...xs) + 40,
          y0: Math.min(...ys) - 36,
          y1: Math.max(...ys) + 34,
        };
      }),
    [layout],
  );
  const gridTop = Math.min(...bandBoxes.map((b) => b.y0));
  const gridBot = Math.max(...bandBoxes.map((b) => b.y1));

  return (
    <div className="tree">
      <svg
        className="tree__svg"
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`Agent genealogy: ${layout.bands.length} bloodlines, ${data.count} agents, ${alive} alive.`}
      >
        <defs>
          {/* soft glow for the living-node halo */}
          <filter id="tree-halo" x="-120%" y="-120%" width="340%" height="340%">
            <feGaussianBlur stdDeviation="4" />
          </filter>
        </defs>

        {/* bloodline bands — a faint --surface plate per bloodline, label top-left inside it */}
        <g>
          {bandBoxes.map((b) => (
            <g key={b.bloodline}>
              <rect
                className="tree__band"
                x={b.x0}
                y={b.y0}
                width={b.x1 - b.x0}
                height={b.y1 - b.y0}
                rx={12}
              />
              <text className="tree__band-label" x={b.x0 + 14} y={b.y0 + 22}>
                {b.bloodline.toUpperCase()}
              </text>
            </g>
          ))}
        </g>

        {/* GEN gridlines — one vertical rule + top label per real generation */}
        <g>
          {layout.generations.map((g) => (
            <g key={g.g}>
              <line
                className="tree__grid"
                x1={g.x}
                y1={gridTop - 6}
                x2={g.x}
                y2={gridBot + 6}
              />
              <text className="tree__grid-label" x={g.x} y={gridTop - 18} textAnchor="middle">
                GEN {g.g}
              </text>
            </g>
          ))}
        </g>

        {/* edges under nodes — dimmed off the hovered ancestry, warmed to --bone on it */}
        <g>
          {layout.edges.map((e) => {
            const [pa, ch] = e.key.split("->") as [UUID, UUID];
            const inChain = chainEdges.has(e.key);
            return (
              <path
                key={e.key}
                d={edgePath(e)}
                className={`tree__edge${e.isBranch ? " tree__edge--branch" : ""}${
                  inChain ? " tree__edge--chain" : ""
                }`}
                opacity={Math.min(dimOf(pa), dimOf(ch))}
              />
            );
          })}
        </g>

        {/* nodes */}
        <g>
          {layout.nodes.map((n) => {
            const id = n.agent.id;
            const inChain = ancestry.has(id);
            const isHover = hoverNode === id;
            const cls =
              "tree__node " +
              (n.isAlive ? "tree__node--alive" : "tree__node--dead") +
              (inChain ? " tree__node--inchain" : "") +
              (isHover ? " tree__node--hover" : "");
            return (
              <g
                key={id}
                className={cls}
                transform={`translate(${n.x} ${n.y})`}
                opacity={dimOf(id)}
                onMouseEnter={() => setHoverNode(id)}
                onMouseLeave={() => setHoverNode(null)}
              >
                {/* transparent hit target — a generous hover radius so the preview is easy to trigger */}
                <circle className="tree__hit" r={26} />
                {n.isAlive && <circle className="tree__halo" r={22} filter="url(#tree-halo)" />}
                <circle className="tree__dot" r={13} />
                <text className="tree__gen" dominantBaseline="central">
                  {n.agent.generation}
                </text>
                <text className="tree__id" y={30} dominantBaseline="hanging">
                  {n.idFrag}
                </text>
              </g>
            );
          })}
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
      </svg>

      <div className="tree__legend" aria-hidden="true">
        <span className="tree__legend-item">
          <span className="tree__key tree__key--alive" /> living
        </span>
        <span className="tree__legend-item">
          <span className="tree__key tree__key--dead" /> dead ancestor
        </span>
        <span className="tree__legend-item">
          <span className="tree__key tree__key--branch" /> branch
        </span>
        <span className="tree__legend-hint">hover a node → ancestry preview</span>
      </div>
    </div>
  );
}
