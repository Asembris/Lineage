import { useEffect, useState } from "react";
import { ApiError, API_BASE, listAgents } from "./api/client";
import type { AgentGenealogy } from "./api/types";
import "./App.css";

/*
 * Phase 1 connectivity proof — NOT the console. This bare layout exists only to
 * prove the whole chain works end to end: CORS → fetch → typed client → real
 * cluster data. It fetches GET /agents live and lists the real genealogy. No
 * design system, no tree, no interactions — those are Phase 2+.
 */

type LoadState =
  | { phase: "loading" }
  | { phase: "error"; message: string; status: number }
  | { phase: "ready"; agents: AgentGenealogy[]; count: number };

function App() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    listAgents()
      .then((res) => {
        if (!cancelled) {
          setState({ phase: "ready", agents: res.agents, count: res.count });
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = err instanceof ApiError ? err.status : -1;
        const message = err instanceof Error ? err.message : String(err);
        setState({ phase: "error", message, status });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="proof">
      <header className="proof__head">
        <h1 className="proof__title">Lineage</h1>
        <p className="proof__sub">
          Phase 1 connectivity proof · <code>GET /agents</code> from{" "}
          <code>{API_BASE}</code>
        </p>
      </header>

      {state.phase === "loading" && (
        <p className="proof__status">Fetching genealogy…</p>
      )}

      {state.phase === "error" && (
        <div className="proof__error" role="alert">
          <strong>Could not reach the API.</strong>
          <p>
            {state.status > 0 ? `HTTP ${state.status}: ` : ""}
            {state.message}
          </p>
          <p className="proof__hint">
            Is the backend running on <code>{API_BASE}</code>? Is CORS allowing
            this origin?
          </p>
        </div>
      )}

      {state.phase === "ready" && (
        <section>
          <p className="proof__status">
            {state.count} agents live from the cluster.
          </p>
          <table className="proof__table">
            <thead>
              <tr>
                <th>gen</th>
                <th>bloodline</th>
                <th>status</th>
                <th>id</th>
                <th>parent</th>
              </tr>
            </thead>
            <tbody>
              {state.agents.map((a) => (
                <tr key={a.id}>
                  <td>{a.generation}</td>
                  <td>{a.bloodline}</td>
                  <td
                    className={
                      a.status === "alive" ? "is-alive" : "is-dead"
                    }
                  >
                    {a.status}
                  </td>
                  <td className="mono">{a.id}</td>
                  <td className="mono">{a.parent_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

export default App;
