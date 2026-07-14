/*
 * The mock. It is a REPLAY of captured cluster responses, not a hand-written imitation of them —
 * and that distinction is the whole reason this guard is allowed to exist.
 *
 * ===================== WHY A MOCK IS NOT A PROXY HERE =====================
 *
 * This project has shipped five checks that were green for their entire lives while proving
 * nothing, and every one of them was a proxy standing in for the real thing. So a mock deserves
 * the obvious suspicion: a mock that drifts from the real API lets the guard pass while the real
 * console breaks.
 *
 * Three things stop that, and none of them is a promise:
 *
 * 1. THE FIXTURES ARE CAPTURED, NEVER WRITTEN. `scripts/capture_console_fixtures.py` issues these
 *    exact requests against the real FastAPI app on the live cluster and records the bodies
 *    verbatim. Fixtures written by the guard's author test what that author thought of — that is
 *    what blinded check C of the composition guard, and the lesson was paid for.
 *
 * 2. THE FIXTURES ARE PINNED BY THE OTHER WORKFLOW. `tests/test_console_fixtures.py` REPLAYS every
 *    recorded request against the live cluster and asserts the captured body still has the same
 *    shape the live one does. It runs in the BACKEND suite — and `ci.yml`'s `paths-ignore` skips
 *    only frontend-ONLY pushes, so every backend or schema change that could break this mock DOES
 *    run the pin. Neither workflow is the one that misses:
 *
 *        frontend-ci   RUNS the geometry guard against these fixtures   (has Node)
 *        ci.yml        PINS these fixtures to the real API + cluster    (has DATABASE_URL)
 *
 * 3. THE MOCK FAILS ON ANY REQUEST IT DOES NOT KNOW. An unmocked call is recorded and fails the
 *    test — so the fixture set is ASSERTED to cover the console's real call surface, rather than
 *    assumed to. It also means this test can never silently reach a real server.
 *
 * And the invariant under test is LAYOUT, which is data-INSENSITIVE by construction: `.kill__actions`
 * is a column and `.kill__cancel` is the full-width last child, so Cancel covers the arm button's
 * footprint and Confirm sits clear above it regardless of how long the belief's rule text is (a
 * longer rule pushes Confirm further UP, which only widens the clearance). The mock's data cannot
 * make a passing geometry assertion lie about the real console.
 *
 * THE HONEST LIMIT, stated rather than buried: this pins SHAPE, not SEMANTICS. A backend field that
 * keeps its type and changes its meaning would slip through. That cannot affect geometry, which is
 * what this guard measures — but it is a limit, and it is written down.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";

/** The backend origin the console calls (`api/client.ts` → `API_BASE`, default). Nothing listens
 *  on it in CI, so even an escaped request fails closed rather than reaching something real. */
export const API_ORIGIN = "http://localhost:8000";

const FIXTURES = JSON.parse(
  readFileSync(fileURLToPath(new URL("./fixtures/console.json", import.meta.url)), "utf-8"),
) as {
  subjects: {
    belief_id: string;
    decision_id: string;
    agent_id: string;
    aml_transaction_id: string;
  };
  responses: Record<string, unknown>;
};

export const SUBJECTS = FIXTURES.subjects;

/**
 * The canonical request key. Query params SORTED; `as_of` normalized to `<ANY>`.
 *
 * `as_of` cannot be keyed on its value: TimeTravel computes `new Date(Date.now() - 20_000)` at
 * click time, so it is different on every render and no captured key could ever match. This is the
 * ONLY normalization, and `scripts/capture_console_fixtures.py` and `tests/test_console_fixtures.py`
 * apply exactly the same one — one key shape, three consumers.
 */
export function canonicalKey(rawUrl: string): string {
  const url = new URL(rawUrl);
  const params = [...url.searchParams.entries()]
    .map(([k, v]): [string, string] => [k, k === "as_of" ? "<ANY>" : v])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const qs = new URLSearchParams(params).toString();
  return qs ? `GET ${url.pathname}?${qs}` : `GET ${url.pathname}`;
}

/**
 * Serve the console from the captured cluster responses.
 *
 * Returns the list of requests the mock did NOT know about. The caller asserts it is empty: an
 * unmocked request is a hole in the fixture set, and a guard that quietly tolerates one is a guard
 * that quietly stopped covering something.
 */
export function installMock(page: Page): string[] {
  const misses: string[] = [];

  page.route(`${API_ORIGIN}/**`, async (route) => {
    const key = canonicalKey(route.request().url());
    const body = FIXTURES.responses[key];

    if (body === undefined) {
      misses.push(`${route.request().method()} ${route.request().url()}  (key: ${key})`);
      // 599, not a plausible empty 200: a mock must never invent a response. An invented empty
      // body is exactly how a mock starts drifting from the API it stands in for.
      await route.fulfill({
        status: 599,
        contentType: "application/json",
        body: JSON.stringify({ detail: `no captured fixture for ${key}` }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });

  return misses;
}
