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
  /** Marks this panel as the genealogy graph the startup loader's transfer line aligns to (and whose
   *  living node it pulses) at the console-ownership beat — Loader §8's `[data-lin-graph]` hook. A
   *  colourless, audit-neutral attribute: the loader reads this element's DOM bounds only, never any
   *  data, so it stays invisible to the composition/geometry guards. */
  graphAnchor?: boolean;
}) {
  return (
    <section className="panel" {...(props.graphAnchor ? { "data-lin-graph": "" } : {})}>
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
