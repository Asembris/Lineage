/*
 * closure — THE derivations over the consistency demo's RECORDED observer samples.
 *
 * ============================ WHY THIS MODULE EXISTS (design-port S7) ============================
 * The 2D DrainMeter and the 3D scene are two presentations of ONE real stream and can NEVER
 * disagree — that is the demo's entire claim. Before S7 they each derived "the sample being shown"
 * independently (`samples[samples.length - 1]`, written twice) and happened to agree because both
 * always showed the tail. The moment a scrubber exists that agreement breaks by construction: the
 * meter would show the INSPECTED sample while the scene kept showing the LATEST one, and the two
 * views of one database state would contradict each other on screen.
 *
 * So the derivation lives HERE, in exactly one function, and both renderers are handed its result.
 * Disagreement is not "tested for" — it is unrepresentable, because there is only one derivation.
 *
 * ============================ WHAT IS AND IS NOT ALLOWED IN HERE ================================
 * Every function below reads ONLY `sample` events that actually arrived on
 * GET /demo/consistency/stream. There is NO interpolation, NO smoothing, and NO synthesis:
 *   - a ClosureView is one recorded sample, rendered verbatim;
 *   - there is no way to ask for "the state at time t" — the only addressable positions are the
 *     samples themselves, so an intermediate frame has no representation to be invented in.
 * This is load-bearing, not stylistic. The proof this surface makes is that EVENTUAL exposes a
 * committed, externally-visible torn window and ATOMIC does not (open_edges goes 8→0 in ONE sample,
 * so no torn frame ever exists). A single interpolated frame between two real samples would render
 * a state the database structurally cannot produce, and would destroy the claim it illustrates.
 *
 * The DC spec this ports from (`Lineage - Supervisor Console.html`, renderConsistency) does the
 * opposite: its `cTimeline()` hardcodes fabricated milliseconds and derives holder state from
 * `t >= T.EVH[i]` against a requestAnimationFrame playhead — a simulation with no stream behind it.
 * We took its CONTROLS, never its clock. See NOTES.md "design-port S7".
 */

import type { ConsistencySampleEvent, ConsistencyState } from "../api/types";

/**
 * The closure exactly as ONE recorded observer sample saw it. `sample` is null only BEFORE a run
 * has produced anything (the idle preview), never mid-stream.
 */
export interface ClosureView {
  /** The recorded sample this view renders, verbatim. Null only in the pre-run preview. */
  sample: ConsistencySampleEvent | null;
  /** Closure size. Off the wire (`total_edges`) whenever a sample exists. */
  total: number;
  /** Holder edges still open — still live on the belief. Off the wire (`open_edges`). */
  open: number;
  /** ALL_ACTIVE | SPLIT | ALL_INVALIDATED, off the wire (`state`). */
  state: ConsistencyState;
  /** total - open. The only arithmetic here, and it is not a new fact. */
  closed: number;
}

/**
 * Closure size shown BEFORE any sample has arrived — the idle "closure at rest · 8/8 holders live"
 * preview, so the 2D/3D toggle has a visible effect before a destructive run is armed. It is the
 * real seeded closure size (8 inherited edges), not a placeholder for a number we lack: the instant
 * the first sample lands, `total_edges` off the wire wins and this is never consulted again.
 */
const TOTAL_AT_REST = 8;

/**
 * THE derivation. One sample in, one renderable closure state out. Called once per render in
 * ConsistencyDemo's Observation and handed to BOTH the 2D meter and the 3D scene.
 */
export function closureView(sample: ConsistencySampleEvent | null): ClosureView {
  const total = sample?.total_edges ?? TOTAL_AT_REST;
  const open = sample?.open_edges ?? total;
  const state: ConsistencyState = sample?.state ?? "ALL_ACTIVE";
  return { sample, total, open, state, closed: total - open };
}

/**
 * The observer SAMPLE that witnessed holder `i`'s edge closing: the first sample whose `open_edges`
 * dropped to at most `total-(i+1)` (i.e. holder i and everything earlier is closed).
 *
 * This is a RUN-LEVEL fact — "the first read in which this edge was observed closed" — and is
 * deliberately independent of where the scrubber is parked. Whether the edge is open or closed AS OF
 * the sample being inspected comes from ClosureView.closed instead; the UI labels the two separately
 * so they cannot be read as one claim.
 *
 * For the strong path open jumps 8→0 in one sample, so every holder resolves to that single commit
 * sample — which is honest: they all closed at one commit. Returns the sample's seq, or null if that
 * holder has not been observed closed in the samples received so far.
 */
export function witnessSeq(
  i: number,
  samples: ConsistencySampleEvent[],
  total: number,
): number | null {
  const threshold = total - (i + 1);
  const s = samples.find((x) => x.open_edges <= threshold);
  return s ? s.seq : null;
}

/**
 * Indices of every sample that OBSERVABLY DIFFERS from the one before it — the reader saw the
 * closure change between these two reads. Index 0 is always included: the run's first observation.
 *
 * WHAT THIS IS NOT, AND WHY IT MATTERS. These are NOT commit points and are never labelled as such.
 * A change is observer-resolution-limited: two holder edges closing between two reads appear as ONE
 * drop, and the belief row's own commit is not visible as an `open_edges` change at all. Counting
 * these would land near 8 and visibly contradict `summary.commit_points: 9`, which is the
 * authoritative figure and comes off the wire. So the UI navigates by these and NEVER tallies them.
 *
 * (The DC spec has no such hazard because it controls its own fake clock — its rail is captioned
 * "nine points" over eight fabricated holder times plus a fabricated belief time. Ours is an
 * observation of a real fan-out, at a real sampling interval, and says only what it saw.)
 */
export function changeIndices(samples: ConsistencySampleEvent[]): number[] {
  const out: number[] = [];
  samples.forEach((s, i) => {
    if (i === 0 || s.open_edges !== samples[i - 1].open_edges || s.state !== samples[i - 1].state) {
      out.push(i);
    }
  });
  return out;
}

/**
 * Index of the first sample the external reader saw in a torn (SPLIT) state, or null if no such
 * sample exists in this run.
 *
 * This is the seek-to-torn control's ONLY input, deliberately. Gating that control on the strategy
 * ("strong ⇒ hide it") would make the UI ASSERT the atomic claim; gating it on the recorded samples
 * makes the UI TEST it — if the atomic path ever did commit an externally-visible torn closure, the
 * control lights up and takes you straight to the sample that proves it. Its absence on the strong
 * path is then a measurement, not a configuration.
 */
export function firstSplitIndex(samples: ConsistencySampleEvent[]): number | null {
  const i = samples.findIndex((s) => s.state === "SPLIT");
  return i === -1 ? null : i;
}

/** How many recorded samples caught the closure torn. 0 on the atomic path — that IS the result. */
export function splitSampleCount(samples: ConsistencySampleEvent[]): number {
  return samples.filter((s) => s.state === "SPLIT").length;
}
