/*
 * useInterrogation — the evidence layer's ONLY data entry point.
 *
 * One call: GET /aml/transactions/{id}/interrogate. It resolves every transaction and account the
 * witnesses reference, so the pane renders with no second round-trip and no per-id fan-out.
 *
 * IT DELIBERATELY FETCHES NOTHING ELSE. Not /decisions, not the belief, not the lineage — the
 * evidence surface must be renderable from /interrogate ALONE, and "alone" is meant literally: if
 * this hook ever needed a field from the audit layer, THAT would be the violation, and the
 * composition guard would fail the build for it (it walks imported symbols too, not just props).
 * A witness that needs the answer key to be drawn is not a witness.
 */

import { useEffect, useState } from "react";
import { ApiError, interrogateTransaction } from "../api/client";
import type { AmlInterrogationResponse, UUID } from "../api/types";
import type { Loadable } from "./useConsoleData";

export function useInterrogation(txnId: UUID): Loadable<AmlInterrogationResponse> {
  const [state, setState] = useState<Loadable<AmlInterrogationResponse>>({ status: "loading" });

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    interrogateTransaction(txnId)
      .then((data) => {
        if (live) setState({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (!live) return;
        const code = err instanceof ApiError ? err.status : 0;
        const message = err instanceof Error ? err.message : String(err);
        setState({ status: "error", message, code });
      });
    // A subject switch must never resolve into the previous subject's pane.
    return () => {
      live = false;
    };
  }, [txnId]);

  return state;
}
