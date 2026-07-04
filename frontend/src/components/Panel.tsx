/*
 * Panel — shared chrome for each of the three console regions: an uppercase
 * header (title + optional count) over a scrollable body. Presentational only.
 */

import type { ReactNode } from "react";
import type { Loadable } from "../hooks/useConsoleData";

export function Panel(props: {
  title: string;
  count?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel__head">
        <h2 className="panel__title">{props.title}</h2>
        {props.count !== undefined && <span className="panel__count">{props.count}</span>}
      </div>
      <div className="panel__body">{props.children}</div>
    </section>
  );
}

/**
 * Renders the loading / error branches of a Loadable and defers to `children`
 * once the data is ready. Keeps each panel's own component focused on the ready
 * state while degrading locally on failure (per-panel, never blanking the shell).
 */
export function Loaded<T>(props: {
  state: Loadable<T>;
  loadingLabel: string;
  children: (data: T) => ReactNode;
}) {
  const { state } = props;
  if (state.status === "loading") {
    return <p className="panel__note">{props.loadingLabel}</p>;
  }
  if (state.status === "error") {
    return (
      <div className="panel__note panel__note--error" role="alert">
        <strong>Could not load.</strong>
        <p>
          {state.code > 0 ? `HTTP ${state.code}: ` : ""}
          {state.message}
        </p>
      </div>
    );
  }
  return <>{props.children(state.data)}</>;
}
