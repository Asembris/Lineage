/*
 * WitnessGeometry — the money flow, DRAWN. Rung 3 of the AML console.
 *
 * Rung 2 stated each witness's size and shape typographically ("a closed ring — contiguous, and it
 * returns to its source") and drew none of it. This draws it, and the signature moment is the ring:
 * the hops arrive one at a time and the LAST ONE LANDS BACK ON THE ACCOUNT THE FIRST ONE LEFT. That
 * closure is the belief's own sentence rendered — "a transfer that completes a directed cycle
 * returning to its account of origin" — and it is the only thing on this surface that a reader can
 * check for themselves without trusting a word of the prose.
 *
 * ================== THE RING IS THE EXCEPTION, NOT THE HEADLINE ==================
 * 1,287 of the 1,500 subjects (85.8%) have ZERO matching witnesses. There is nothing structural to
 * draw for the overwhelming majority, and this component's most common output is the sentence that
 * says so. The negative space IS the product; a console that centres on a shape 3.8% of subjects
 * have would be lying about what the detector mostly does.
 *
 * ================== WHAT IS DRAWN IS WHAT IS ON THE WIRE ==================
 * Only the RING is the whole structure. LEGS and BUNDLE are BOUNDED CITATIONS — `aml_graph.py`
 * truncates the hub's edges to MIN_FANOUT, so a GATHER-SCATTER whose own prose says "gathers from
 * 12 sources" ships exactly 4 transactions. The picture and the sentence beside it therefore differ
 * in magnitude, and THE PICTURE SAYS SO, in the pixels, on its own face. It is never reconciled by
 * parsing the prose (lib/basis.ts bans that, with a test) and never by inventing the missing edges.
 * See lib/witnessGeometry.ts for the measurements. The real fix is the `neighbourhood` endpoint.
 *
 * ================== COLOUR ==================
 * `--bone` marks what the system is pointing at — the subject transaction, and the account the ring
 * returns to — and nothing else. No `--alert`: painting a witness red would fuse "the graph found a
 * structure" with "this is fraud", which is the oracle-boundary collapse in colour form, and is the
 * whole thing this console exists to avoid. No `--trace`/`--origin`: warmth is Trace's, and a ring
 * closing is not a trace igniting. The world here is cold and it stays cold.
 *
 * ================== MOTION ==================
 * `pathLength` + opacity only, on the shared DUR/EASE tokens. `prefers-reduced-motion` collapses to
 * the IDENTICAL FINAL FRAME, instantly — not a faster animation. For a RING that means every hop
 * drawn and the ring already closed; for LEGS both legs fully drawn; for BUNDLE the whole edge set
 * present. The final frame is the evidence, and it is never withheld from anyone.
 */

import { motion, useReducedMotion } from "framer-motion";
import type { AmlInterrogationResponse, AmlWitness } from "../api/types";
import { DUR, EASE } from "../lib/motion";
import { witnessGeometry, type GeoEdge, type WitnessGeometry as Geo } from "../lib/witnessGeometry";
import { formatAmount } from "../lib/format";
import "./WitnessGeometry.css";

/** Per-hop stagger for the ring's draw. The whole 10-hop ring lands inside ~1.1s. */
const HOP = 0.09;

/** The arrowheads. One `defs` for the whole section — SVG ids are document-global. */
export function GeometryDefs() {
  return (
    <svg className="geo__defs" aria-hidden="true" focusable="false">
      <defs>
        {(["quiet", "subject"] as const).map((tone) => (
          <marker
            key={tone}
            id={`geo-arrow-${tone}`}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="5"
            markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 8 4 L 0 8 z" className={`geo__head geo__head--${tone}`} />
          </marker>
        ))}
      </defs>
    </svg>
  );
}

/**
 * SEQUENCE IS A CLAIM, SO ONLY THE RING GETS ONE.
 *
 * A ring is a walk: its hops are drawn one after another via `pathLength`, and the last one arrives
 * back at the account the first one left. That order is real — `predecessor_of` resolves it.
 *
 * LEGS and BUNDLE have NO linear order (`predecessor_of` returns NO_LINEAR_ORDER for them), so
 * their edges fade in TOGETHER. Staggering them would animate an order the evidence does not have,
 * and motion asserting a fact the API denies is the same lie as prose doing it.
 */
function Edge({ e, reduced, sequenced }: { e: GeoEdge; reduced: boolean; sequenced: boolean }) {
  const tone = e.isSubject ? "subject" : "quiet";

  // The final frame — the evidence itself. Reduced motion arrives here INSTANTLY, not slowly.
  const settled = { pathLength: 1, opacity: 1 };

  return (
    <motion.path
      // THE EDGE IS THE TRANSACTION. Keyed by transaction id, never by its endpoints — two distinct
      // transactions can run between the same pair of accounts, and merging them would draw three
      // lines where the evidence has four.
      className={`geo__edge geo__edge--${tone}${e.leg ? ` geo__edge--${e.leg}` : ""}`}
      d={e.d}
      markerEnd={`url(#geo-arrow-${tone})`}
      initial={reduced ? settled : sequenced ? { pathLength: 0, opacity: 0 } : { pathLength: 1, opacity: 0 }}
      animate={settled}
      transition={
        reduced
          ? { duration: 0 }
          : sequenced
            ? { duration: DUR.reveal * 2, ease: EASE.out, delay: e.step * HOP }
            : { duration: DUR.reveal, ease: EASE.out }
      }
    >
      {/* Every edge is a real row. The tooltip states it without the reader leaving the drawing. */}
      <title>{`${formatAmount(e.amount, e.currency)}${e.leg ? ` · ${e.leg} leg` : ""}${
        e.isSubject ? " · the subject transaction" : ""
      }`}</title>
    </motion.path>
  );
}

function Figure({ geo, typology }: { geo: Geo; typology: string }) {
  const reduced = useReducedMotion() ?? false;

  return (
    <figure className="geo">
      <figcaption className="geo__cap">
        <span className="geo__typology">{typology}</span>
        {/* THE FRAME IS IN THE PIXELS, NOT THE DOCS. A reader must not be able to mistake a bounded
            citation for the structure — so the partial ones say "partial" on their own face, beside
            the drawing, every time. */}
        {geo.complete ? (
          <span className="geo__scope geo__scope--whole">
            the whole cycle · {geo.edges.length} transactions
          </span>
        ) : (
          <span className="geo__scope geo__scope--partial">
            partial · the {geo.edges.length} transactions this witness cites
          </span>
        )}
      </figcaption>

      <svg
        className="geo__svg"
        viewBox={`0 0 ${geo.width} ${geo.height}`}
        width={geo.width}
        height={geo.height}
        role="img"
        // A fact rendered only as pixels is a fact a screen reader cannot read. This carries the
        // SAME evidence the drawing does — INCLUDING the citation caveat. If the picture says
        // partial, the description says partial.
        aria-label={geo.description}
      >
        {geo.edges.map((e) => (
          <Edge key={e.id} e={e} reduced={reduced} sequenced={geo.kind === "RING"} />
        ))}

        {geo.nodes.map((n) => (
          <g
            key={n.id}
            className={`geo__node${n.onSubject ? " geo__node--on-subject" : ""}`}
            transform={`translate(${n.x} ${n.y})`}
          >
            <circle className="geo__dot" r="5" />
            {/* The account NUMBER is the node's visible identity (648/648 unique in this extract);
                the bank is in the title, because a two-line label at every node of a 10-hop ring is
                unreadable and the compound identity is what the tooltip and the description carry.
                The offset is RADIAL on a ring — a label above every node overlaps the ring's own
                stroke on the left and right, hiding the edges it exists to annotate. */}
            <text
              className="geo__acct"
              x={n.labelDx}
              y={n.labelDy}
              textAnchor={n.labelAnchor}
            >
              {n.account}
            </text>
            <title>{`${n.bank} · ${n.account}`}</title>
          </g>
        ))}
      </svg>

      {/* TWO LABELLED LEGS, AND IT SAYS SO. The dash alone would be a key the reader does not have:
          which edges are the scatter and which the gather is a FACT about the evidence, and a fact
          carried only by a stroke pattern with no legend is a fact the reader cannot read. These
          are two parallel routes — the API returns NO_LINEAR_ORDER for them — and the legend says
          that too, so nobody reads the diamond as a path. */}
      {geo.kind === "LEGS" && (
        <ul className="geo__legend">
          {/* `geo__edge` MEANS "A TRANSACTION", AND A LEGEND KEY IS NOT ONE. These swatches first
              carried that class for its stroke styling, and the geometry guard immediately failed:
              it counted 6 edges where the witness cites 4, because the two legend keys were being
              counted as money. The guard was right and the markup was wrong — a class that names a
              thing must not be worn by something that is not that thing. */}
          <li>
            <svg className="geo__swatch" viewBox="0 0 26 6" aria-hidden="true">
              <path className="geo__swatch-line" d="M 1 3 L 25 3" />
            </svg>
            <span>scatter — one source fans out</span>
          </li>
          <li>
            <svg className="geo__swatch" viewBox="0 0 26 6" aria-hidden="true">
              <path className="geo__swatch-line geo__swatch-line--gather" d="M 1 3 L 25 3" />
            </svg>
            <span>gather — they carry it into one destination</span>
          </li>
          <li className="geo__legend-note">
            Two parallel routes, not one path. The evidence has no order to walk.
          </li>
        </ul>
      )}

      {!geo.complete && (
        <p className="geo__caveat">
          This is what the witness <strong>cites</strong>, not the whole structure it sits in. Those
          accounts&rsquo; other transactions are not carried by this response, so they are not drawn
          — and none are invented to finish the picture.
        </p>
      )}
      {!geo.citesSubject && (
        // 75 of 107 GATHER-SCATTER witnesses omit the subject. Marking an edge here anyway would
        // point the reader at a transaction they did not click.
        <p className="geo__caveat">
          The subject transaction is <strong>not among them</strong>. This witness cites other
          transactions in the same structure, so nothing here is marked as the subject.
        </p>
      )}
    </figure>
  );
}

/**
 * Every structure the graph witnessed around this subject — one drawing per matching witness.
 *
 * The competing case (a subject witnessed by CYCLE *and* GATHER-SCATTER *and* STACK) renders as
 * SEPARATE drawings, deliberately. They share edges, and merging them into one picture would invent
 * a combined structure that no witness claims.
 */
export function WitnessGeometrySection({ r }: { r: AmlInterrogationResponse }) {
  const drawn = r.witnesses
    .map((w: AmlWitness) => ({ w, geo: witnessGeometry(w, r.subject, r.transactions, r.accounts) }))
    .filter((x): x is { w: AmlWitness; geo: Geo } => x.geo !== null);

  return (
    <section className="geo__section">
      <div className="geo__section-head">
        <span className="aml__label">the money flow, drawn from the cited rows</span>
      </div>

      {drawn.length === 0 ? (
        // THE DOMINANT VIEW: 1,287 of 1,500 subjects land here. This is not an empty state — it is
        // the finding. And the searched region is NOT drawn, because it is not on the wire: an
        // INCONCLUSIVE witness names the boundary account (Rung 2 renders it, above) but no
        // transaction reaching it is served. A line to it would be an invented edge.
        <p className="geo__none">
          <strong>No structure to draw.</strong> No typology witnessed a structure around this
          transaction, so there is no money flow to render — which is the case for 1,287 of the
          1,500 transactions in the extract. The region the search <em>did</em> walk is not carried
          by this response, and it is not drawn from anything else.
        </p>
      ) : (
        <>
          <GeometryDefs />
          <div className="geo__figures">
            {drawn.map(({ w, geo }) => (
              <Figure key={w.typology} geo={geo} typology={w.typology} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
