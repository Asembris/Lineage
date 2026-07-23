# DESIGN_PORT_DIAGNOSIS.md — the Claude-Design console re-skin, audited before a line moves

**Status: READ-ONLY DIAGNOSIS.** This document is the *only* file this pass writes. No code, no
component, no config, no token was changed. Nothing was ported. Extracted assets live in a scratch
directory (path in §1), never in `frontend/src`.

**Subject:** `Lineage - Supervisor Console.html` (806,459 bytes) in the repo root — a self-extracting
Claude-Design ("DC") bundle. It was decoded in full and every claim below is measured against the
decoded artifacts and our real code, not the wrapper.

---

## STEP 0 — what the bundle actually is (verified, not assumed)

The file is a **self-extracting bundle**, exactly as described. It carries:

- `<script type="__bundler/manifest">` — a JSON map of `uuid → {mime, compressed, data(base64)}`,
  603,212 chars, **21 entries**. Each entry base64-decoded, and gzip-decompressed where
  `compressed:true`.
- `<script type="__bundler/template">` — a JSON-encoded string (183,003 chars) that decodes to the
  **179,675-char page** (`decoded/template.html`, 2,274 lines).

Decoder script and all outputs:
`…/scratchpad/unpack.py`, `…/scratchpad/decoded/`.

### The critical structural fact — CONFIRMED

The decoded template is a Claude-Design "DC" artifact, not a portable web page:

- Root is `<x-dc>` with `<sc-if>` (11×), `<sc-for>` (2×), and `{{ binding }}` interpolation
  throughout the markup (`template.html` lines 495–625).
- The logic is a single **`class Component extends DCLogic`** block, **lines 627–2272 (~1,645
  lines)** — matching the expected ~1,647. It renders via `React.createElement` (aliased `h`) and is
  driven by a **proprietary generated runtime**: manifest asset `1b7109f3…` opens with
  `// GENERATED from dc-runtime/src/*.ts — do not edit. Rebuild with … bun run build`.
- The bundle ships **its own React + ReactDOM** (`90f1a344…` = `react.production.min.js`,
  `162b6bbf…` = `react-dom.production.min.js`) plus the DC runtime — a complete, self-contained app.

Our app is **React 19 + Vite + TypeScript** with its own component tree, typed API client, and CI
guards. **They share no runtime.** `<x-dc>`/`<sc-if>`/`{{…}}`/`DCLogic` have no meaning in our
toolchain, and the bundled runtime is a black box we will not adopt or vendor.

> **Therefore: NO file in this bundle drops into `frontend/src`.** The port is a
> **re-implementation** of the *visual and behavioral design* into our existing React components,
> using the decoded template as a **specification**. The good news buried in that constraint: the DC
> logic is plain, readable `React.createElement` with inline-style objects, so it is an unusually
> precise spec — exact hex, exact px, exact easing curves, exact keyframes are all right there to
> copy as *values*. We copy the design; we import nothing.

---

## 1 · ASSET INVENTORY

21 manifest assets. Extracted and named for handoff in
`…/scratchpad/handoff/` (logo + emblem + `fonts/`).

### (a) PORTABLE AS-IS — real files usable directly

| Asset | uuid | Bytes (decoded) | What it is |
|---|---|---|---|
| **Logo / emblem SVG** | `5f226315…` | 896 | `viewBox 0 0 120 150`, `<title>Lineage emblem</title>`. A crest/shield with inherited channels and a central "attestation spine" (teal diamond over a vertical spine). Clean, hand-authored, scalable. **The single most portable asset.** → `handoff/lineage-logo.svg` |
| **Emblem PNG** | `ed060305…` | 52,003 | 228×303 RGBA raster of the *same* crest (transparent-cut). Used by the DC for the nav "mark" at small sizes. → `handoff/lineage-emblem.png` |
| **Webfonts (woff2)** | 16 files | 25.9 KB–1.6 KB | Real subsetted woff2 for **Inter** (400/500/600), **JetBrains Mono** (400/500/600), **Space Grotesk** (400/500/600/700). → `handoff/fonts/` |

**Colour note on the emblem:** the SVG uses `#F2F0E8` (warm off-white) for the shield strokes and
**`#53B8C6` (teal)** for the attestation spine/diamond. **Teal is not in our palette** (`tokens.css`
has no teal token). This is a *new brand accent introduced by the logo itself* — see §3.

### (b) SPEC TO REIMPLEMENT — the design, not a droppable file

| Asset | uuid / location | What it is |
|---|---|---|
| DC template markup | `decoded/template.html` 495–625 | The console layout: header/nav, three-region console grid, feed, genealogy SVG, inspector, evidence view, ledger, consistency view — as `<x-dc>`/`{{binding}}` markup. **A visual spec.** |
| DC `Component` logic | `template.html` 627–2272 | All render methods (`renderTree`, `renderSpark`, `renderEvidence`, `renderConsistency`, `renderIntro`, …), the palette `C={…}`, the `state` object, the loader timeline. **A behavioral spec** — read it, reimplement in our components. |
| `@font-face` + reset CSS | `template.html` 13–490 | 51 `@font-face` blocks (resolving to the 16 woff2 above) + scrollbar/selection styling + 5 `@keyframes` + reduced-motion guard. **Directly adaptable CSS** (values copy 1:1; selectors get rewritten for our DOM). |

### (c) UNUSABLE — the DC runtime and its bundled libs

| Asset | uuid | Why unusable |
|---|---|---|
| DC runtime | `1b7109f3…` (66 KB) | Proprietary generated runtime for `DCLogic`/`<x-dc>`/`<sc-*>`. Do **not** vendor. |
| React (prod min) | `90f1a344…` (10.7 KB) | We already run React 19; a second copy is pointless and conflicting. |
| ReactDOM (prod min) | `162b6bbf…` (131 KB) | Same. |

### The fonts, specifically (this closes a known gap)

`FRONTEND.md` and `tokens.css` declare Space Grotesk / Inter / JetBrains Mono **but only as a
fallback stack** — the real faces were never wired (`NOTES.md:493`: "token FALLBACK stack … not yet
self-hosted"). **The bundle embeds all three, so self-hosting is now trivial:**

- Drop the 16 woff2 into `frontend/public/fonts/` (or `src/assets/fonts/`), paste the bundle's
  `@font-face` blocks (rewriting `url("uuid")` → the real paths), done. No network, no Google Fonts
  CDN, no CSP change, no new dependency.
- **What changes visually:** today every heading, label, and mono number renders in the *fallback*
  (`system-ui` / `ui-monospace`). Wiring the real faces gives the intended typographic identity —
  Space Grotesk's geometric caps on headers, Inter's tighter body metrics, JetBrains Mono's true
  tabular figures on every ID/amount/confidence. This is a **real, visible quality jump for near-zero
  risk** and is frontend-only (no cluster contact). It is arguably the highest value-to-effort item
  in the whole package after the loader.
- Housekeeping: the 16 physical files cover multiple unicode-range subsets (latin, latin-ext, greek,
  cyrillic, …). We can ship all 16 or prune to latin/latin-ext to save ~100 KB; either is fine.

---

## 2 · ⚠️ THE DATA-FIDELITY AUDIT — highest risk, done first

**The governing rule (`FRONTEND.md` "Non-negotiables"):** *"Every number, ID, and timestamp shown
must come from a real API response. … do not fabricate a plausible-looking placeholder and move on."*

The DC `Component` hardcodes an entire demo dataset in its `state`, `constructor`, `buildClaims()`
and `buildInterro()`. Some of it **matches** our real system; some is **wrong in a way that looks
right at a glance**; some is **pure DC invention**. Every visual we port must keep pulling its data
from our live endpoints — **not one DC literal may enter `frontend/src`.**

### The three the user pre-flagged — CONFIRMED, and here is the proof

| DC value (file:line) | Our REAL value (source) | Verdict |
|---|---|---|
| belief ids `898ad0e5` **and** `b7f2c1a9` (`template.html:652,654`) | crimson `898ad0e5` ✅ real; azure belief is **`ea4f9135`** (`frontend/tests-e2e/fixtures/console.json`, 406 refs; `migrations/0006`) | `898ad0e5` **match** · **`b7f2c1a9` MISMATCH** (DC-invented azure id) |
| formed dates `2025-11-02` / `2025-12-14` (`:652,654`) | **`2024-05-12`** (fixture, 6 refs) | **MISMATCH** (both) |
| windows curve `0.924, 0.902, 0.861, 0.803, 0.717, 0.556, 0.624, 0.528` (`:657-666`) | **`0.924, 0.952, 0.876, 0.852, 0.724, 0.556, 0.624, 0.528`** (fixture confidence sequence; `NOTES.md:754`) | **MISMATCH in the middle.** Endpoints (0.924 / 0.528) and the gen-5→6 dip (0.556 → 0.624) match; **gen 1–4 differ.** DC is smoothly monotone-declining; **our real curve RISES at gen-1 (0.924→0.952) before falling.** A curve that reads "right" and is wrong. |

### The full enumeration (every hardcoded value found)

**Matches — safe to render, but STILL must come from the API, never copied as a literal:**

| DC literal | Our real value | Note |
|---|---|---|
| fleet `24` agents; `2/2` beliefs active | 24 / 2 | `template.html:588,591,703,704` — match, but read live via `/agents`, `/beliefs`. |
| feed totals `5,500` / `4,000` / `1,500` | 4,000 card + 1,500 AML = 5,500 | `:521-523` — match; read from `/decisions` counts. |
| census `MATCH 57`, `INCONCLUSIVE 980`, `CONCLUSIVE_NO self-loop 447` + `closed-search 16` (= 1,500) | identical (README honesty ledger) | `:763-781` — **faithful** to our real seam census. |
| `65.3%` could-not-determine; `252 / 300` laundering approved | identical | `:709,711,769,776,783,789` — match. |
| present confidence `0.528`, CI `[0.466, 0.589]` | `0.528`, `[0.466, 0.589]` (`NOTES.md:3496`) | gen-7 window `lo 0.466 / hi 0.589` (`:665`) matches our real CI. |
| certificate hash `1e40b7a72fe1796cc91fa49bd119e1f2…c393ff` | same hash (README, `MCP_SESSION.md`) | `:712,730` — **real** hash, matches. |

**MISMATCH / DC-only invention — MUST NOT appear anywhere in `frontend/src`:**

| DC-only literal | Where (`template.html`) | Why it's poison |
|---|---|---|
| `b7f2c1a9` | 654, 704 | invented azure belief id (real: `ea4f9135`). |
| `0.902`, `0.861`, `0.803`, `0.717` | 659-662 | wrong middle-curve confidences. |
| `2025-11-02`, `2025-12-14` | 652, 654 | wrong formed dates (real: `2024-05-12`). |
| window `n` values `1240,980,760,540,410,300,240,190` | 657-666 | DC-fabricated sample sizes; ours are re-aggregated from `decisions`, not persisted. |
| `crimson-5b` (living holder) | 707 | invented agent handle. |
| `s.okonkwo` (supervisor of record) | 715 | invented human name; ours is a UUID actor (`SUPERVISOR_ACTOR`). |
| `zero-witness 85.8%` / `1,287` | 710, 782, 790 | DC-computed, not a figure any endpoint of ours serves. |
| IBANs `DE89·3704·••••·4471`, `GB29·NWBK·••••·0088`, +8 more; banks (Deutsche Bank, NatWest, …) | 743-752 | fabricated pretty accounts; our AML rows are masked IBM-synthetic ids. |
| amount `$1,904,789.00`; per-hop amounts; edge relations ("nostro settlement" …) | 694, 755-758 | invented transaction specifics. |
| `txn_5f2c81` (loader label) | 1002 | invented txn id. |
| all `clm_00a1…clm_0131` ledger ids; `blake3·…` seals; `wk_…`/`rt_01` attestation keys; HLC `7f3c·001a4b2e`; `s3://lineage-certs/898ad0e5/2026-07-19.json` | 703-736 | the DC ledger is a hardcoded demo; **our HonestyLedger reads live cluster rows** (`HonestyLedger.tsx`). |

### Proposed safeguard — a `doc_guard`-shaped literal tripwire (feasible, cheap, offline)

Yes, a guard is feasible and worth it. It makes the leak **unrepresentable** rather than remembered:

- **Mechanism:** a Node/regex script (call it `frontend/scripts/no-fabricated-data.mjs`) greps every
  `frontend/src/**/*.{ts,tsx,css}` for a frozen denylist of DC-only literals and exits non-zero on
  any hit. Denylist (exact strings): `b7f2c1a9`, `0.902`, `0.861`, `0.803`, `0.717`, `2025-11-02`,
  `2025-12-14`, `crimson-5b`, `s.okonkwo`, `85.8%`, `1287` / `1,287`, `txn_5f2c81`, the fabricated
  IBAN prefixes (`DE89·3704`, `GB29·NWBK`, …), `1904789` / `1,904,789`, `7f3c·001a4b2e`, the `clm_00`
  id prefix.
- **Cost:** pure string scan, offline, no cluster, sub-second. Wire it into
  `.github/workflows/frontend-ci.yml` (which already runs the composition guard) so it fires on
  exactly the frontend pushes that could introduce a leak.
- **Numeric caution:** bare decimals like `0.717` *could* appear innocently in unrelated math, so
  anchor the numeric entries to context (e.g. require them inside a `.ts`/`.tsx` string/array
  literal, or only scan data/fixture files) to avoid false alarms. The ID/date/name entries
  (`b7f2c1a9`, `2025-11-02`, `s.okonkwo`) are unambiguous and need no anchoring.
- **Self-test (match the house style of `composition-guard.mjs`):** ship a committed fixture line
  containing one denied literal and assert the guard catches it every run — "a guard that cannot be
  shown to fail is theatre."

This is directly in the spirit of the project's existing `@doc_guard` / composition-guard / geometry-guard
discipline: **prove the property, don't trust memory.**

---

## 3 · PALETTE + MOTION CHECK

### Palette — byte-identical (verified all 12)

The DC declares `C = { … }` at `template.html:628`. Every token equals `tokens.css` exactly:

| Token | DC `C` | `tokens.css` | |
|---|---|---|---|
| void | `#0A0E14` | `#0a0e14` | ✅ |
| surface | `#121821` | `#121821` | ✅ |
| surface2 | `#1A2230` | `#1a2230` | ✅ |
| line | `#243040` | `#243040` | ✅ |
| ash | `#5A6678` | `#5a6678` | ✅ |
| ghost | `#8A94A6` | `#8a94a6` | ✅ |
| bone | `#C4CDD8` | `#c4cdd8` | ✅ |
| alive | `#3FE0A8` | `#3fe0a8` | ✅ |
| trace | `#E0A23F` | `#e0a23f` | ✅ |
| origin | `#E07B3F` | `#e07b3f` | ✅ |
| alert | `#E5484D` | `#e5484d` | ✅ |
| alertDim | `#3A1518` | `#3a1518` | ✅ |

**Zero drift.** (Case only.) The console body reuses these same hexes inline.

**The one addition:** the **loader and the emblem introduce non-palette colours** — teal `#53B8C6`
(emblem spine) and `#42DDB3` / amber `#F2A43A` / off-white `#F2F0E8` in the intro (`template.html:980`,
`1011`). These are **loader/brand-only**, never used in the console body. If we port the loader we
either (a) adopt teal as a deliberate *brand* accent scoped to the logo/loader only, or (b) recolor
the spine to `--alive`/`--bone`. Recommendation: keep teal **only** inside the emblem+loader (it
never touches the reserved-warmth surfaces), and add it as a named `--brand-*` token so it can't
leak into signal usage.

### Motion — inside our vocabulary, with two things to watch

Our motion law (`lib/motion.ts`, `NOTES.md` Phase 6): **tweens only, no springs; animate
transform / opacity / pathLength only; colours SNAP, never lerp.**

The DC's five `@keyframes` (`template.html:482-486`):

| Keyframe | Animates | Verdict |
|---|---|---|
| `lin-live` | `opacity` + `transform:scale` (living-node halo pulse) | ✅ transform/opacity |
| `lin-drawin` | `opacity` + `translateX` | ✅ |
| `lin-sweepx` | `transform:scaleX` + `opacity` | ✅ |
| `lin-settle` | `transform:scale` + `opacity` (bloom-in) | ✅ |
| `lin-armed` | `opacity` (armed-holder pulse) | ✅ |

All tweens, all transform/opacity. The loader's Web-Animations timeline is likewise **transform /
opacity / strokeDashoffset(pathLength)** with `cubic-bezier` easings — tweens, no springs (§6). The
staleness sparkline animates `strokeDashoffset` + a clip-rect width (`renderSpark`, `:1383-1409`) —
pathLength/transform, fine.

**Two watch-items (neither a blocker):**

1. **`frameTo()` camera framing** (`template.html:673-688`): a `requestAnimationFrame` loop doing
   `v[i] += (target - v[i]) * 0.16` on the genealogy **viewBox** — an *asymptotic ease-out*, not a
   fixed-duration tween, with a 1,400 ms safety cap and a reduced-motion snap. It animates **geometry
   (SVG viewBox pan/zoom), never colour**, so it doesn't violate the colour-snap rule, but it is a
   **new motion idiom** (a "camera") not in our `DUR`/`EASE` set and is a **new feature** (§4), not
   polish. If ported, give it a real duration/easing consistent with `lib/motion.ts` or keep it as a
   clearly-scoped exception documented like the 3D scene's rAF loop.
2. **The sparkline gradient `alive → alert`** (`renderSpark`, `:1390-1391`): a **static**
   `linearGradient` along the curve (green when-formed → red now). This is a *spatial* gradient, not a
   colour **lerp over time**, so it obeys "colours snap" — but note it puts `--alert` on the
   time-travel curve. That is the staleness/decay surface (allowed), **not** a witness or the
   evidence surface (see §5). Keep it off evidence.

**No springs, no colour interpolation, no foreign durations that break the vocabulary.** Where the
DC uses bespoke ms values (loader beats, `frameTo` 0.16), the port should route them through
`lib/motion.ts` rather than reintroducing bare literals (the exact drift Phase 6 fixed).

---

## 4 · WHAT THE DESIGN CHANGES, SURFACE BY SURFACE

The DC is a **more finished, denser, more cinematic** version of the same three-region console + the
same auxiliary views. Classification per affordance: **(i) pure visual polish**, **(ii) NEW
interaction/feature**, **(iii) would BREAK an invariant**.

| Surface | What's visually different & worth porting | New affordances (DC `state`) | Class |
|---|---|---|---|
| **Header / nav** | Real emblem mark + wordmark; nav as an underlined indicator that *slides* between surfaces (`layoutNavInd`, `navHover`, `brandHover`); a "fleet" popover. | `fleetOpen`, `navHover`, `brandHover`, `focusMode` | (i) mark/wordmark + hover polish; (ii) sliding indicator, fleet popover, focusMode |
| **Decision feed** | Tighter rows; per-row confidence bar; **collapsible rail** (vertical label when collapsed); `card/aml/all` chips with counts; "see why →" affordance (we already have this). | `feedCollapsed`, `filter`, `shown` | (i) row styling; (ii) collapse-to-rail |
| **Genealogy tree** | Bloodline **bands** (CRIMSON/AZURE) + **GEN 0–7 gridlines**; living-node halo pulse; hover→ancestry preview dim; **camera framing** that zooms to the selected bloodline; "⤢ fit all". | `hoverNode`, `vb/vbTarget/frameTo` | (i) bands/gridlines/halo; (ii) camera framing + hover-preview dimming |
| **Inspector** | Fleet stat grid (24 / alive / 5,500 / 2·2); belief catalog cards with status pills. | — | (i) polish (our Inspector already covers this) |
| **Investigation / Trace** | Warm trace draw over cold tree with origin **bloom** (`lin-settle`, `bloomP`); dashed living-holder rings. | `traceMs, tracing, traced, ringMs` | (i) — matches our signature trace; port the bloom/ring polish |
| **Time-travel** | The **staleness sparkline** with a Wilson ribbon + when-formed/present dots + a left→right sweep; formed/present toggle. | `timeMode`, `sparkMs` | (i) sparkline polish (we have a curve; theirs is more finished) |
| **Invalidate gate** | Armed-holder `--alert` pulse on the tree; corrected `--alive` ring on commit; a confirm gate. | `gate, invalidated, closeMs` | (i) tree overlay polish — **but gate geometry is invariant-bound (§5)** |
| **Evidence / INTERROGATION** | A full **5-phase procedure**: subject-lock → witness inspector (10-hop ring, ◄►/keyboard) → census (four legitimate read-states) → evidence-vacuum meters → finding-assembly. Explicit "NOT USED / label withheld" card. | `ivPhase, ivMax, ivWitness, ivCensus, censusMs, vacuumMs, findMs, ivLockMs, ivHopHover` | **(ii) a substantially richer feature** than our current `AmlConsole`/`WitnessGeometry`; oracle-boundary-bound (§5) |
| **Ledger** | Searchable/filterable claims table; per-claim provenance **trail** with an ANOMALOUS break; escalate/attest drawers. | `ledgerSel, ledgerFilter, ledgerQuery, provMs, escalate, attestOpen, ledgerEnterMs, reconMs, ledgerHover, anomStep` | (ii) search/filter/escalate/attest are **new**; our HonestyLedger reads live rows and shows top-line verdict only |
| **Consistency demo** | 2D timeline scrubber of the atomic-vs-eventual proof with pause-at-torn, holder/observer selection. | `cPhase, cT, cMode, cPauseTorn, cHolderSel, cObsSel` | (i)/(ii) — richer controls over the same real SSE proof |
| **Loader (intro)** | Full startup cinematic (§6). | `intro` | (ii) new, self-contained, high value |

**Net:** most *look* is class-(i) polish that ports cleanly onto our existing components. The
genuinely **new** things are: the loader, the camera-framing tree, the 5-phase interrogation, the
searchable ledger with attestation drawers, and the consistency scrubber controls. Several new items
(escalate/attest, `focusMode`, ledger search) are **features with no backing endpoint** — building
them would either need new API surface or would tempt fabricated data. **Those wait or are cut; they
are not part of a re-skin.**

---

## 5 · THE INVARIANT CHECK — what the port MUST NOT break

Verified against the real guards (`frontend/scripts/composition-guard.mjs`,
`frontend/tests-e2e/geometry.spec.ts`, `tokens.css`, `App.tsx`). **The invariants win; guards do not
relax.**

### Oracle boundary — `is_fraud` never co-visible with a witness; evidence renders ALONE
- **Our enforcement:** `App.tsx` renders exactly one body arm from a discriminated `View` union; the
  `aml` arm mounts `<AmlConsole>` **alone** (feed + Inspector unmount) and is handed only a bare
  `UUID`. `composition-guard.mjs` walks the TS type graph and fails CI if any AUDIT-coloured
  type/component (`Decision`, `is_fraud`, `verdict`, `DecisionFeed`, `Investigation`) reaches or sits
  adjacent to the evidence surface (checks A/composition, B/channel, C/adjacency), and re-proves
  itself on committed fixtures each run.
- **Does the DC respect it?** **Yes, structurally.** The DC evidence view is a **separate full-screen
  surface** (`isEvidence` arm, `template.html:618`), not a panel beside the feed. Its phase-0 card
  *explicitly* renders a "**NOT USED** — account identity and any semantic label are withheld"
  (`:1700-1702`), and no `is_fraud`/`is_laundering` appears anywhere in the evidence render
  (`renderEvidence`, `:1682-1795`). The feed's per-row `FRAUD` badge (`:537`) is on the **audit**
  surface (the feed), which is legitimate and matches our own.
- **Port rule:** map the DC's 5-phase interrogation into our **evidence modules only**
  (`AmlConsole.tsx`, `WitnessGeometry.tsx`, `useInterrogation.ts`, `lib/basis.ts`,
  `lib/witnessGeometry.ts` — the `EVIDENCE_MODULES` list). It must remain derivable from
  `/interrogate` alone. **One concrete fix:** the DC's census reconciliation total turns
  `C.alert` if the arithmetic doesn't sum to 1,500 (`:1753`). That is `--alert` on the evidence
  surface — forbidden. In the port, use `--ash`/`--ghost` for that self-check, never `--alert`.

### Reserved warmth — `--trace` / `--origin` ONLY on the belief Trace and its ignited origin
- **Does the DC respect it?** **Yes.** A full grep of `C.trace` / `C.origin` / `#E0A23F` / `#E07B3F`
  shows them **only** in: the tree/trace render (trace-chain edges + arrived/origin nodes,
  `:1355-1366, 1483-1492`), the legend (`:576-577`), and the loader's backward-trace draw
  (`:997,1008` — amber `#F2A43A`, the loader's own tint). **Never** on hover, buttons, the evidence
  ring, or 3D. Hover uses `--bone` (`:1367`); armed uses `--alert`; living uses `--alive`. Clean.
- **Port rule:** preserve this exactly. Do not let the loader's amber bleed into console hover/button
  styling.

### `--alert` never on a witness / never on the evidence surface
- DC uses `--alert` on: the feed FRAUD badge (audit surface ✅), armed invalidation holders on the
  tree (✅ invalidation signal), the eventual/split/leaked-fraud markers in the consistency demo (✅),
  the sparkline's "now" endpoint (time-travel/decay surface — allowed), and the census-total
  self-check (`:1753` — **the one to fix**, above). No `--alert` on a witness node. **Port rule:**
  keep it off the evidence/witness surface (fix the one census case).

### Geometry guard (Playwright) + composition guard — would the DC DOM trip them?
- **Composition guard:** the DC's separate-surface structure is *compatible* — but the guard checks
  **our** TS types, so any port must keep the richer interrogation inside the pinned evidence modules
  with colourless props (a bare `txnId`). If a port added, say, an `EvidencePane` that took a
  `Decision` prop or imported `listDecisions`, the build fails. **The port must not.**
- **Geometry guard:** asserts, by *rendering*, that the invalidate **Confirm** button never shares
  the **Arm** button's footprint and **Cancel** does (`.kill__actions` stacks column, Cancel
  full-width last). The DC's invalidate gate is a different layout; **the port must preserve the
  stacked-column geometry** (Confirm ∩ arm = ∅, Cancel ∩ arm ≠ ∅), or the 40-test geometry suite
  fails. Do not adopt a side-by-side arm/confirm row.

### prefers-reduced-motion collapses to the identical final frame
- DC honors it: a global `@media (prefers-reduced-motion: reduce){ *{animation:none;transition:none} }`
  (`:489`), a reduced loader path (`introReducedPath`, §6), and `frameTo` snapping under
  `this.reduced()`. **Port rule:** match our existing discipline — reduced-motion snaps to the same
  end-state (`NOTES.md`: Trace's load-bearing reduced path, 3D snap). Verify with the existing
  Playwright reduced-motion dimension.

---

## 6 · THE LOADER / STARTUP ANIMATION — the cleanest, highest-impact win

`renderIntro()` + `runIntroTimeline()` (`template.html:976-1024`, `859-974`). It is a **faithful DOM
/ CSS / SVG reproduction of a golden reference** (`lineage_loader_reference.html`), driven by the
**Web Animations API** (`node.animate`), fully self-contained.

### The sequence (motion path, ~4.2 s total)
1. **Decision lock** (0–0.7 s): four provenance "strata" planes register in 3-D (`translate3d` +
   `rotateY`, opacity 0→~0.4); the subject decision **diamond** scales/rotates in.
2. **Provenance separation** (0.68 s+): three labels fade up — `ORIGIN crimson-0`, `INHERITED gen 3 ·
   belief 898ad0e5`, `SELECTED DECISION txn_5f2c81 · 0.528`. *(Note: `898ad0e5` real; `txn_5f2c81`
   invented — but the loader is decorative and shows no live data, so this is cosmetic, not a data
   leak. Still, prefer neutral/real strings if kept.)*
3. **Backward trace to origin** (1.2–2.4 s): an amber path draws via `strokeDashoffset` (pathLength) —
   the same "warmth spreading backward" as our signature Trace.
4. **Origin resolved** (2.34 s): the origin diamond blooms in.
5. **Evidence compresses away** (2.55 s+): all HUD elements fade/scale to 0.85.
6. **Identity seal + brand** (2.82 s): the **exact emblem SVG** (`assets/lineage_logo.svg`) scales in;
   `LINEAGE` / `SUPERVISOR CONSOLE` wordmark fades up.
7. **Console ownership** (3.46 s): a teal **handoff line** measures the real genealogy panel
   (`[data-lin-graph]` bounds) and sweeps to it; `revealConsole()` fades the backdrop and pulses the
   living node's brightness. Fires a `lineage-loader-complete` CustomEvent, then disposes.

**Controls & safety:** click/key anywhere **skips** to the resolved handoff (`_introSkip`); a 5.2 s
watchdog and a 4.23 s finisher guarantee it never traps the user; `disposeIntro()` cancels every
animation (safe to replay).

**Reduced-motion path** (`introReducedPath`, `:946-956`): ~500 ms — exact logo → transfer line →
hand ownership, **no camera travel**, collapsing to the same final frame.

**Vocabulary check:** all transform / opacity / strokeDashoffset with `cubic-bezier` tweens — **no
springs, no colour lerp.** Inside our motion law (the only new colours are the loader's own teal/amber
tints, scoped to the overlay).

### As a deliverable: self-contained, ships independently
- **What it touches:** app **entry/mount only**. It is a `position:fixed` overlay (`zIndex 400`,
  `pointer-events:none`, `aria-hidden`) rendered above the app, gated by an `intro` flag, that
  removes itself and emits an event. It touches **no console internals** and reads **no data**.
- **What porting takes:** a single `<Loader/>` React component (WAAPI or framer-motion tweens, our
  choice — WAAPI ports 1:1 from the spec), mounted in `App` behind an `intro` state that flips false
  on completion or skip; the emblem SVG inlined or imported; reduced-motion via our existing
  `useReducedMotion`. Half a day, frontend-only, no cluster, no guard interaction (it mounts no
  audit/evidence component).
- **Verdict:** **highest impact / lowest risk in the package.** Ship it **first and standalone**,
  independent of any console re-skin.

---

## 7 · THE HONEST PORT PLAN — sequenced, reversible, not big-bang

Every slice below is **frontend-only** (offline push, **no cluster wipe**, no backend touch) unless
noted. Ordered safest-first; each names the guards that must stay green. Ranked by visual-impact ÷
risk.

| # | Slice | What changes | Guards that must stay green | Impact/Risk |
|---|---|---|---|---|
| **S1** | **Self-host fonts** | Add 16 woff2 + `@font-face`; the fallback stack becomes real faces. | frontend-ci build/lint (typecheck unaffected). | ★★★★ / very low |
| **S2** | **Real logo + wordmark in header** | Replace text-only brand with the emblem SVG + Space Grotesk wordmark; add scoped `--brand-teal`. | composition guard (header is audit-neutral); no `--trace/--origin/--alert` misuse. | ★★★ / very low |
| **S3** | **The loader** (§6) | New `<Loader/>` overlay at app entry; reduced-motion path; skip. | reduced-motion Playwright dimension; no audit/evidence mount. | ★★★★ / low |
| **S4** | **Console visual polish** (feed rows, inspector stat grid, tree bands/gridlines/halo, sparkline finish) | Restyle existing components to DC values; route new timings through `lib/motion.ts`. | geometry guard (invalidate gate untouched), composition guard, colour-snap/reserved-warmth. | ★★★ / low-med |
| **S5** | **`no-fabricated-data.mjs` guard** (§2) | Add the literal tripwire + self-test to frontend-ci. | its own fixture self-test. | (safety) / very low — **do this alongside S4, before any interrogation/ledger port** |
| **S6** | **Trace/Invalidate motion polish** | Port origin bloom (`lin-settle`), dashed holder rings, armed/corrected overlays. | **geometry guard** (Confirm∩arm=∅, Cancel∩arm≠∅ — do NOT adopt a side-by-side row); reserved-warmth. | ★★ / med |
| **S7** | **Consistency demo scrubber controls** | Add pause-at-torn / holder-observer selection over the **real** SSE. | reduced-motion; no fabricated samples (all from the live stream). | ★★ / med |
| **S8** | **Richer interrogation** (5-phase) | Reimplement into evidence modules **only**; drop DC's `--alert` census self-check. | **composition guard A/B/C** (colourless props, derivable from `/interrogate` alone); oracle boundary. | ★★★ / **high** — new feature, most guard exposure |
| **S9** | **Ledger search/filter/attest** | New affordances — **needs backing data**; several DC fields (attestation keys, `s.okonkwo`) have no endpoint. | doc/data guard; HonestyLedger stays live-row-backed. | ★ / high — **likely cut or defer** (no API; fabrication risk) |

**Reversibility:** S1–S7 are additive/restyle and revert with a `git revert`. S8/S9 are feature work
and should be separate PRs behind the guards.

**Safe BEFORE R3 (deploy):** S1, S2, S3, S5 (and S4 if time) — all frontend-only, guarded, no cluster
contact. **Should WAIT until after the gates:** S6–S9 (higher guard exposure and, for S8/S9, new
feature/data surface).

---

## 8 · THE BRUTAL BOTTOM LINE

**R3 (deploy) and R6 (video) are the only remaining hard gates, and both prior audits ranked
frontend polish below them.** That ranking is correct and this port does not change it. A full
re-skin (S6–S9) is **not worth doing before the gates** — it carries the most guard exposure
(geometry, composition, oracle boundary), and S8/S9 drift toward *new features with no backing data*,
which is exactly where the fabrication rule bites hardest.

**But a small slice captures most of the visual gain for almost none of the risk — and it is worth
doing now:**

> **The minimum high-value slice = S1 (self-host the three real fonts) + S2 (the real emblem/wordmark)
> + S3 (the loader) + S5 (the literal guard), with S4's cheapest console polish if time allows.**

That set is **entirely frontend-only, offline, no cluster wipe, and touches no invariant surface**
(the loader mounts nothing audit/evidence; fonts and logo are inert; the guard only *adds* a check).
It delivers the *felt* upgrade — real typography, a real identity mark, and a cinematic first
impression that reads well on camera for R6 — which is precisely the part a viewer and a judge
notice. The full re-skin's remaining value is incremental polish on surfaces that already work, and
it should wait until after R3/R6.

**Do not** attempt the 5-phase interrogation port (S8) or the ledger features (S9) before the gates:
they are the highest-risk, most guard-entangled, most fabrication-prone slices, and their visual
payoff per unit risk is the lowest in the package.

**The one non-negotiable that spans all of it:** the port takes **visual design only**. Every number,
id, and timestamp keeps coming from our live endpoints. The DC's `b7f2c1a9` / `0.902` / `0.861` /
`0.803` / `0.717` / `2025-11-02` / `2025-12-14` / `s.okonkwo` / invented IBANs must **never** appear
in `frontend/src` — and S5 makes that a build failure instead of a thing to remember.

---

### Appendix — extraction artifacts (scratch, not in `frontend/src`)

- Decoder: `…/scratchpad/unpack.py`
- Decoded page: `…/scratchpad/decoded/template.html` (2,274 lines) and `…/decoded/assets/` (21 raw assets)
- Handoff (named, portable): `…/scratchpad/handoff/lineage-logo.svg`,
  `…/scratchpad/handoff/lineage-emblem.png`, `…/scratchpad/handoff/fonts/` (Inter/JetBrainsMono/SpaceGrotesk woff2)
- Nothing here was copied into the repo except this file, `DESIGN_PORT_DIAGNOSIS.md`.
