/*
 * treeLayout — pure, React-free genealogy layout. Turns the flat AgentGenealogy[]
 * from GET /agents into positioned nodes + edges for a static SVG.
 *
 * Axes: generation runs left→right (gen 0 = founding ancestor at the left). Each
 * bloodline is a horizontal BAND; within a band the main line sits on lane 0 and
 * offshoots (siblings/branches) drop to lane 1+. Lane assignment is data-driven —
 * the "main child" is the one with the most descendants, so the spine that reaches
 * the newest generation stays on lane 0 and childless offshoots fall below. No
 * agent names are invented: the model has none, so identity is generation + a real
 * UUID fragment.
 */

import type { AgentGenealogy } from "../api/types";

export interface TreeNode {
  agent: AgentGenealogy;
  x: number;
  y: number;
  lane: number;
  bandIndex: number;
  isAlive: boolean;
  isSibling: boolean; // sits off the main line (lane > 0)
  idFrag: string; // real: first 6 chars of the UUID
}

export interface TreeEdge {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  isBranch: boolean; // parent → offshoot (changes lane), vs a straight spine step
}

export interface TreeBand {
  bloodline: string;
  labelY: number;
  aliveCount: number;
  total: number;
}

export interface TreeLayout {
  width: number;
  height: number;
  nodes: TreeNode[];
  edges: TreeEdge[];
  bands: TreeBand[];
  generations: { g: number; x: number }[];
  axisY: number;
  labelX: number;
}

const COL_W = 98;
const MARGIN_X = 104; // room for the band label on the left
const MARGIN_RIGHT = 56;
const MARGIN_TOP = 76; // room for the per-generation GEN labels + band-rect top (design-port S4)
const LANE_H = 82;
const BAND_GAP = 58;
const AXIS_GAP = 46;

export function computeTreeLayout(agents: AgentGenealogy[]): TreeLayout {
  const byId = new Map(agents.map((a) => [a.id, a]));

  const children = new Map<string, AgentGenealogy[]>();
  for (const a of agents) {
    if (a.parent_id && byId.has(a.parent_id)) {
      const arr = children.get(a.parent_id) ?? [];
      arr.push(a);
      children.set(a.parent_id, arr);
    }
  }

  // Descendant count per node (memoized) — drives main-child selection.
  const descCount = new Map<string, number>();
  const countDesc = (id: string): number => {
    const cached = descCount.get(id);
    if (cached !== undefined) return cached;
    let n = 0;
    for (const k of children.get(id) ?? []) n += 1 + countDesc(k.id);
    descCount.set(id, n);
    return n;
  };
  for (const a of agents) countDesc(a.id);

  // Group into bloodline bands; most-alive bloodline on top, then alphabetical.
  const bandMembers = new Map<string, AgentGenealogy[]>();
  for (const a of agents) {
    const arr = bandMembers.get(a.bloodline) ?? [];
    arr.push(a);
    bandMembers.set(a.bloodline, arr);
  }
  const aliveIn = (bl: string) =>
    bandMembers.get(bl)!.filter((a) => a.status === "alive").length;
  const bloodlines = [...bandMembers.keys()].sort((b1, b2) => {
    const d = aliveIn(b2) - aliveIn(b1);
    return d !== 0 ? d : b1.localeCompare(b2);
  });

  const maxGen = agents.reduce((m, a) => Math.max(m, a.generation), 0);
  const genX = (g: number) => MARGIN_X + g * COL_W;

  const nodePos = new Map<string, { x: number; y: number; lane: number }>();
  const nodes: TreeNode[] = [];
  const bands: TreeBand[] = [];

  let bandTop = MARGIN_TOP;
  bloodlines.forEach((bl, bandIndex) => {
    const members = bandMembers.get(bl)!;
    // A root within the band is one whose parent is not in this band.
    const roots = members.filter((a) => {
      if (!a.parent_id) return true;
      const p = byId.get(a.parent_id);
      return !p || p.bloodline !== bl;
    });

    // lane -> generation columns already taken, so offshoots never overlap.
    const laneCols = new Map<number, Set<number>>();
    const claimLane = (minLane: number, gen: number): number => {
      let lane = minLane;
      for (;;) {
        const used = laneCols.get(lane);
        if (!used || !used.has(gen)) {
          (used ?? laneCols.set(lane, new Set()).get(lane)!).add(gen);
          return lane;
        }
        lane += 1;
      }
    };

    const assign = (node: AgentGenealogy, lane: number) => {
      nodePos.set(node.id, { x: genX(node.generation), y: 0, lane });
      const kids = (children.get(node.id) ?? [])
        .filter((k) => k.bloodline === bl)
        .sort((k1, k2) => {
          const d = countDesc(k2.id) - countDesc(k1.id);
          if (d !== 0) return d;
          if (k1.generation !== k2.generation) return k1.generation - k2.generation;
          return k1.id.localeCompare(k2.id);
        });
      kids.forEach((k, i) => {
        // main child keeps the line; the rest drop to the first free lane below.
        const kl = i === 0 ? lane : claimLane(1, k.generation);
        assign(k, kl);
      });
    };
    roots
      .sort((a, b) => a.generation - b.generation || a.id.localeCompare(b.id))
      .forEach((r) => {
        claimLane(0, r.generation);
        assign(r, 0);
      });

    const laneCount = Math.max(0, ...laneCols.keys()) + 1;

    // Finalize y for this band and emit nodes.
    for (const a of members) {
      const pos = nodePos.get(a.id)!;
      pos.y = bandTop + pos.lane * LANE_H;
      nodes.push({
        agent: a,
        x: pos.x,
        y: pos.y,
        lane: pos.lane,
        bandIndex,
        isAlive: a.status === "alive",
        isSibling: pos.lane > 0,
        idFrag: a.id.slice(0, 6),
      });
    }
    bands.push({
      bloodline: bl,
      labelY: bandTop, // lane-0 row (the main line) — label aligns to it
      aliveCount: aliveIn(bl),
      total: members.length,
    });

    bandTop += laneCount * LANE_H + BAND_GAP;
  });

  // Edges: every real parent→child link (provenance stays honest).
  const edges: TreeEdge[] = [];
  for (const a of agents) {
    if (!a.parent_id) continue;
    const child = nodePos.get(a.id);
    const parent = nodePos.get(a.parent_id);
    if (!child || !parent) continue;
    edges.push({
      key: `${a.parent_id}->${a.id}`,
      x1: parent.x,
      y1: parent.y,
      x2: child.x,
      y2: child.y,
      isBranch: parent.lane !== child.lane,
    });
  }

  const axisY = bandTop - BAND_GAP + AXIS_GAP;
  const height = axisY + 22;
  const width = genX(maxGen) + MARGIN_RIGHT;
  const generations = Array.from({ length: maxGen + 1 }, (_, g) => ({ g, x: genX(g) }));

  return { width, height, nodes, edges, bands, generations, axisY, labelX: 16 };
}
