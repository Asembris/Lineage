/*
 * witnessGeometry — the money-flow idiom, laid out. Pure, React-free, deterministic.
 *
 * Turns ONE witness of an `AmlInterrogationResponse` into positioned accounts (nodes) and
 * transactions (edges) for an SVG. Same payload in, same pixels out, always: no force simulation,
 * no randomness, no time. That is not a style preference — a layout that is not a pure function of
 * the payload cannot be guarded, and cannot have a stable `prefers-reduced-motion` final frame.
 *
 * This is NOT treeLayout. The genealogy is a generational lattice with a root and a known depth.
 * The money-flow graph has no root, no hierarchy, and no stable global layout — so nothing here is
 * borrowed from it beyond the convention that a layout module is pure.
 *
 * ============================================================================================
 *   THREE FACTS ABOUT THE PAYLOAD. EACH ONE WOULD HAVE PRODUCED A PLAUSIBLE, SILENTLY-WRONG
 *   PICTURE, AND EACH WAS MEASURED AGAINST THE LIVE EXTRACT — NOT REASONED ABOUT.
 * ============================================================================================
 *
 * 1. THE SUBJECT IS OFTEN ABSENT FROM ITS OWN WITNESS.
 *    CYCLE cites it in 57/57 and STACK in 35/35 — but GATHER-SCATTER omits it in 75 of 107, and
 *    SCATTER-GATHER in 1 of 42. Two different causes, both real:
 *      * GATHER-SCATTER truncates the hub's edges to MIN_FANOUT (`aml_graph.py`), so if the
 *        subject is not in the first two by id-sort it is dropped from its own witness.
 *      * SCATTER-GATHER cites `g.succ(u)[v]`, and `succ()` keys ONE edge per (src,dst) PAIR — so
 *        when the subject has a parallel twin, the witness cites the TWIN.
 *    CONSEQUENCE: `citesSubject` is a MEASURED property of each witness, never an assumption. A
 *    renderer must mark the subject only where it is genuinely cited. Marking `edges[0]` — or
 *    marking anything at all in the other 75 — would point the reader at a transaction they did
 *    not click, and nothing would fail.
 *
 * 2. THE MONEY-FLOW GRAPH IS A MULTIGRAPH.
 *    Two DISTINCT transactions can run between the same pair of accounts (41 witnesses contain
 *    such a pair: 27 GATHER-SCATTER, 14 STACK). So EDGE IDENTITY IS THE TRANSACTION ID, never the
 *    (from, to) pair. A layout that keys edges by their endpoints — the natural thing to write —
 *    draws three lines where the evidence has four, and the picture silently UNDERSTATES the
 *    evidence. Parallel edges are therefore counted and bowed apart, and `edgeCount === the
 *    witness's transaction_ids.length` is asserted in the browser guard, on every push.
 *
 * 3. A NAMED BOUNDARY ACCOUNT IS A NODE WITH NO LINE.
 *    An INCONCLUSIVE witness names the account where the search ran off the edge of the extract —
 *    but that account is an endpoint of NO transaction on the wire (in 730 witness-instances it is
 *    connected to nothing served). The searched region lives in `aml_evidence.neighbourhood()` and
 *    NO ROUTE SERVES IT. So the boundary is never drawn as connected geometry here. Drawing a line
 *    to it would be FABRICATION — inventing an edge to make a picture look finished.
 *
 * ============================================================================================
 *   COMPLETENESS: ONLY THE RING IS THE WHOLE STRUCTURE. EVERYTHING ELSE IS A PARTIAL CITATION.
 * ============================================================================================
 * Measured over all 1,500 edges, comparing each witness's own `detail` prose to the edge set it
 * actually serves:
 *
 *    CYCLE           57/57  FAITHFUL — the served edges ARE the whole cycle ("length 10" -> 10)
 *    SCATTER-GATHER  39/42  understate ("15 intermediaries scatter..." -> 2 are served)
 *    GATHER-SCATTER  107/107 understate ("gathers from 12 sources..." -> 2 are served)
 *    STACK           truncated too, but its prose claims no number ("two bipartite layers")
 *
 * The cause is `MIN_FANOUT` truncation in `aml_graph.py`. So a GATHER-SCATTER drawn from
 * `transaction_ids` alone renders a 2-in/2-out fan directly beside a sentence saying "gathers from
 * 12 sources" — a picture contradicting the prose next to it, with nothing failing anywhere. That
 * is this project's signature defect ("CONCLUSIVE_NO: 463 searched") committed in PIXELS.
 *
 * TWO THINGS FOLLOW, and they are the whole design:
 *
 *   a) `complete` is derived from the KIND, never from the prose. A RING is a closed cycle and is
 *      whole by definition; a LEGS/BUNDLE citation is bounded by MIN_FANOUT by construction. The
 *      client must NOT parse `detail` to reconcile the numbers — `lib/basis.ts` bans exactly that,
 *      with a test, for exactly this reason: a backend reword would silently corrupt the pixel.
 *
 *   b) The partial ones are LABELLED PARTIAL, IN THE PIXELS. The picture and the prose beside it
 *      will differ in magnitude. The honest fix is to SAY SO — never to make the picture lie in
 *      the direction of the sentence, and never to invent the missing edges.
 *
 * THE TRUNCATION IS A WIRE PROBLEM, NOT A RENDERING PROBLEM. It is fixed properly by serving the
 * searched region (`neighbourhood()`, gated as Rung 5) — and this is the strongest argument for
 * that endpoint the project has produced. Do not fix it here by inventing edges.
 *
 * EVIDENCE-ONLY. Nothing here imports, reaches, or could reach a `Decision`. Enforced by
 * `frontend/scripts/composition-guard.mjs` (this module is a declared EVIDENCE_MODULE), not by
 * this comment.
 */

import type { AmlAccount, AmlTransaction, AmlWitness, TraversalKind, UUID } from "../api/types";

/** An account: a NODE of the money-flow graph. `account` is resolved when the row was served. */
export interface GeoNode {
  id: UUID;
  x: number;
  y: number;
  bank: string;
  account: string;
  /** An endpoint of the subject transaction — true whether or not the witness cites the subject. */
  onSubject: boolean;
  /** Label offset from the node, and its text-anchor. On a RING the label is pushed RADIALLY
   *  OUTWARD: a label sitting above every node collides with the ring's own stroke on the left and
   *  right of the circle, which hides the edge it is meant to annotate. Found by looking at it. */
  labelDx: number;
  labelDy: number;
  labelAnchor: "start" | "middle" | "end";
}

/** A transaction: an EDGE. Identity is `id` — NEVER the (from, to) pair (fact 2 above). */
export interface GeoEdge {
  id: UUID;
  from: UUID;
  to: UUID;
  /** SVG path. Bowed when parallel twins share this edge's endpoints, so both stay countable. */
  d: string;
  /** Midpoint of the drawn path — where a marker or the direction arrow sits. */
  mx: number;
  my: number;
  /** THIS edge is the transaction the reader clicked. False for every edge when !citesSubject. */
  isSubject: boolean;
  /** SCATTER-GATHER only: which labelled leg this edge belongs to. Never an ORDER. */
  leg: "scatter" | "gather" | null;
  /** Draw order. For a RING this is the traversal; for LEGS/BUNDLE it is a stable index only. */
  step: number;
  amount: number;
  currency: string;
}

export interface WitnessGeometry {
  kind: TraversalKind;
  nodes: GeoNode[];
  edges: GeoEdge[];
  width: number;
  height: number;
  /** Does the witness actually cite the subject transaction? Measured, never assumed (fact 1). */
  citesSubject: boolean;
  /** Is the drawing the WHOLE structure, or a bounded citation of it? True only for a RING. */
  complete: boolean;
  /** The same evidence the drawing carries, as text — for screen readers, and for anyone who
   *  cannot see the SVG. A fact rendered only as pixels is a fact a screen reader cannot read. */
  description: string;
}

/* ------------------------------------------------------------------------------------------- */

// Sized against the REAL labels, not a guess: accounts are 9-character identifiers ("80F93F560"),
// so a 10-hop ring needs ~85px of arc per node to seat one without collision.
// Radial labels extend OUTWARD from the ring, so the padding must seat a 9-character mono label
// lying flat against the circle's left and right, not merely the label's height.
const R_PAD = 76;
const RING_MIN_R = 100;
const RING_STEP_R = 9; // a 10-hop ring needs more circumference than a 6-hop one
const COL_W = 150;
const ROW_H = 74;
const MARGIN = 44;
const BOW = 13; // how far parallel twins bow apart, so N transactions read as N lines

/** How far short of the account an edge stops.
 *
 *  THE DIRECTION OF THE MONEY IS A FACT, AND IT WAS INVISIBLE. Drawn centre-to-centre, the
 *  arrowhead's tip lands exactly on the node's centre and the node disc paints straight over it —
 *  so nine of a ring's ten hops showed no arrow at all and the flow had no readable direction.
 *  Found by looking at the render, not by reasoning about it. Edges now stop clear of the disc, so
 *  every arrowhead sits in open space. */
const NODE_GAP = 11;

/** The edges of a witness, resolved to rows. Ids the response did not resolve are dropped —
 *  they cannot be drawn, and inventing a placeholder node for one would be fabrication. */
function resolve(w: AmlWitness, txns: Record<UUID, AmlTransaction>): AmlTransaction[] {
  return w.transaction_ids.map((id) => txns[id]).filter((t): t is AmlTransaction => Boolean(t));
}

function legOf(w: AmlWitness, id: UUID): "scatter" | "gather" | null {
  if (!w.legs) return null;
  if (w.legs.scatter?.includes(id)) return "scatter";
  if (w.legs.gather?.includes(id)) return "gather";
  return null;
}

/**
 * Build the edge list, bowing parallel twins apart.
 *
 * FACT 2 LIVES HERE. Edges are grouped by their (from,to) pair ONLY to decide how far to bow each
 * one — never to merge them. Every transaction in `transaction_ids` produces exactly one GeoEdge,
 * and the browser guard asserts that count against the witness on every push.
 */
function drawEdges(
  rows: AmlTransaction[],
  w: AmlWitness,
  subjectId: UUID,
  pos: Map<UUID, { x: number; y: number }>,
): GeoEdge[] {
  const twins = new Map<string, number>();
  for (const t of rows) {
    const k = `${t.from_account_id}>${t.to_account_id}`;
    twins.set(k, (twins.get(k) ?? 0) + 1);
  }
  const seen = new Map<string, number>();

  return rows.map((t, step) => {
    const a = pos.get(t.from_account_id)!;
    const b = pos.get(t.to_account_id)!;
    const k = `${t.from_account_id}>${t.to_account_id}`;
    const n = twins.get(k) ?? 1;
    const i = seen.get(k) ?? 0;
    seen.set(k, i + 1);

    // Centre the fan of twins on the straight line: one edge is straight, two bow ±BOW/2, etc.
    const offset = n === 1 ? 0 : (i - (n - 1) / 2) * BOW * 2;

    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;

    // Stop clear of both account discs, so the arrowhead is not painted over by the node it points
    // at. Shortening only the target end would misplace the edge's own start on a dense ring.
    const ax = a.x + ux * NODE_GAP;
    const ay = a.y + uy * NODE_GAP;
    const bx = b.x - ux * NODE_GAP;
    const by = b.y - uy * NODE_GAP;

    // Perpendicular, so the bow is always across the edge regardless of its direction.
    const px = -uy * offset;
    const py = ux * offset;
    const cx = (ax + bx) / 2 + px;
    const cy = (ay + by) / 2 + py;

    return {
      id: t.id,
      from: t.from_account_id,
      to: t.to_account_id,
      d: offset === 0 ? `M ${ax} ${ay} L ${bx} ${by}` : `M ${ax} ${ay} Q ${cx} ${cy} ${bx} ${by}`,
      // The quadratic's midpoint is halfway between the chord and the control point, not at it.
      mx: offset === 0 ? cx : (ax + bx) / 2 + px / 2,
      my: offset === 0 ? cy : (ay + by) / 2 + py / 2,
      isSubject: t.id === subjectId,
      leg: legOf(w, t.id),
      step,
      amount: t.amount_paid,
      currency: t.payment_currency,
    };
  });
}

/** Label placement. `radial` pushes each label away from a centre (the RING); otherwise it sits
 *  above the node, which is collision-free in the layered shapes. */
function labelAt(
  p: { x: number; y: number },
  radial: { cx: number; cy: number } | null,
): Pick<GeoNode, "labelDx" | "labelDy" | "labelAnchor"> {
  if (!radial) return { labelDx: 0, labelDy: -13, labelAnchor: "middle" };
  const dx = p.x - radial.cx;
  const dy = p.y - radial.cy;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  return {
    labelDx: ux * 17,
    // +3.5 puts the mono cap-height on the radius rather than the baseline.
    labelDy: uy * 17 + 3.5,
    // Near the top or bottom of the circle a side-anchored label would hang off the node it names.
    labelAnchor: Math.abs(ux) < 0.34 ? "middle" : ux > 0 ? "start" : "end",
  };
}

function nodesFrom(
  ids: UUID[],
  pos: Map<UUID, { x: number; y: number }>,
  accounts: Record<UUID, AmlAccount>,
  subject: AmlTransaction,
  radial: { cx: number; cy: number } | null = null,
): GeoNode[] {
  return ids.map((id) => {
    const a = accounts[id];
    const p = pos.get(id)!;
    return {
      id,
      x: p.x,
      y: p.y,
      // An unresolved account still gets a node: it is a real endpoint of a real served edge, and
      // dropping it would break the very edge it anchors. It is named by its id fragment.
      bank: a?.bank ?? "—",
      account: a?.account ?? id.slice(0, 6),
      onSubject: id === subject.from_account_id || id === subject.to_account_id,
      ...labelAt(p, radial),
    };
  });
}

/* ------------------------------------- THE RING -------------------------------------------- */
/**
 * A closed directed walk, drawn as a closed directed ring.
 *
 * A circle is the MINIMAL FAITHFUL EMBEDDING of a cycle: the one property that matters — that the
 * last hop returns to the account the first hop left — is the one a circle shows and every other
 * embedding hides. It is honest, not decorative.
 *
 * It CLOSES BY CONSTRUCTION and that is why there is no browser guard for closure: each edge is
 * drawn between the real account ids on its own row, and the backend already pins the data property
 * (57/57 contiguous and closed, `test_cycle_witness_is_a_closed_ring_so_predecessor_is_total`). The
 * one bug that could break it in pixels — collapsing two accounts into one node — cannot manifest:
 * nodes are keyed by account UUID, and account numbers are 648/648 unique in this extract anyway.
 */
function ringLayout(
  rows: AmlTransaction[],
  w: AmlWitness,
  subject: AmlTransaction,
  accounts: Record<UUID, AmlAccount>,
): WitnessGeometry {
  // The walk, taken from the edges themselves: node i is the source of hop i. The subject is hop 0
  // in 57/57, so it lands at the top and the ring reads clockwise from it.
  const walk = rows.map((t) => t.from_account_id);
  const n = walk.length;
  const r = RING_MIN_R + Math.max(0, n - 6) * RING_STEP_R;
  const size = (r + R_PAD) * 2;
  const c = size / 2;

  const pos = new Map<UUID, { x: number; y: number }>();
  walk.forEach((id, i) => {
    const theta = -Math.PI / 2 + (i / n) * Math.PI * 2; // -90deg: hop 0 at twelve o'clock
    pos.set(id, { x: c + r * Math.cos(theta), y: c + r * Math.sin(theta) });
  });

  const first = rows[0];
  const a0 = accounts[first.from_account_id];
  const origin = a0 ? `${a0.bank} ${a0.account}` : first.from_account_id.slice(0, 6);

  return {
    kind: "RING",
    nodes: nodesFrom(walk, pos, accounts, subject, { cx: c, cy: c }),
    edges: drawEdges(rows, w, subject.id, pos),
    width: size,
    height: size,
    citesSubject: rows.some((t) => t.id === subject.id),
    complete: true,
    description:
      `A closed ring of ${n} transactions through ${n} accounts. It leaves ${origin}, and the ` +
      `${n}th hop returns to ${origin} — the account it started from. ` +
      `This drawing is the whole cycle, not a sample of it.`,
  };
}

/* ---------------------------------- LEGS AND BUNDLES ---------------------------------------- */
/**
 * Columns by ROLE, derived from degree — never from a template.
 *
 * All three of these are acyclic by construction (GATHER-SCATTER's source and destination sets are
 * disjoint; STACK's tiers exclude the crossing's own endpoints; SCATTER-GATHER's destination is
 * neither the source nor an intermediary), so a longest-path layering is well defined: a node's
 * column is the longest chain of served edges that reaches it.
 *
 * ONE ALGORITHM COVERS ALL THREE, and that is deliberate. Today's extract is uniform — LEGS is
 * (1 source, 2 intermediaries, 1 destination) in 42/42, GATHER-SCATTER has exactly 1 hub in
 * 107/107, STACK exactly 2 hubs in 35/35 — but hardcoding those shapes would force a future witness
 * that violates them into a picture it does not have. Layering DEGRADES; a template LIES.
 *
 * LEGS IS NOT A PATH, AND THIS DOES NOT MAKE IT ONE. It is drawn as two labelled legs because the
 * API says it has no linear order (`predecessor_of` returns NO_LINEAR_ORDER for it) and the picture
 * must say so too. The columns carry no traversal and nothing animates along them in sequence.
 */
function layeredLayout(
  rows: AmlTransaction[],
  w: AmlWitness,
  subject: AmlTransaction,
  accounts: Record<UUID, AmlAccount>,
): WitnessGeometry {
  const ids: UUID[] = [];
  for (const t of rows) {
    for (const a of [t.from_account_id, t.to_account_id]) if (!ids.includes(a)) ids.push(a);
  }

  // Longest-path layering. Bounded by the node count so a cycle (which cannot occur in these three
  // typologies, but must not hang the console if one ever did) settles instead of spinning.
  const col = new Map<UUID, number>(ids.map((id) => [id, 0]));
  for (let pass = 0; pass < ids.length; pass++) {
    let moved = false;
    for (const t of rows) {
      if (t.from_account_id === t.to_account_id) continue; // a self-edge cannot advance a layer
      const want = (col.get(t.from_account_id) ?? 0) + 1;
      if (want > (col.get(t.to_account_id) ?? 0)) {
        col.set(t.to_account_id, want);
        moved = true;
      }
    }
    if (!moved) break;
  }

  // Group by column, then order within it deterministically (by id) so the picture is stable.
  const byCol = new Map<number, UUID[]>();
  for (const id of [...ids].sort((a, b) => a.localeCompare(b))) {
    const k = col.get(id) ?? 0;
    byCol.set(k, [...(byCol.get(k) ?? []), id]);
  }
  const cols = [...byCol.keys()].sort((a, b) => a - b);
  const tallest = Math.max(...cols.map((k) => byCol.get(k)!.length));

  const width = MARGIN * 2 + Math.max(1, cols.length - 1) * COL_W;
  const height = MARGIN * 2 + Math.max(1, tallest - 1) * ROW_H;

  const pos = new Map<UUID, { x: number; y: number }>();
  cols.forEach((k, ci) => {
    const members = byCol.get(k)!;
    members.forEach((id, ri) => {
      // Centre each column vertically, so a 1-node column sits level with a 2-node one's midpoint.
      const span = (members.length - 1) * ROW_H;
      pos.set(id, {
        x: MARGIN + ci * COL_W,
        y: MARGIN + (height - MARGIN * 2 - span) / 2 + ri * ROW_H,
      });
    });
  });

  const cites = rows.some((t) => t.id === subject.id);
  const n = rows.length;
  const legs =
    w.kind === "LEGS"
      ? ` The ${w.legs?.scatter?.length ?? 0} scatter legs leave one source; the ` +
        `${w.legs?.gather?.length ?? 0} gather legs carry it onward into one destination. ` +
        `These are two parallel routes, not one path: the evidence has no order to walk.`
      : "";

  return {
    kind: w.kind,
    nodes: nodesFrom(ids, pos, accounts, subject),
    edges: drawEdges(rows, w, subject.id, pos),
    width,
    height,
    citesSubject: cites,
    complete: false,
    // The description carries the SAME caveat the picture does. If the drawing says partial, this
    // says partial — an accessible description that omits the caveat is a more confident claim than
    // the one on screen.
    description:
      `A partial citation: the ${n} transactions this witness cites, through ${ids.length} ` +
      `accounts.${legs} It is NOT the whole structure it sits in — the rest of those accounts' ` +
      `transactions are not carried by this response, so they are not drawn. ` +
      (cites
        ? `The subject transaction is one of the ${n}.`
        : `The subject transaction is NOT among them: this witness cites other transactions in the ` +
          `same structure, so nothing here is marked as the subject.`),
  };
}

/* -------------------------------------------------------------------------------------------- */

/**
 * The geometry of ONE witness, or null when there is nothing to draw.
 *
 * Null is the DOMINANT case and it is not a failure: 1,287 of the 1,500 subjects (85.8%) have zero
 * matching witnesses. The ring is the EXCEPTION. A caller must render the negative space, not an
 * empty box — and must never reach for the boundary account to fill it (fact 3).
 */
export function witnessGeometry(
  w: AmlWitness,
  subject: AmlTransaction,
  txns: Record<UUID, AmlTransaction>,
  accounts: Record<UUID, AmlAccount>,
): WitnessGeometry | null {
  if (w.kind === "NONE") return null;
  const rows = resolve(w, txns);
  if (rows.length === 0) return null;
  return w.kind === "RING"
    ? ringLayout(rows, w, subject, accounts)
    : layeredLayout(rows, w, subject, accounts);
}
