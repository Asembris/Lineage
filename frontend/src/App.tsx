import { useState } from "react";
import { useConsoleData } from "./hooks/useConsoleData";
import { Loaded, Panel } from "./components/Panel";
import { DecisionFeed } from "./components/DecisionFeed";
import { GenealogyTree } from "./components/GenealogyTree";
import { Inspector } from "./components/Inspector";
import { resolveInvestigation } from "./lib/investigation";
import { formatCount } from "./lib/format";
import type { UUID } from "./api/types";
import "./App.css";

/*
 * The Lineage supervisor console shell.
 *
 * Three regions (decision feed / genealogy tree / inspector), each fed real data
 * from the backend via useConsoleData. Frontend Phase 3 wires the first
 * interaction — Investigate: selecting a decision in the feed takes over the
 * Inspector to show the belief that drove it, tagged inherited / formed-here.
 * Still cold and motionless (warmth/motion are reserved for the Trace step).
 */

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

function App() {
  const { agents, decisions, beliefs } = useConsoleData();

  // Investigate: which decision is under investigation (null = none). Clicking a
  // selected row again clears it. State lives here because the feed (left) and the
  // Inspector (right) are siblings that both need it.
  const [selectedId, setSelectedId] = useState<UUID | null>(null);
  const onSelect = (id: UUID) =>
    setSelectedId((cur) => (cur === id ? null : id));
  const investigation = resolveInvestigation(selectedId, decisions, agents, beliefs);

  // Feed header count is honest about the bounding: loaded / cluster total. The
  // feed shows the most-recent page (limit 200), not every row.
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
          <h1 className="console__title">LINEAGE</h1>
          <span className="console__tagline">supervisor console</span>
        </div>
        <FleetSummary agents={agents} />
      </header>

      <div className="console__body">
        <div className="console__region">
          <Panel title="Decision feed" count={decisionCount}>
            <Loaded state={decisions} loadingLabel="Loading decisions…">
              {(data) => (
                <DecisionFeed data={data} selectedId={selectedId} onSelect={onSelect} />
              )}
            </Loaded>
          </Panel>
        </div>

        <div className="console__region">
          <Panel title="Genealogy">
            <Loaded state={agents} loadingLabel="Loading genealogy…">
              {(data) => <GenealogyTree data={data} />}
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
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}

export default App;
