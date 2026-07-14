/*
 * THE COMPOSITION GUARD — the exam and the answer key may never be in the same component tree.
 *
 * ============================== WHAT IT PROTECTS ==============================
 * The project's central integrity claim is that the AML witness never saw a ground-truth label.
 * That is what makes CYCLE's honest 75.4% precision (14 of the 57 edges it fires on are benign) a
 * MEANINGFUL number rather than a lookup: the reader can only evaluate the graph's work if they
 * cannot already see which of the two they are looking at.
 *
 * The backend enforces this (tests/test_oracle_boundary.py: the decider's inputs cannot carry a
 * label, BY TYPE). The FRONTEND did not. `DecisionFeed.tsx` and `Investigation.tsx` both render
 * `is_fraud` — legitimately, because a decision's outcome IS an audit fact, attached to a decision
 * that was already made without it. That was harmless for exactly as long as no witness was on
 * screen. The moment a witness is drawn, a single React component taking both an
 * AmlInterrogationResponse and a Decision would reintroduce the collapse IN PIXELS, PERMANENTLY,
 * with no test failing anywhere.
 *
 * ======================== WHY THIS IS NOT A TEXT SCAN =========================
 * A grep for `is_fraud` in .tsx would be a PROXY, and a proxy guard passes while the thing it
 * protects is broken. This project has already shipped that mistake more than once (the 14-line
 * proximity window that passed its own bug; the guard that EXPLAINed a query the application never
 * runs; a typecheck that checked zero files and was cited as evidence nine times). So:
 *
 *   - a token scan misses `d.verdict` (still the audit layer) and trips on an honest comment;
 *   - a scan for the type NAMES misses `import type { Decision as Answer }`;
 *   - and BOTH are blind to the shape that will actually happen — a component that takes an
 *     `Investigation`, which CONTAINS a Decision. The word "Decision" appears nowhere in it.
 *
 * This walks the TYPE GRAPH with the TypeScript compiler API and compares SYMBOLS BY DECLARATION
 * SITE. Aliasing, re-export chains, spreads, intersections and wrappers are all transparent to it.
 *
 * ============================== THE THREE CHECKS ==============================
 *  A. COMPOSITION. No component's prop surface may transitively reach BOTH layers.
 *  B. CHANNEL. Props are not the only channel: a zero-prop evidence component could simply CALL
 *     `listDecisions()` itself. So every evidence module's imported symbols are resolved and
 *     type-walked; not one of them may reach the audit layer.
 *  C. ADJACENCY. Two sibling components, each individually legal, still put the witness and the
 *     label on one screen. Whitespace is not the mechanism; SEQUENCE is. So the JSX subtree that
 *     mounts the evidence surface may contain no audit-coloured component.
 *
 * App.tsx is exempt from (A) BY STRUCTURE, not by allowlist: it takes no props, so it has no prop
 * surface to violate. It is the composition root, it legitimately holds both layers, and what it
 * hands the evidence surface is a bare `UUID`. (C) is what keeps that honest.
 *
 * ===================== THE DEMONSTRATION SHIPS INSIDE THE GUARD ================
 * "A guard that cannot be shown to fail is theatre." Proving it once in a session and pasting the
 * output proves it about a build nobody will ever run again. So the guard re-analyzes a committed
 * fixture pair on EVERY run and asserts it still finds the known violations at the known lines. A
 * gutted guard fails its own fixtures.
 *
 * WHERE THIS RUNS, AND WHY NOT PYTEST. It runs in `.github/workflows/frontend-ci.yml`. It cannot
 * live in the backend pytest suite: `ci.yml` has no Node step, and — far worse — it declares
 * `paths-ignore: ['frontend/**']`, so a frontend-only push does not run the backend suite AT ALL.
 * A pytest composition guard would have been skipped by exactly the pushes that can violate it:
 * a check that cannot fail on the change it protects against. That is the `tsc --noEmit` disease,
 * one layer up. `tests/test_composition_guard.py` instead asserts that frontend CI really invokes
 * this script — workflow files are NOT in paths-ignore, so deleting the step trips the backend.
 */

import ts from "typescript";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FE = path.resolve(HERE, "..");
const norm = (p) => p.replace(/\\/g, "/");
const rel = (p) => norm(path.relative(FE, p));

/* --------------------------------------------------------------------------------------------
 * THE TWO COLOURS. Identified by DECLARATION SITE — the interface as declared in src/api/types.ts
 * — never by a name matched in text. A local `interface Decision {}` elsewhere is a different
 * symbol and is correctly ignored; `Decision` imported under any alias is the same symbol and is
 * correctly caught.
 * ------------------------------------------------------------------------------------------ */
const TYPES_TS = norm(path.join(FE, "src/api/types.ts"));

/** The AUDIT layer: what the fleet DECIDED, and how it scored. `Decision` is the type that carries
 *  `is_fraud`, `verdict`, `driving_belief_id` and `witness_outcome` — the answer key and the score. */
const AUDIT_ROOTS = new Set(["Decision", "DecisionListResponse"]);

/** The EVIDENCE layer: what the graph WITNESSED, re-derived from unlabeled rows. Carries no label
 *  and must never carry one. */
const EVIDENCE_ROOTS = new Set([
  "AmlInterrogationResponse",
  "AmlWitness",
  "AmlTransaction",
  "AmlAccount",
]);

/** Every module of the evidence surface. Check B applies to each: not one imported symbol in any of
 *  them may reach the audit layer, through props or any other channel. Pinned, and completeness is
 *  separately asserted below, so a new evidence component cannot quietly escape the check.
 *
 *  ONE ENTRY HERE IS A DELIBERATE DECLARATION, NOT A FORMALITY — `src/lib/witnessGeometry.ts`.
 *  Check B' below asserts this list is complete with respect to COMPONENTS: anything rendering the
 *  evidence layer from an unlisted module fails the build. But `witnessGeometry.ts` contains NO
 *  JSX, so it is not a component, and B' cannot force it. A pure layout module is exactly the shape
 *  of the hole check B exists to close (a zero-prop module that simply CALLS `listDecisions()`
 *  itself), so it is listed by hand and MEANT: the geometry must be derivable from /interrogate
 *  alone, and if it ever imports the audit layer through any channel, the build fails. */
const EVIDENCE_MODULES = [
  "src/components/AmlConsole.tsx",
  "src/components/WitnessGeometry.tsx",
  "src/hooks/useInterrogation.ts",
  "src/lib/basis.ts",
  "src/lib/witnessGeometry.ts",
];

/* ---------------------------------------------------------------------------------------- */

function buildProgram(extraFiles = []) {
  const cfg = ts.readConfigFile(path.join(FE, "tsconfig.app.json"), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(cfg.config, ts.sys, FE);
  const program = ts.createProgram([...parsed.fileNames, ...extraFiles], {
    ...parsed.options,
    noEmit: true,
  });
  return { program, checker: program.getTypeChecker() };
}

/** Resolve through import aliases to the symbol that was actually DECLARED. */
function realSymbol(checker, sym) {
  if (!sym) return sym;
  return sym.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(sym) : sym;
}

function makeColourOf(rootFiles) {
  return (sym) => {
    const d = sym?.declarations?.[0];
    if (!d) return null;
    if (!rootFiles.has(norm(d.getSourceFile().fileName))) return null;
    const n = sym.getName();
    if (AUDIT_ROOTS.has(n)) return "AUDIT";
    if (EVIDENCE_ROOTS.has(n)) return "EVIDENCE";
    return null;
  };
}

/** Declared outside our own source — React, the DOM lib, node_modules.
 *
 *  THE WALK MUST NOT DESCEND INTO THESE, and this is a correctness fix, not just a speed one: the
 *  first draft walked call-signature parameters into React's event/DOM types and exhausted a 4 GB
 *  heap. It is also SOUND to skip them. Both colours are declared in `src/api/types.ts`, so a
 *  foreign type can only reach a colour through its TYPE ARGUMENTS (`Loadable<DecisionsData>`,
 *  `Promise<DecisionListResponse>`) — and those are walked below regardless. What is skipped is a
 *  foreign type's own internals, which cannot contain our declarations. */
function isForeign(type) {
  const d = (type.aliasSymbol ?? type.getSymbol())?.declarations?.[0];
  if (!d) return false;
  const f = norm(d.getSourceFile().fileName);
  return f.includes("node_modules") || /\/lib\.[^/]*\.d\.ts$/.test(f);
}

/** Transitive closure over a TYPE's graph: properties, unions, intersections, type arguments,
 *  and — in BOTH directions — call signatures. */
function makeClosure(checker, colourOf) {
  return function closure(type, seen = new Set(), colours = new Set(), depth = 0) {
    if (!type || depth > 12) return colours;
    if (type.id && seen.has(type.id)) return colours;
    if (type.id) seen.add(type.id);

    for (const s of [type.aliasSymbol, type.getSymbol()]) {
      const c = colourOf(s);
      if (c) colours.add(c);
    }

    // Unions/intersections and type arguments are ALWAYS followed, foreign or not: `Promise<T>` and
    // `Loadable<T>` are how our types travel through library generics.
    for (const t of type.types ?? []) closure(t, seen, colours, depth + 1);
    for (const t of checker.getTypeArguments?.(type) ?? []) closure(t, seen, colours, depth + 1);
    if (isForeign(type)) return colours;

    for (const p of type.getProperties?.() ?? []) {
      const d = p.valueDeclaration ?? p.declarations?.[0];
      if (!d) continue;
      closure(checker.getTypeOfSymbolAtLocation(p, d), seen, colours, depth + 1);
    }
    // FUNCTIONS ARE A CHANNEL IN BOTH DIRECTIONS, and missing this was a real hole in the guard's
    // first draft: `listDecisions` reaches the audit layer through its RETURN type, and a callback
    // prop like `(d: Decision) => void` reaches it through its PARAMETER. Neither is a property, so
    // the property walk above sees neither. A guard blind to functions is blind to the API client.
    for (const sig of type.getCallSignatures?.() ?? []) {
      closure(sig.getReturnType(), seen, colours, depth + 1);
      for (const p of sig.getParameters()) {
        const d = p.valueDeclaration ?? p.declarations?.[0];
        if (d) closure(checker.getTypeOfSymbolAtLocation(p, d), seen, colours, depth + 1);
      }
    }
    return colours;
  };
}

/** A component: a function containing JSX. Its first parameter is its prop surface. */
function isComponent(node) {
  if (
    !ts.isFunctionDeclaration(node) &&
    !ts.isArrowFunction(node) &&
    !ts.isFunctionExpression(node)
  )
    return false;
  let hasJsx = false;
  const walk = (n) => {
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)) hasJsx = true;
    if (!hasJsx) ts.forEachChild(n, walk);
  };
  if (node.body) walk(node.body);
  return hasJsx;
}

function componentName(node) {
  if (node.name) return node.name.getText();
  const p = node.parent;
  if (p && (ts.isVariableDeclaration(p) || ts.isPropertyAssignment(p)) && p.name)
    return p.name.getText();
  return "(anonymous)";
}

/** Every JSX tag name used anywhere inside a node. */
function jsxTagsIn(node) {
  const tags = new Set();
  const walk = (n) => {
    if (ts.isJsxSelfClosingElement(n)) tags.add(n.tagName.getText());
    if (ts.isJsxElement(n)) tags.add(n.openingElement.tagName.getText());
    ts.forEachChild(n, walk);
  };
  walk(node);
  return tags;
}

/* =============================== THE ANALYSIS ================================ */

function analyze({ files, evidenceModules, rootFiles }) {
  const { program, checker } = buildProgram(files);
  const colourOf = makeColourOf(rootFiles);
  const closure = makeClosure(checker, colourOf);

  const inScope = (sf) => {
    const f = norm(sf.fileName);
    if (f.includes("node_modules")) return false;
    return f.startsWith(norm(path.join(FE, "src"))) || files.some((x) => norm(x) === f);
  };

  const violations = [];
  /** name -> colours reachable through its PROPS. Check A's currency. */
  const registry = new Map();
  /** name -> the JSX tags it mounts. The render graph, for check C. */
  const mounts = new Map();
  const components = [];

  /* --- A. COMPOSITION ------------------------------------------------------- */
  for (const sf of program.getSourceFiles()) {
    if (!inScope(sf)) continue;
    const visit = (node) => {
      if (isComponent(node)) {
        const param = node.parameters?.[0];
        const colours = param ? closure(checker.getTypeAtLocation(param)) : new Set();
        const name = componentName(node);
        const line = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
        components.push({ file: rel(sf.fileName), name, line, colours: [...colours] });
        if (name !== "(anonymous)") {
          registry.set(name, colours);
          mounts.set(name, jsxTagsIn(node));
        }
        if (colours.has("AUDIT") && colours.has("EVIDENCE")) {
          violations.push({
            check: "A/composition",
            file: rel(sf.fileName),
            line,
            message:
              `<${name}> receives BOTH the EVIDENCE layer (an interrogation) AND the AUDIT layer ` +
              `(a decision) on its prop surface. The exam and the answer key are in one component.`,
          });
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }

  /* --- B. CHANNEL ----------------------------------------------------------- */
  for (const modRel of evidenceModules) {
    const sf = program.getSourceFiles().find((s) => norm(s.fileName).endsWith(modRel));
    if (!sf) {
      violations.push({
        check: "B/channel",
        file: modRel,
        line: 0,
        message: `declared an EVIDENCE module but no such file is in the program`,
      });
      continue;
    }
    for (const stmt of sf.statements) {
      if (!ts.isImportDeclaration(stmt) || !stmt.importClause) continue;
      const named = stmt.importClause.namedBindings;
      const specs = named && ts.isNamedImports(named) ? named.elements : [];
      for (const spec of specs) {
        const sym = realSymbol(checker, checker.getSymbolAtLocation(spec.name));
        if (!sym) continue;
        const colours = new Set();
        // A type import: walk the type it declares. A value import (a function): walk its type,
        // which reaches its RETURN type — so `listDecisions` is caught by what it hands back.
        const declared = checker.getDeclaredTypeOfSymbol(sym);
        closure(declared, new Set(), colours);
        const d = sym.valueDeclaration ?? sym.declarations?.[0];
        if (d) closure(checker.getTypeOfSymbolAtLocation(sym, d), new Set(), colours);
        if (colours.has("AUDIT")) {
          const line = sf.getLineAndCharacterOfPosition(spec.getStart()).line + 1;
          violations.push({
            check: "B/channel",
            file: rel(sf.fileName),
            line,
            message:
              `the EVIDENCE module imports \`${spec.name.getText()}\`, which reaches the AUDIT ` +
              `layer. The evidence surface must be renderable from /interrogate ALONE — a witness ` +
              `that needs the answer key to be drawn is not a witness.`,
          });
        }
      }
    }
  }

  /* --- B'. The evidence-module list must be COMPLETE ------------------------- */
  // A new component whose props reach the evidence layer, in a module nobody listed, would be
  // exempt from check B for free. So completeness is asserted rather than trusted.
  for (const c of components) {
    if (!c.colours.includes("EVIDENCE")) continue;
    if (!c.file.startsWith("src/")) continue; // fixtures are inspected explicitly, above
    if (evidenceModules.some((m) => c.file.endsWith(m) || m.endsWith(c.file))) continue;
    violations.push({
      check: "B/channel",
      file: c.file,
      line: c.line,
      message:
        `<${c.name}> renders the EVIDENCE layer but its module is not in EVIDENCE_MODULES, so ` +
        `check B never inspected it. Add it (and mean it) — do not leave it unguarded.`,
    });
  }

  /* --- C. ADJACENCY --------------------------------------------------------- */
  // Find every place an EVIDENCE-coloured component is MOUNTED, and prove no AUDIT-coloured
  // component is mounted in the same JSX subtree. This is the check that a props-only guard
  // cannot make: two siblings, each individually legal, still put the label beside the witness.
  // COLOUR PROPAGATES THROUGH THE RENDER GRAPH, and getting this wrong was a REAL HOLE that the
  // fixtures did not cover — so it was green while broken, which is the exact disease this guard
  // exists to prevent. Found only by mounting <AmlConsole> beside <DecisionFeed> and watching
  // nothing happen.
  //
  // The mechanism: `AmlConsole`'s props are `{ txnId: UUID }` — COLOURLESS, and deliberately so,
  // because the join between the layers is a bare id. The EVIDENCE-coloured component is
  // `EvidencePane`, which lives INSIDE it. So a check that only knows prop colours never looks at
  // the surface's actual mount site, and the adjacency it is supposed to forbid sails through.
  //
  // A component is EVIDENCE if it renders evidence, however deep. Same for AUDIT. Fixpoint over
  // the mount graph. (Check A still uses PROP colours only — a component that merely renders a
  // child holding a Decision has not itself received one.)
  const renderColours = new Map([...registry].map(([n, c]) => [n, new Set(c)]));
  for (let changed = true; changed; ) {
    changed = false;
    for (const [name, tags] of mounts) {
      const mine = renderColours.get(name) ?? new Set();
      for (const tag of tags) {
        for (const c of renderColours.get(tag) ?? []) {
          if (!mine.has(c)) {
            mine.add(c);
            changed = true;
          }
        }
      }
      renderColours.set(name, mine);
    }
  }

  const evidenceComponents = new Set(
    [...renderColours.entries()].filter(([, c]) => c.has("EVIDENCE")).map(([n]) => n),
  );
  const auditComponents = new Set(
    [...renderColours.entries()].filter(([, c]) => c.has("AUDIT")).map(([n]) => n),
  );

  for (const sf of program.getSourceFiles()) {
    if (!inScope(sf)) continue;
    const visit = (node) => {
      const tag =
        ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)
          ? node.tagName.getText()
          : null;
      if (tag && evidenceComponents.has(tag)) {
        // The "arm": the nearest enclosing conditional branch, or the enclosing return.
        let arm = node;
        let cur = node.parent;
        while (cur) {
          if (ts.isConditionalExpression(cur.parent ?? {})) {
            arm = cur;
            break;
          }
          if (ts.isReturnStatement(cur) || ts.isArrowFunction(cur)) {
            arm = cur;
            break;
          }
          cur = cur.parent;
        }
        // Not itself: a component that is BOTH colours is check A's finding, not an adjacency
        // one, and the composition root legitimately renders both layers in DIFFERENT arms.
        const co = [...jsxTagsIn(arm)].filter((t) => t !== tag && auditComponents.has(t));
        if (co.length) {
          const line = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
          violations.push({
            check: "C/adjacency",
            file: rel(sf.fileName),
            line,
            message:
              `<${tag}> (EVIDENCE) is mounted in the same JSX subtree as ${co
                .map((t) => `<${t}>`)
                .join(", ")} (AUDIT). The answer key would be on screen beside the exam. The two ` +
              `layers are joined by an ORDERED REVEAL, never by adjacency.`,
          });
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }

  return { violations, components };
}

/* ================================ THE FIXTURES ================================
 * The guard's own demonstration, re-run on every invocation. If the analyzer above is ever gutted
 * — a check deleted, a colour dropped, the closure depth cut to nothing — these stop failing, and
 * THAT is what breaks the build.
 * ============================================================================ */
const FIXTURES = [
  "scripts/fixtures/legitimate.tsx",
  "scripts/fixtures/violation-direct.tsx",
  "scripts/fixtures/violation-indirect.tsx",
  "scripts/fixtures/violation-channel.tsx",
  "scripts/fixtures/violation-adjacency.tsx",
];

/** Exactly what the guard must find in the fixtures. File, line, and which check caught it. */
const EXPECTED = [
  { check: "A/composition", file: "scripts/fixtures/violation-direct.tsx", line: 14 },
  { check: "A/composition", file: "scripts/fixtures/violation-indirect.tsx", line: 17 },
  { check: "B/channel", file: "scripts/fixtures/violation-channel.tsx", line: 14 },
  { check: "C/adjacency", file: "scripts/fixtures/violation-adjacency.tsx", line: 40 },
];

function selfTest() {
  const files = FIXTURES.map((f) => path.join(FE, f));
  const rootFiles = new Set([TYPES_TS, ...files.map(norm)]);
  const { violations } = analyze({
    files,
    // In the self-test the fixtures ARE the evidence modules, so check B inspects them.
    evidenceModules: [...EVIDENCE_MODULES, "scripts/fixtures/violation-channel.tsx"],
    rootFiles,
  });

  const found = violations
    .filter((v) => v.file.startsWith("scripts/fixtures/"))
    .map((v) => ({ check: v.check, file: v.file, line: v.line }))
    .sort((a, b) => (a.file + a.line).localeCompare(b.file + b.line));
  const want = [...EXPECTED].sort((a, b) => (a.file + a.line).localeCompare(b.file + b.line));

  const ok = JSON.stringify(found) === JSON.stringify(want);
  if (!ok) {
    console.error("\n=== THE GUARD FAILED ITS OWN FIXTURES ===");
    console.error(
      "It must find EXACTLY the known violations, at the known lines. If it found FEWER, the guard\n" +
        "has been weakened and no longer detects what it exists to detect — a guard that cannot be\n" +
        "shown to fail is theatre. If it found MORE, the legitimate arrangement is now tripping it.\n",
    );
    console.error("expected:", JSON.stringify(want, null, 2));
    console.error("found:   ", JSON.stringify(found, null, 2));
    return false;
  }
  console.log(
    `self-test: the guard still catches its ${EXPECTED.length} known violations ` +
      `(${EXPECTED.map((e) => e.check.split("/")[0]).join(", ")}) — and leaves the legitimate ` +
      `separate-surface arrangement alone.`,
  );
  return true;
}

/* ================================== RUN ===================================== */

const fixturesOk = selfTest();

const { violations, components } = analyze({
  files: [],
  evidenceModules: EVIDENCE_MODULES,
  rootFiles: new Set([TYPES_TS]),
});

const coloured = components.filter((c) => c.colours.length);
console.log(
  `\ncomponents inspected: ${components.length}  ·  reaching a layer: ${coloured.length}  ` +
    `(EVIDENCE ${coloured.filter((c) => c.colours.includes("EVIDENCE")).length} / ` +
    `AUDIT ${coloured.filter((c) => c.colours.includes("AUDIT")).length})`,
);

if (violations.length) {
  console.error(`\n=== THE ORACLE BOUNDARY IS BROKEN IN THE COMPONENT TREE ===\n`);
  for (const v of violations) {
    console.error(`  ${v.file}:${v.line}  [${v.check}]\n     ${v.message}\n`);
  }
  console.error(
    "The label may appear ONLY where it is an AUDIT fact — attached to a decision that was already\n" +
      "made without it. It may never be readable beside, or by, the witness whose work it scores.\n",
  );
}

process.exit(violations.length || !fixturesOk ? 1 : 0);
