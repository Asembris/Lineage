/*
 * consistencyStream — lifecycle-safe consumer of GET /demo/consistency/stream.
 *
 * WHY NOT EventSource. This endpoint is DESTRUCTIVE: every request truncates + reseeds the
 * whole cluster and runs a real per-holder invalidation to completion. A native
 * `new EventSource(url)` auto-reconnects on any error AND after the server closes the stream
 * (which it does right after `summary`) — so a stale tab would silently re-wipe the cluster on
 * a loop, exactly the failure NOTES.md records happening once. So we drive it with fetch +
 * ReadableStream instead:
 *   - opens ONLY when this function is called (the caller gates that behind an explicit,
 *     confirmed user action — never on mount / route entry),
 *   - runs exactly ONCE and NEVER reconnects: `summary` and `busy` are terminal → we abort the
 *     fetch; a network error or a premature server close ends the run, it does not retry,
 *   - is cancelable via the returned `stop()` (the frontend's Stop button AND component-unmount
 *     cleanup both call it) — abort closes the TCP connection, which the backend detects
 *     (GeneratorExit) and uses to release its single-flight guard (confirmed empirically, see
 *     NOTES "Frontend Phase 4" — mid-drain abort releases the guard cleanly),
 *   - has a silence watchdog: reseed latency is variable (~2s clean, up to ~30s when CRDB
 *     schema-change jobs are settling), so we do NOT hard-timeout the reseed. Instead any
 *     received chunk — including sse-starlette's 15s `: ping` keep-alives — resets a 20s
 *     watchdog; only true silence (a dead connection) trips it. A live-but-reseeding stream
 *     keeps itself alive via pings and is never falsely errored.
 *
 * The `:`-prefixed comment lines (pings) are parsed for liveness but never dispatched as events.
 */

import { API_BASE } from "../api/client";
import type {
  ConsistencyBusyEvent,
  ConsistencySampleEvent,
  ConsistencyStartEvent,
  ConsistencySummaryEvent,
} from "../api/types";

export interface ConsistencyStreamHandlers {
  /** Connection established; the destructive reseed has begun (no data yet). */
  onOpen?: () => void;
  onStart?: (e: ConsistencyStartEvent) => void;
  onSample?: (e: ConsistencySampleEvent) => void;
  /** Terminal — the stream is closed after this. */
  onSummary?: (e: ConsistencySummaryEvent) => void;
  /** Terminal — another stream is already in flight; nothing was reseeded. */
  onBusy?: (e: ConsistencyBusyEvent) => void;
  /** Network / non-2xx / stall / premature close. Not fired on an explicit stop(). */
  onError?: (err: Error) => void;
}

export interface ConsistencyStreamController {
  /** Abort the stream (idempotent). Closes the connection; fires no callbacks. */
  stop: () => void;
}

/** No data (not even a keep-alive ping) for this long ⇒ the connection is dead, not reseeding. */
const STALL_MS = 20_000;

function toError(cause: unknown, fallback: string): Error {
  if (cause instanceof Error) return cause;
  return new Error(`${fallback} (${String(cause)})`);
}

/**
 * Parse one SSE frame (an "event: …\ndata: …" block) and dispatch it.
 * Returns true if it was a TERMINAL event (`summary` / `busy`), so the caller stops reading.
 */
function dispatchFrame(frame: string, h: ConsistencyStreamHandlers): boolean {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank or comment (ping) — ignore
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return false; // e.g. a lone ping frame — no payload to dispatch

  let data: unknown;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    return false; // malformed data line — skip it rather than crash the reader
  }

  switch (event) {
    case "start":
      h.onStart?.(data as ConsistencyStartEvent);
      return false;
    case "sample":
      h.onSample?.(data as ConsistencySampleEvent);
      return false;
    case "summary":
      h.onSummary?.(data as ConsistencySummaryEvent);
      return true; // terminal
    case "busy":
      h.onBusy?.(data as ConsistencyBusyEvent);
      return true; // terminal
    default:
      return false;
  }
}

/**
 * Open the consistency stream and drive `handlers` from it. Call once per run; the returned
 * controller's `stop()` aborts it. Never reconnects.
 */
export function runConsistencyStream(
  handlers: ConsistencyStreamHandlers,
): ConsistencyStreamController {
  const ac = new AbortController();
  let stallTimer: ReturnType<typeof setTimeout> | undefined;

  const clearStall = () => {
    if (stallTimer !== undefined) {
      clearTimeout(stallTimer);
      stallTimer = undefined;
    }
  };
  const armStall = () => {
    clearStall();
    stallTimer = setTimeout(() => {
      ac.abort();
      handlers.onError?.(
        new Error(
          "stream stalled — no data for 20s. The cluster may be mid-reseed from a prior run; retry shortly.",
        ),
      );
    }, STALL_MS);
  };

  void (async () => {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/demo/consistency/stream`, {
        headers: { Accept: "text/event-stream" },
        signal: ac.signal,
      });
    } catch (err) {
      if (ac.signal.aborted) return; // explicit stop() before the response — silent
      handlers.onError?.(toError(err, `cannot reach the stream at ${API_BASE}`));
      return;
    }

    if (!res.ok || !res.body) {
      handlers.onError?.(new Error(`stream failed: HTTP ${res.status} ${res.statusText}`));
      return;
    }

    handlers.onOpen?.();
    armStall();

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        armStall(); // any chunk (incl. a keep-alive ping) proves the connection is live
        buf += decoder.decode(value, { stream: true }).replace(/\r/g, ""); // normalize CRLF
        let sep: number;
        while ((sep = buf.indexOf("\n\n")) !== -1) {
          const frame = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          if (dispatchFrame(frame, handlers)) {
            // Terminal event: close the connection so the server reaps its tasks. NEVER reconnect.
            clearStall();
            ac.abort();
            return;
          }
        }
      }
      // Stream ended with no terminal frame — server closed early. End the run; do NOT retry.
      clearStall();
      if (!ac.signal.aborted) {
        handlers.onError?.(new Error("stream ended before a summary"));
      }
    } catch (err) {
      clearStall();
      if (ac.signal.aborted) return; // explicit stop() or our terminal abort — not an error
      handlers.onError?.(toError(err, "stream read failed"));
    }
  })();

  return {
    stop: () => {
      clearStall();
      ac.abort();
    },
  };
}
