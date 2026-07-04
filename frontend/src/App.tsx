import { useConsoleData } from "./hooks/useConsoleData";
import { Loaded, Panel } from "./components/Panel";
import { DecisionFeed } from "./components/DecisionFeed";
import { GenealogyTree } from "./components/GenealogyTree";
import { Inspector } from "./components/Inspector";
import "./App.css";

/*
 * The Lineage supervisor console shell — Frontend Phase 2.
 *
 * Three regions (decision feed / genealogy tree / inspector), each fed real data
 * from the backend via useConsoleData. Presentational only: NO interactions this
 * phase (investigate/trace/time-travel/invalidate are Phase 3), NO motion, NO
 * color warmth (warmth is reserved for the Phase-3 trace so it lands hard).
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

  const decisionCount =
    decisions.status === "ready" ? decisions.data.total : undefined;
  const beliefCount = beliefs.status === "ready" ? beliefs.data.count : undefined;

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
              {(data) => <DecisionFeed data={data} />}
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
            <Loaded state={beliefs} loadingLabel="Loading beliefs…">
              {(data) => <Inspector data={data} />}
            </Loaded>
          </Panel>
        </div>
      </div>
    </div>
  );
}

export default App;
