/*
 * THE FABRICATED-DATA GUARD — the DC console's demo literals may never enter frontend/src.
 *
 * ============================== WHAT IT PROTECTS ==============================
 * FRONTEND.md's non-negotiable: "Every number, ID, and timestamp shown must come from a real API
 * response. … do not fabricate a plausible-looking placeholder and move on." The design port
 * (DESIGN_PORT_DIAGNOSIS.md §2) copies the DC bundle's VISUAL design, but that bundle hardcodes an
 * entire demo dataset — some of it right, some of it WRONG IN A WAY THAT LOOKS RIGHT: an invented
 * azure belief id (`b7f2c1a9`, real is `ea4f9135`), a smoothly-declining confidence curve whose
 * middle is fabricated (`0.902/0.861/0.803/0.717`, our real curve RISES at gen-1 then falls),
 * invented formed dates, IBANs, a supervisor name (`s.okonkwo`, ours is a UUID actor), and so on.
 *
 * A porter copying markup values 1:1 could paste one of these without noticing — the exact leak the
 * house rule forbids, in the one project whose entire thesis is that it never fabricates. This guard
 * makes that leak UNREPRESENTABLE rather than remembered: it greps every scanned source file for a
 * frozen denylist of DC-only literals and fails the build on any hit.
 *
 * ======================== WHY A SCAN IS THE RIGHT TOOL HERE ===================
 * The composition guard walks the TYPE GRAPH because the thing it protects (a witness never seeing a
 * label) is a SHAPE, invisible to text. This is the opposite: the thing forbidden IS a specific
 * string. A denylist scan is not a proxy for the property — it IS the property. What a naive scan
 * gets WRONG is false positives on innocent arithmetic (a bare `0.717` in unrelated math), so the
 * numeric entries are ANCHORED (DESIGN_PORT_DIAGNOSIS §2's own caution): the unambiguous ids/dates/
 * names match anywhere; bare decimals are digit-boundary-anchored so they cannot be a substring of a
 * longer number; bare integers must sit inside a quoted string. A guard that false-fails on correct
 * work gets deleted — the mirror-image of the vacuous check, and just as fatal.
 *
 * ===================== THE DEMONSTRATION SHIPS INSIDE THE GUARD ================
 * "A guard that cannot be shown to fail is theatre" (composition-guard.mjs). So selfTest() scans a
 * committed fixture (scripts/fixtures/fabricated-data.fixture.txt) that plants one of every denied
 * shape AND a set of anchoring-negative controls, and asserts the guard finds EXACTLY the planted
 * leaks — no fewer (it still detects), no more (it still leaves legit math alone). A gutted guard
 * fails its own fixture; an over-eager one does too.
 *
 * WHERE THIS RUNS, AND WHY. `.github/workflows/frontend-ci.yml`, alongside the composition guard —
 * the offline job that fires on exactly the frontend pushes that could introduce a leak. It needs no
 * cluster, no secrets, no browser: a sub-second string scan.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FE = path.resolve(HERE, "..");
const SRC = path.join(FE, "src");
const norm = (p) => p.replace(/\\/g, "/");
const rel = (p) => norm(path.relative(FE, p));

/* --------------------------------------------------------------------------------------------
 * THE DENYLIST. Every entry is a DC-only literal from DESIGN_PORT_DIAGNOSIS §2 ("MISMATCH / DC-only
 * invention — MUST NOT appear anywhere in frontend/src"). Three match MODES:
 *
 *   "text"      — matched anywhere. Ids, dates, names, and a comma-formatted number (`1,904,789`)
 *                 which CANNOT be a bare JS numeric literal, so matching it anywhere is already safe.
 *   "decimal"   — a bare confidence value, digit-boundary-anchored: not preceded by a digit or dot,
 *                 not followed by a digit. `0.717` matches `[0.717]` but NOT `10.717` or `0.7175`.
 *   "quoted"    — a bare integer that IS plausible innocent math, so it only counts inside a quoted
 *                 string, digit-bounded there. `"1904789"` matches; `19047890` and bare `1904789` do not.
 * ------------------------------------------------------------------------------------------ */
/*
 * A NOTE ON WHAT IS NOT HERE. DESIGN_PORT_DIAGNOSIS §2 also lists `crimson-5b`, `1,287`, and `85.8%`
 * as "DC-only invention." They are NOT, and this guard proved it: all three already live in our
 * reviewed src as REAL values, and a text scan for them fails the build on correct code — the exact
 * false-fail the diagnosis itself warns about.
 *   · `crimson-5b` is our real living branch holder (`cd75b3…`, gen 5, seeded crimson-4 → crimson-5b;
 *     referenced across seed/, app/services/, tests/, DEMO.md, NOTES.md). InvalidateOverlay names it
 *     in a comment legitimately.
 *   · `1,287` / `85.8%` is our real zero-witness census (1287 of 1500 subjects witness nothing),
 *     stated honestly in WitnessGeometry and its CSS. The DC merely re-derived our own real figure.
 * Denying a real value is not caution, it is a broken guard. They are deliberately excluded, and
 * `898ad0e5` / `0.528` / the real curve (`0.952/0.876/0.852/0.724`) are absent for the same reason:
 * only genuine DC inventions — confirmed absent from the repo today — belong on the list.
 *
 * DO NOT RE-ADD `crimson-5b`, `1,287`/`1287`, or `85.8%` to DENY on the diagnosis's authority. They
 * are REAL. Adding any of them turns this guard RED on correct, shipped code — the failure mode that
 * gets a guard deleted. If you believe one must be denied, first grep the repo and prove it is gone.
 */
const DENY = [
  // — Unambiguous ids / dates / names (no anchoring needed) —
  { label: "b7f2c1a9 (DC-invented azure belief id; real is ea4f9135)", mode: "text", re: /b7f2c1a9/ },
  { label: "2025-11-02 (DC-invented formed date; real is 2024-05-12)", mode: "text", re: /2025-11-02/ },
  { label: "2025-12-14 (DC-invented formed date; real is 2024-05-12)", mode: "text", re: /2025-12-14/ },
  { label: "s.okonkwo (DC-invented supervisor name; ours is a UUID actor)", mode: "text", re: /s\.okonkwo/ },
  { label: "txn_5f2c81 (DC-invented loader txn id)", mode: "text", re: /txn_5f2c81/ },
  { label: "DE89·3704 (DC-fabricated IBAN prefix)", mode: "text", re: /DE89[^0-9A-Za-z]?3704/ },
  { label: "GB29·NWBK (DC-fabricated IBAN prefix)", mode: "text", re: /GB29[^0-9A-Za-z]?NWBK/ },
  { label: "7f3c·001a4b2e (DC-fabricated HLC)", mode: "text", re: /7f3c[^0-9A-Za-z]?001a4b2e/ },
  { label: "clm_00… (DC-fabricated ledger claim id prefix; our ledger reads live rows)", mode: "text", re: /clm_00/ },
  { label: "1,904,789 (DC-fabricated transaction amount)", mode: "text", re: /1,904,789/ },
  // — Wrong middle-curve confidences (bare decimals; digit-boundary-anchored) —
  { label: "0.902 (DC-fabricated middle-curve confidence; real gen-1 rises to 0.952)", mode: "decimal", re: /(?<![\d.])0\.902(?!\d)/ },
  { label: "0.861 (DC-fabricated middle-curve confidence; real is 0.876)", mode: "decimal", re: /(?<![\d.])0\.861(?!\d)/ },
  { label: "0.803 (DC-fabricated middle-curve confidence; real is 0.852)", mode: "decimal", re: /(?<![\d.])0\.803(?!\d)/ },
  { label: "0.717 (DC-fabricated middle-curve confidence; real is 0.724)", mode: "decimal", re: /(?<![\d.])0\.717(?!\d)/ },
  // — Bare integer form of the fabricated amount (only inside a quoted string) —
  { label: "1904789 (bare fabricated amount; quoted)", mode: "quoted", re: /1904789/ },
];

/** Spans of every quoted string on a line: '…', "…", `…`. Used by "quoted" mode. Good enough for a
 *  source scan — an escaped quote inside a string only ever ENDS a span early, which cannot turn a
 *  non-match into a match, so it never produces a false positive. */
function quotedSpans(line) {
  const spans = [];
  const re = /'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`/g;
  let m;
  while ((m = re.exec(line))) spans.push([m.index, m.index + m[0].length]);
  return spans;
}

/** Does `entry` match anywhere on `line`, honouring its anchoring mode? */
function hits(entry, line) {
  if (entry.mode === "text" || entry.mode === "decimal") return entry.re.test(line);
  // "quoted": the digit-bounded integer must fall inside a quoted span.
  const spans = quotedSpans(line);
  if (!spans.length) return false;
  const re = new RegExp(`(?<!\\d)${entry.re.source}(?!\\d)`, "g");
  let m;
  while ((m = re.exec(line))) {
    const start = m.index;
    const end = start + m[0].length;
    if (spans.some(([a, b]) => start >= a && end <= b)) return true;
  }
  return false;
}

/** Scan one file's text; return [{ file, line, label }] for every denylist hit. */
function scanText(fileRel, text) {
  const out = [];
  const lines = text.split(/\r?\n/);
  lines.forEach((line, i) => {
    for (const entry of DENY) {
      if (hits(entry, line)) out.push({ file: fileRel, line: i + 1, label: entry.label });
    }
  });
  return out;
}

/** Every scanned source file: src/**\/*.{ts,tsx,css}. */
function sourceFiles(dir) {
  const out = [];
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    const st = fs.statSync(full);
    if (st.isDirectory()) out.push(...sourceFiles(full));
    else if (/\.(ts|tsx|css)$/.test(name)) out.push(full);
  }
  return out;
}

/* ================================ THE FIXTURE ================================
 * Re-scanned on every run. The planted leaks pin that the guard still DETECTS; the anchoring
 * negatives pin that it still leaves legit math alone. Change the fixture and this list must move
 * with it — that coupling is the point.
 * ============================================================================ */
const FIXTURE = "scripts/fixtures/fabricated-data.fixture.txt";
const EXPECTED = [
  { line: 8, has: "b7f2c1a9" },
  { line: 9, has: "2025-11-02" },
  { line: 10, has: "2025-12-14" },
  { line: 11, has: "s.okonkwo" },
  { line: 12, has: "txn_5f2c81" },
  { line: 13, has: "DE89" },
  { line: 14, has: "GB29" },
  { line: 15, has: "7f3c" },
  { line: 16, has: "clm_00" },
  { line: 17, has: "1,904,789" },
  { line: 18, has: "0.902" },
  { line: 19, has: "0.861" },
  { line: 20, has: "0.803" },
  { line: 21, has: "0.717" },
  { line: 22, has: "1904789" },
];

function selfTest() {
  const text = fs.readFileSync(path.join(FE, FIXTURE), "utf8");
  const found = scanText(FIXTURE, text);
  const foundLines = [...new Set(found.map((f) => f.line))].sort((a, b) => a - b);
  const wantLines = EXPECTED.map((e) => e.line);

  const missing = wantLines.filter((l) => !foundLines.includes(l));
  const extra = foundLines.filter((l) => !wantLines.includes(l));

  if (missing.length || extra.length) {
    console.error("\n=== THE FABRICATED-DATA GUARD FAILED ITS OWN FIXTURE ===");
    if (missing.length)
      console.error(
        `MISSED planted leaks at lines ${missing.join(", ")} — the guard has been weakened and no\n` +
          "longer detects what it exists to detect. A guard that cannot be shown to fail is theatre.",
      );
    if (extra.length)
      console.error(
        `FALSE-FLAGGED anchoring negatives at lines ${extra.join(", ")} — the guard now trips on\n` +
          "legit math, which is how a correct-work guard gets deleted. Tighten the anchoring.",
      );
    return false;
  }
  console.log(
    `self-test: the guard catches all ${EXPECTED.length} planted DC literals (ids, dates, name, ` +
      `IBANs, HLC, ledger id, amount, curve middles, bare int) and leaves the 6 anchoring ` +
      `negatives (10.717, 0.7175, "19047890", bare 1904789, 0.924, 0.556) alone.`,
  );
  return true;
}

/* ================================== RUN ===================================== */

const fixtureOk = selfTest();

const violations = [];
for (const full of sourceFiles(SRC)) {
  violations.push(...scanText(rel(full), fs.readFileSync(full, "utf8")));
}

console.log(
  `\nfiles scanned: ${sourceFiles(SRC).length} under src/  ·  denylist entries: ${DENY.length}`,
);

if (violations.length) {
  console.error(`\n=== A FABRICATED DC LITERAL REACHED frontend/src ===\n`);
  for (const v of violations) {
    console.error(`  ${v.file}:${v.line}\n     ${v.label}\n`);
  }
  console.error(
    "The port takes VISUAL DESIGN ONLY. Every number, id and timestamp shown must come from a live\n" +
      "endpoint — the DC bundle's demo literals are wrong in ways that look right. Remove it.\n",
  );
}

process.exit(violations.length || !fixtureOk ? 1 : 0);
