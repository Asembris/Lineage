import { useCallback, useEffect, useState } from "react";
import { useConsoleData } from "./hooks/useConsoleData";
import { Loaded, Panel } from "./components/Panel";
import { DecisionFeed } from "./components/DecisionFeed";
import { GenealogyTree } from "./components/GenealogyTree";
import { Inspector } from "./components/Inspector";
import { ConsistencyDemo } from "./components/ConsistencyDemo";
import { HonestyLedger } from "./components/HonestyLedger";
import { AmlConsole } from "./components/AmlConsole";
import { LineageEmblem } from "./components/LineageEmblem";
import type { InvestigationTrace } from "./components/Investigation";
import type { InvalidateHandlers, InvalidateUi } from "./components/Invalidate";
import type { TreeInvalidation } from "./components/GenealogyTree";
import { resolveInvestigation } from "./lib/investigation";
import { deriveChain } from "./lib/trace";
import { SUPERVISOR_ACTOR } from "./lib/supervisor";
import { ApiError, getBeliefLineage, invalidateBelief } from "./api/client";
import { formatCount } from "./lib/format";
import type {
  DecisionKind,
  InvalidateResponse,
  LineageResponse,
  UUID,
  WitnessOutcome,
} from "./api/types";
import "./App.css";

/*
 * The Lineage supervisor console shell.
 *
 * Three regions (decision feed / genealogy tree / inspector), each fed real data
 * from the backend via useConsoleData. Frontend Phase 3 wires the supervisor
 * interactions: Investigate (select a decision → its driving belief, tagged
 * inherited/formed-here) and Trace (walk that belief backward through the tree to
 * its igniting origin, via the real GET /beliefs/{id}/lineage). App owns the trace
 * state because three regions coordinate: the trigger is in the Inspector, the
 * animation is in the tree, and the resolved conclusion returns to the Inspector.
 */

/** The trace lifecycle. `chain` is the real leaf→origin inheritance chain; `phase`
 *  flips to "done" when the tree reports the origin has ignited. */
type TraceState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "empty"; lineage: LineageResponse } // agent isn't a holder — nothing to trace
  | {
      status: "ready";
      lineage: LineageResponse;
      chain: UUID[];
      playToken: number;
      phase: "animating" | "done";
    };

/** The belief's real inheritance closure, derived from GET /beliefs/{id}/lineage. The
 *  living holders are the closure's alive agents — the fork Invalidate reveals (> 1 here)
 *  and corrects at one commit. edgeCount is the number of inheritance edges (non-origin nodes). */
interface Closure {
  livingHolders: UUID[];
  edgeCount: number;
}

/** The invalidate lifecycle. App owns it (like TraceState) because the confirm gate is in the
 *  Inspector while the closure-reveal + atomic-correction animation is in the genealogy tree. */
type InvalidateState =
  | { status: "idle" }
  | { status: "arming"; beliefId: UUID } // fetching the closure to populate the confirm scope
  | { status: "confirming"; beliefId: UUID; closure: Closure }
  | { status: "invalidating"; beliefId: UUID; closure: Closure }
  | { status: "done"; beliefId: UUID; closure: Closure; result: InvalidateResponse; playToken: number }
  | { status: "error"; beliefId: UUID; message: string; alreadyInvalidated: boolean; closure: Closure | null };

function deriveClosure(lineage: LineageResponse): Closure {
  return {
    livingHolders: lineage.path.filter((n) => n.status === "alive").map((n) => n.agent_id),
    edgeCount: lineage.path.filter((n) => n.from_agent_id !== null).length,
  };
}

function FleetSummary({ agents }: { agents: ReturnType<typeof useConsoleData>["agents"] }) {
  if (agents.status !== "ready") {
    return <span className="console__fleet">fleet · —</span>;
  }
  const total = agents.data.count;
  const alive = agents.data.agents.filter((a) => a.status === "alive").length;
  return (
    <div className="console__fleet">
      <span>
        <span className="alive">{alive}</span> alive
      </span>
      <span className="console__fleet-sep">/</span>
      <span>{total} agents</span>
    </div>
  );
}

/** Which surface fills the console body. "consistency" is the standalone fleet-level demo
 *  (Frontend Phase 4); "ledger" is the honesty ledger (Item 9). Both take over the whole body
 *  rather than hanging off a selected row, because they are fleet-scoped, not per-decision/
 *  per-agent like the four supervisor interactions — the same "fleet-scoped ⇒ header mode"
 *  reasoning Phase 4 used. A plain view flag (no router, no new dep).
 *
 * `aml` — the EVIDENCE surface (Rung 2). It takes over the body for the same structural reason,
 * but a far more important one: the audit layer must NOT be on screen beside a witness. The feed
 * and the Inspector legitimately render `is_fraud` (the label attached to a decision that was
 * already made without it — an AUDIT fact, which is the one place it may be served), and drawing
 * the witness next to them would put the answer key beside the exam. A discriminated union is what
 * makes that impossible rather than merely avoided: exactly one body arm is ever mounted, and the
 * `aml` arm carries only a transaction id.
 *
 * THE JOIN IS AN ID. App is the composition root — it is the one place that legitimately holds both
 * layers, and it is exempt from the composition guard BY STRUCTURE (it takes no props, so it has no
 * prop surface to violate). What it hands down is a `UUID`. An id carries no verdict and no ground
 * truth, and the guard proves nothing downstream of it can reach one. */
type View =
  | { kind: "console" }
  | { kind: "consistency" }
  | { kind: "ledger" }
  | { kind: "aml"; txnId: UUID };

function App() {
  // The feed's kind filter. null = unfiltered, and that is the deliberate default: the console
  // does not pre-filter the fleet's record for the supervisor. See DecisionFeed's header for why
  // the filter had to exist at all (the fixed AML `decided_at` buried every card decision).
  const [kind, setKind] = useState<DecisionKind | null>(null);
  // The BASIS filter, meaningful only for AML decisions. Null = every basis, and that is the
  // default for the same reason `kind` defaults to unfiltered: the console does not decide for the
  // supervisor which part of the record is worth looking at.
  const [witness, setWitness] = useState<WitnessOutcome | null>(null);
  const { agents, decisions, beliefs, counts, witnessCounts, loadMore, loadingMore } =
    useConsoleData(kind, witness);
  const [view, setView] = useState<View>({ kind: "console" });

  // The seam's focus handoff (return leg). When the evidence view closes, focus should return to the
  // "see why" row it came from, not vanish to <body> (verified by a tab-walk: it did). App remembers
  // the transaction; DecisionFeed refocuses that row's control when it remounts. The forward leg —
  // focusing the evidence surface's heading on open — lives in AmlConsole.
  const [returnFocusTxn, setReturnFocusTxn] = useState<UUID | null>(null);
  const clearReturnFocus = useCallback(() => setReturnFocusTxn(null), []);

  // Investigate: which decision is under investigation (null = none). Clicking a
  // selected row again clears it. State lives here because the feed (left) and the
  // Inspector (right) are siblings that both need it.
  const [selectedId, setSelectedId] = useState<UUID | null>(null);
  const onSelect = (id: UUID) =>
    setSelectedId((cur) => (cur === id ? null : id));

  // Changing the filter can drop the investigated row out of the loaded feed, and an investigation
  // whose decision is no longer on screen is a stale surface. Clear it with the filter.
  const onKind = (k: DecisionKind | null) => {
    setKind(k);
    setSelectedId(null);
    // A basis filter is meaningless off the AML kind (a card decision has no witness), so leaving
    // one armed while switching to `card` would silently return nothing and read as an empty world.
    if (k !== "aml") setWitness(null);
  };
  const onWitness = (w: WitnessOutcome | null) => {
    setWitness(w);
    setSelectedId(null);
  };
  const investigation = resolveInvestigation(selectedId, decisions, agents, beliefs);

  // THE JUSTIFICATION SEAM. A verdict is a conclusion; the witness that defends it is the
  // justification, and it lived three clicks deep (select → Investigation → interrogate). This
  // opens the evidence surface for a transaction directly — from a feed row's "see why", or from
  // the Investigation's interrogate button. It is a VIEW TRANSITION, not a co-mount: `view` flips
  // to the `aml` arm and the console body (feed, Inspector, and every `is_fraud`) UNMOUNTS. The
  // join it hands down is a bare UUID; the witness is re-derived FRESH by /interrogate, never
  // carried from the decision. See the composition guard and NOTES "AML CONSOLE — RUNG 4".
  const openInterrogation = (txnId: UUID) => setView({ kind: "aml", txnId });

  // Trace state. Changing the investigated decision resets any active trace so a
  // stale warm path never lingers over a new investigation.
  const [trace, setTrace] = useState<TraceState>({ status: "idle" });
  useEffect(() => setTrace({ status: "idle" }), [selectedId]);

  const startTrace = async (beliefId: UUID, agentId: UUID) => {
    setTrace({ status: "loading" });
    try {
      const lineage = await getBeliefLineage(beliefId);
      const chain = deriveChain(lineage, agentId);
      if (chain.length === 0) {
        setTrace({ status: "empty", lineage });
      } else {
        setTrace({ status: "ready", lineage, chain, playToken: 0, phase: "animating" });
      }
    } catch (err) {
      setTrace({ status: "error", message: err instanceof Error ? err.message : String(err) });
    }
  };
  const replayTrace = () =>
    setTrace((t) =>
      t.status === "ready" ? { ...t, playToken: t.playToken + 1, phase: "animating" } : t,
    );
  const onTraceComplete = () =>
    setTrace((t) => (t.status === "ready" ? { ...t, phase: "done" } : t));

  // Invalidate state — the one governed write. Also reset when the investigated decision
  // changes, so a completed/armed kill never lingers over a fresh investigation.
  const [inval, setInval] = useState<InvalidateState>({ status: "idle" });
  useEffect(() => setInval({ status: "idle" }), [selectedId]);

  const armInvalidate = async (beliefId: UUID) => {
    setInval({ status: "arming", beliefId });
    try {
      const lineage = await getBeliefLineage(beliefId);
      setInval({ status: "confirming", beliefId, closure: deriveClosure(lineage) });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setInval({ status: "error", beliefId, message, alreadyInvalidated: false, closure: null });
    }
  };
  const cancelInvalidate = () => setInval({ status: "idle" });
  const confirmInvalidate = async () => {
    if (inval.status !== "confirming") return; // Confirm is only reachable from the gate.
    const { beliefId, closure } = inval;
    setInval({ status: "invalidating", beliefId, closure });
    try {
      const result = await invalidateBelief(beliefId, SUPERVISOR_ACTOR);
      setInval({ status: "done", beliefId, closure, result, playToken: 1 });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const alreadyInvalidated = err instanceof ApiError && err.status === 409;
      setInval({ status: "error", beliefId, message, alreadyInvalidated, closure });
    }
  };

  // View projections for the tree + inspector consumers.
  const treeTrace =
    trace.status === "ready"
      ? { chain: trace.chain, playToken: trace.playToken, onComplete: onTraceComplete }
      : null;

  // Tree overlay: mark the living holders --alert while armed, then --alive on correction.
  const treeInvalidation: TreeInvalidation | null =
    inval.status === "confirming" || inval.status === "invalidating"
      ? { livingHolders: inval.closure.livingHolders, phase: "armed", playToken: 0 }
      : inval.status === "done"
        ? { livingHolders: inval.closure.livingHolders, phase: "corrected", playToken: inval.playToken }
        : inval.status === "error" && inval.closure
          ? { livingHolders: inval.closure.livingHolders, phase: "armed", playToken: 0 }
          : null;

  const invalidateUi: InvalidateUi =
    inval.status === "arming"
      ? { status: "arming" }
      : inval.status === "confirming"
        ? {
            status: "confirming",
            livingHolderCount: inval.closure.livingHolders.length,
            edgeCount: inval.closure.edgeCount,
          }
        : inval.status === "invalidating"
          ? {
              status: "invalidating",
              livingHolderCount: inval.closure.livingHolders.length,
              edgeCount: inval.closure.edgeCount,
            }
          : inval.status === "done"
            ? { status: "done", result: inval.result }
            : inval.status === "error"
              ? { status: "error", message: inval.message, alreadyInvalidated: inval.alreadyInvalidated }
              : { status: "idle" };

  const invalidateHandlers: InvalidateHandlers = {
    ui: invalidateUi,
    onArm: armInvalidate,
    onConfirm: confirmInvalidate,
    onCancel: cancelInvalidate,
  };

  const traceUi: InvestigationTrace =
    trace.status === "ready"
      ? { status: "active", phase: trace.phase, chainLength: trace.chain.length }
      : trace.status === "error"
        ? { status: "error", message: trace.message }
        : trace.status === "loading"
          ? { status: "loading" }
          : trace.status === "empty"
            ? { status: "empty" }
            : { status: "idle" };

  // Feed header count is honest about the bounding: loaded / total. The feed shows the most-recent
  // page (limit 200), not every row. Under a kind filter the denominator is that kind's real total
  // (the backend counts it), so the ratio never overstates what is on screen; the chips carry the
  // other kinds' counts so the cluster-wide picture stays visible.
  const decisionCount =
    decisions.status === "ready"
      ? `${formatCount(decisions.data.decisions.length)} / ${formatCount(decisions.data.total)}`
      : undefined;
  // While a decision is under investigation the Inspector is taken over, so its
  // header count (active beliefs) is dropped to keep that surface clean.
  const beliefCount =
    !investigation && beliefs.status === "ready" ? beliefs.data.count : undefined;

  return (
    <div className="console">
      <header className="console__header">
        <div className="console__brand">
          <LineageEmblem className="console__mark" />
          <h1 className="console__title">LINEAGE</h1>
          <span className="console__tagline">supervisor console</span>
        </div>
        <nav className="console__views" aria-label="Console view">
          <button
            className="console__view"
            aria-pressed={view.kind === "console"}
            onClick={() => setView({ kind: "console" })}
          >
            Console
          </button>
          <button
            className="console__view"
            aria-pressed={view.kind === "consistency"}
            onClick={() => setView({ kind: "consistency" })}
          >
            Consistency demo
          </button>
          <button
            className="console__view"
            aria-pressed={view.kind === "ledger"}
            onClick={() => setView({ kind: "ledger" })}
          >
            Ledger
          </button>
        </nav>
        <FleetSummary agents={agents} />
      </header>

      {/* EXACTLY ONE BODY ARM IS MOUNTED. The `aml` arm renders the evidence surface ALONE — no
          feed, no Inspector, no audit component anywhere in its subtree. That is not a layout
          preference; it is the oracle boundary in pixels, and `frontend/scripts/composition-guard.mjs`
          fails the build if an audit-coloured component ever appears inside this arm. */}
      {view.kind === "aml" ? (
        <AmlConsole
          txnId={view.txnId}
          onClose={() => {
            setReturnFocusTxn(view.txnId);
            setView({ kind: "console" });
          }}
        />
      ) : view.kind === "consistency" ? (
        <ConsistencyDemo />
      ) : view.kind === "ledger" ? (
        <HonestyLedger agents={agents} decisions={decisions} beliefs={beliefs} />
      ) : (
        <div className="console__body">
          <div className="console__region">
            <Panel title="Decision feed" count={decisionCount}>
              <Loaded state={decisions} loadingLabel="Loading decisions…">
                {(data) => (
                  <DecisionFeed
                    data={data}
                    selectedId={selectedId}
                    onSelect={onSelect}
                    onInterrogate={openInterrogation}
                    kind={kind}
                    onKind={onKind}
                    counts={counts}
                    witness={witness}
                    onWitness={onWitness}
                    witnessCounts={witnessCounts}
                    loadMore={loadMore}
                    loadingMore={loadingMore}
                    returnFocusTxn={returnFocusTxn}
                    onFocusReturned={clearReturnFocus}
                  />
                )}
              </Loaded>
            </Panel>
          </div>

          <div className="console__region">
            <Panel title="Genealogy">
              <Loaded state={agents} loadingLabel="Loading genealogy…">
                {(data) => (
                  <GenealogyTree data={data} trace={treeTrace} invalidation={treeInvalidation} />
                )}
              </Loaded>
            </Panel>
          </div>

          <div className="console__region">
            <Panel title="Inspector" count={beliefCount}>
              <Inspector
                agents={agents}
                decisions={decisions}
                beliefs={beliefs}
                investigation={investigation}
                onClear={() => setSelectedId(null)}
                traceHandlers={{
                  trace: traceUi,
                  onStartTrace: startTrace,
                  onReplay: replayTrace,
                }}
                invalidateHandlers={invalidateHandlers}
                onInterrogate={openInterrogation}
              />
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
