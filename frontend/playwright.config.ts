/*
 * The browser guard's configuration.
 *
 * It runs against the REAL BUILD (`vite preview` over `dist/`), not the dev server — the thing CI
 * ships is the thing measured. In frontend-ci this step runs after `vite build`, so the artifact is
 * already there.
 *
 * FULLY OFFLINE, and that is load-bearing rather than incidental. frontend-ci has no DATABASE_URL,
 * no AWS creds and no cluster, by deliberate design ("so it can never collide with the backend CI's
 * live-cluster reseeds" — its own header). Giving it a live backend to render against would have
 * meant every frontend CSS tweak reseeding the cluster and racing backend CI, a collision this
 * project has recorded four times. TODAY FRONTEND PUSHES ARE THE ONLY PUSHES THAT DO NOT WIPE THE
 * CLUSTER, and this guard does not change that: the console's data is replayed from captured
 * fixtures (see tests-e2e/mock.ts), and an escaped request has nowhere to go — nothing listens on
 * :8000 here.
 *
 * THE VIEWPORTS ARE THE MEASURED ONES, AND HEIGHT IS A FIRST-CLASS INPUT. Every audit before the
 * Inspector-fold session pinned WIDTH and let HEIGHT float, which is precisely how a below-the-fold
 * kill-shot survived Phase 6: 1440x900 fails identically to 1280x900 because the variable is
 * HEIGHT. Both motion modes run, because `prefers-reduced-motion` changes what renders.
 */

import { defineConfig, devices } from "@playwright/test";

const PORT = 4173;

export default defineConfig({
  testDir: "./tests-e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0, // A geometry assertion is deterministic. A retry would only hide a real flake.
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],

  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },

  projects: [
    {
      name: "1280x800",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
    },
    // `reducedMotion` lives under `contextOptions`. At the top level of `use` it is an unknown key
    // that JavaScript SILENTLY IGNORES — which is exactly what it did in this guard's first green
    // run: these two projects were byte-identical duplicates of the two above, and the
    // reduced-motion dimension was FAKE AND GREEN. Nothing at runtime complained; the typecheck
    // project (tsconfig.e2e.json) caught it, and it exists because the guard's own source was
    // previously checked by nothing at all.
    {
      name: "1280x800-reduced-motion",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
        contextOptions: { reducedMotion: "reduce" },
      },
    },
    {
      name: "1440x900",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "1440x900-reduced-motion",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        contextOptions: { reducedMotion: "reduce" },
      },
    },
  ],

  webServer: {
    command: `npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
