/*
 * THE GEOMETRY GUARD — the invariant that is only provable by rendering.
 *
 * ================== A CONFIRM GATE PROTECTS AGAINST NOT KNOWING. ==================
 * ================== IT CANNOT PROTECT AGAINST NOT MOVING YOUR HAND. ==================
 *
 * Pinning the governed write put the arm button and the confirm button in the SAME bottom-anchored
 * footer. Two clicks of muscle memory in one screen position would then be an irreversible
 * fleet-wide write — and the existing arm/confirm gate is no defence, because it defends against
 * ignorance, not against a hand that has not moved.
 *
 * So `.kill__actions` stacks (column, Cancel the full-width last child), which puts CANCEL on the
 * arm button's exact footprint: a repeated click at the remembered position now CANCELS. That is a
 * SAFETY MECHANISM wearing the clothes of a style rule, and until this file existed NOTHING caught
 * a change back to `flex-direction: row`.
 *
 * A pytest grep for `flex-direction: column` in the CSS would assert the TEXT, not the GEOMETRY —
 * it would pass a `.kill__cancel { position: absolute }` without blinking. That is the disease this
 * project has recorded five times (the dead vector indexes; the EXPLAIN of a query nothing runs;
 * test_citations' own docstring; the 14-line proximity window; `tsc --noEmit` checking zero files).
 * This guard renders the real console in a real browser and MEASURES.
 *
 * ===================== ASSERT THE PROPERTY, NEVER THE PIXELS =====================
 *
 * The Inspector-fold session measured, at 1280x800: arm y=749..786, confirm y=694..729 (clearing by
 * 20px), cancel y=737..772. Those numbers are NOT asserted here, and pinning them would be a
 * mistake dressed as rigour. They are an artifact of one belief's rule-text length and one font
 * metric; any innocent layout change would "fail" the guard, and a guard that cries wolf on honest
 * change teaches people to weaken it. What is asserted is the INVARIANT the numbers were evidence
 * for:
 *
 *      Confirm ∩ arm-footprint  =  ∅        (the kill-shot is NOT where your hand already is)
 *      Cancel  ∩ arm-footprint  ≠  ∅        (the safe action IS)
 *
 * The data comes from a mock — see mock.ts for why that is a replay of the cluster and not a proxy,
 * and why layout is data-insensitive to it.
 */

import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectedGeometry, installMock, SUBJECT_TXN_REF, SUBJECTS } from "./mock.js";

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Do two rects share any area at all? Touching edges do NOT count as overlap. */
function intersects(a: Rect, b: Rect): boolean {
  return (
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height
  );
}

/** `y 749..786  x 912..1264` — the form a failure message should be readable in. */
function fmt(name: string, r: Rect): string {
  return `${name.padEnd(8)} y ${Math.round(r.y)}..${Math.round(r.y + r.height)}   x ${Math.round(
    r.x,
  )}..${Math.round(r.x + r.width)}   (w ${Math.round(r.width)} h ${Math.round(r.height)})`;
}

async function rectOf(locator: Locator): Promise<Rect> {
  const box = await locator.boundingBox();
  if (!box) throw new Error("element has no bounding box (not rendered?)");
  return box;
}

/**
 * Drive the real console to the state the invariant lives in: the crimson decision investigated,
 * Time-travel OPEN (the state that used to push the kill-shot below the fold — i.e. the moment the
 * supervisor has actually LOOKED at the evidence), the governed write armed.
 *
 * THIS IS THE GUARD'S LIVENESS PROPERTY, and it is what a vacuous check cannot have. The test must
 * genuinely reach the armed gate before it can measure anything: a mock too thin to produce that
 * state FAILS here rather than passing while measuring nothing. `tsc --noEmit` had no such floor —
 * it typechecked zero files and exited 0 for nine gates.
 */
async function driveToArmed(page: Page): Promise<{ armRect: Rect; confirm: Locator; cancel: Locator }> {
  await page.goto("/");

  // The crimson belief is ONLY reachable under `card`: all 1,500 AML rows share one `decided_at`
  // newer than every card row, so they sort above them and fill the unfiltered first page.
  const filter = page.getByRole("group", { name: "Filter decisions by kind" });
  await filter.getByRole("button", { name: /^card/ }).click();

  // The decision the crimson belief drove. Selected by its real txn_ref from the captured feed —
  // no data-testid was needed anywhere in this guard; the console is already addressable by the
  // text and roles a supervisor actually sees.
  await page.locator(".feed__row", { hasText: SUBJECT_TXN_REF }).first().click();

  // Time-travel OPEN. This is not optional set-dressing: with it CLOSED the whole Investigation
  // fits at every viewport, and the defect only appears once the evidence is on screen.
  await page.getByRole("button", { name: /Time-travel/ }).click();
  await page.waitForSelector(".tt__depo", { state: "visible" });

  // The arm button's footprint — measured BEFORE arming, because it is the position the
  // supervisor's hand is about to remember.
  const arm = page.getByRole("button", { name: "Invalidate belief fleet-wide" });
  await expect(arm).toBeVisible();
  const armRect = await rectOf(arm);

  await arm.click();

  const confirm = page.getByRole("button", { name: "Confirm invalidation" });
  const cancel = page.getByRole("button", { name: "Cancel" });
  await expect(confirm).toBeVisible();
  await expect(cancel).toBeVisible();

  return { armRect, confirm, cancel };
}

test.describe("the governed write", () => {
  test("Confirm does not land on the arm button's footprint; Cancel does", async ({ page }) => {
    const misses = installMock(page);
    const { armRect, confirm, cancel } = await driveToArmed(page);

    const confirmRect = await rectOf(confirm);
    const cancelRect = await rectOf(cancel);

    const rects = [
      fmt("arm", armRect),
      fmt("confirm", confirmRect),
      fmt("cancel", cancelRect),
    ].join("\n");

    expect(
      intersects(confirmRect, armRect),
      "CONFIRM LANDS ON THE ARM BUTTON'S FOOTPRINT.\n\n" +
        "Two clicks of muscle memory in one screen position are now an irreversible fleet-wide\n" +
        "write. The confirm gate does not save you here: it protects against not KNOWING, not\n" +
        "against not MOVING YOUR HAND. `.kill__actions` must stack (flex-direction: column) with\n" +
        "Cancel as the full-width last child, so the remembered position is the SAFE action.\n\n" +
        rects,
    ).toBe(false);

    expect(
      intersects(cancelRect, armRect),
      "CANCEL NO LONGER COVERS THE ARM BUTTON'S FOOTPRINT.\n\n" +
        "Confirm is clear of it, so the write is not one-click-reachable — but the remembered\n" +
        "position now does NOTHING instead of cancelling, which is half the mechanism. Cancel must\n" +
        "be the full-width last child of `.kill__actions`.\n\n" +
        rects,
    ).toBe(true);

    // DOM order == visual order, so tab order is unchanged (Confirm, then Cancel). The stack is a
    // safety mechanism; it must not have quietly become a focus-order surprise.
    expect(confirmRect.y, `Confirm must render ABOVE Cancel.\n\n${rects}`).toBeLessThan(cancelRect.y);

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
  });

  test("the kill-shot is reachable without a scroll, with Time-travel open", async ({ page }) => {
    const misses = installMock(page);
    await page.goto("/");

    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^card/ }).click();
    await page.locator(".feed__row", { hasText: SUBJECT_TXN_REF }).first().click();
    await page.getByRole("button", { name: /Time-travel/ }).click();
    await page.waitForSelector(".tt__depo", { state: "visible" });

    // THE INVERTED DEFECT this is here to keep fixed: with Time-travel CLOSED the Investigation fit
    // at every viewport, and the moment it OPENED — the moment the supervisor actually looked at
    // the evidence — the one irreversible control dropped below the fold. The console made the
    // kill-shot one unobstructed click away while UNINFORMED and hid it behind a scroll once
    // INFORMED. It is pinned in a footer now; this asserts the footer still holds.
    const arm = page.getByRole("button", { name: "Invalidate belief fleet-wide" });
    const armRect = await rectOf(arm);
    const viewport = page.viewportSize();
    if (!viewport) throw new Error("no viewport");

    expect(
      armRect.y >= 0 && armRect.y + armRect.height <= viewport.height,
      "THE GOVERNED WRITE IS BELOW THE FOLD WITH TIME-TRAVEL OPEN.\n\n" +
        "This is the defect the Inspector-fold session was convened for, and it was INVERTED: the\n" +
        "control is reachable while uninformed and hidden once informed. Controls are PINNED;\n" +
        "evidence and receipts SCROLL.\n\n" +
        `${fmt("arm", armRect)}\nviewport height ${viewport.height}`,
    ).toBe(true);

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
  });
});

/** Enter through the MOAT — the decision feed, where `is_fraud` legitimately lives — and click
 *  through to the evidence layer. The seam hands over a bare UUID and nothing else. Every geometry
 *  subject is captured from the feed's FIRST PAGE, so this never needs to page. */
async function interrogate(page: Page, txnId: string): Promise<void> {
  await page.goto("/");
  const filter = page.getByRole("group", { name: "Filter decisions by kind" });
  await filter.getByRole("button", { name: /^aml/ }).click();
  await page.locator(".feed__row", { hasText: txnId.slice(0, 6) }).first().click();
  await page.getByRole("button", { name: /Interrogate the transaction/ }).click();
  await page.waitForSelector(".aml__witnesses", { state: "visible" });
}

/**
 * THE PICTURE MUST NOT UNDERSTATE THE EVIDENCE, AND IT MUST NOT INVENT A SUBJECT.
 *
 * ================== WHY THESE TWO, AND NOT "DOES THE RING CLOSE" ==================
 * Ring closure is the money shot, and it is deliberately NOT guarded: each edge is drawn between the
 * real account ids on its own row, so the ring closes BY CONSTRUCTION, and the data property behind
 * it is already pinned in the backend suite (57/57 contiguous and closed). The one bug that could
 * break it in pixels — collapsing two accounts into one node — cannot manifest: nodes are keyed by
 * account UUID, and account numbers are 648/648 unique in this extract anyway. A per-push cost for a
 * property that is designed out rather than observed is what this project cut the sticky-focus guard
 * for. These two CAN regress, silently, and would be invisible:
 *
 *  1. EDGE COUNT. The money-flow graph is a MULTIGRAPH: 41 witnesses carry TWO distinct transactions
 *     between the SAME pair of accounts. A layout keying edges by (from,to) — the natural thing to
 *     write, and the thing a future refactor will reach for — draws THREE lines where the evidence
 *     has FOUR. The picture then silently understates the evidence, which is this project's signature
 *     defect ("CONCLUSIVE_NO: 463 searched") committed in the one surface that has no test.
 *
 *  2. THE SUBJECT MARKER. The subject is ABSENT from its own witness in 75 of 107 GATHER-SCATTER
 *     matches. Marking `edges[0]`, or marking anything at all on those, points the reader at a
 *     transaction they did not click — a fabrication, and a confident-looking one.
 *
 * Both are asserted against the WIRE, never against a number written here: `expectedGeometry()` reads
 * the witness's own `transaction_ids` out of the captured interrogation. A hardcoded "10" would be a
 * second copy of the same belief, free to drift with the fixture and stay green.
 */
test.describe("the witness geometry", () => {
  for (const [label, txnId] of [
    ["a RING — every hop drawn, and the subject marked", SUBJECTS.aml_ring_txn_id],
    ["PARALLEL transactions — two on one account pair, drawn as two", SUBJECTS.aml_parallel_txn_id],
    ["a witness that does NOT cite the subject — nothing marked", SUBJECTS.aml_omits_subject_txn_id],
  ] as const) {
    test(`the drawing matches the wire: ${label}`, async ({ page }) => {
      const misses = installMock(page);
      const expected = expectedGeometry(txnId);

      // LIVENESS. A fixture whose subject witnesses nothing renders no geometry, and every
      // assertion below would then pass over an empty page — which is exactly how the old capture
      // (`ORDER BY decided_at DESC, id DESC`, over 1,500 rows sharing one `decided_at`) would have
      // made this guard vacuous. It must have something to measure before it measures.
      expect(
        expected.length,
        `${txnId} witnesses NO structure, so this guard would measure nothing. Re-capture with ` +
          "`python -m scripts.capture_console_fixtures`, which picks geometry subjects on purpose.",
      ).toBeGreaterThan(0);

      await interrogate(page, txnId);
      await expect(page.locator(".geo__section")).toBeVisible();

      const figures = page.locator(".geo");
      await expect(figures).toHaveCount(expected.length);

      for (const w of expected) {
        const fig = page.locator(".geo", { has: page.getByText(w.typology, { exact: true }) });

        // 1. EVERY CITED TRANSACTION IS A LINE. Not "every account pair".
        // Scoped to the DRAWING (`.geo__svg`), never the whole figure: the LEGS legend draws two
        // key swatches, and counting those as money is exactly the mistake this assertion exists to
        // catch. It caught it — in this guard's own first run, against the real markup.
        await expect(
          fig.locator(".geo__svg .geo__edge"),
          `${w.typology}: the witness cites ${w.edges} transactions and the drawing does not have ` +
            `${w.edges} edges.\n\nThe money-flow graph is a MULTIGRAPH — two distinct transactions ` +
            `can run between the same two accounts. If the layout keys edges by (from,to) it will ` +
            `merge them, and the picture will show FEWER transactions than the evidence contains. ` +
            `The edge is the TRANSACTION; its identity is the transaction id.`,
        ).toHaveCount(w.edges);

        // 2. THE SUBJECT IS MARKED IF AND ONLY IF THE WITNESS CITES IT.
        await expect(
          fig.locator(".geo__svg .geo__edge--subject"),
          `${w.typology}: the witness ${w.citesSubject ? "CITES" : "DOES NOT CITE"} the subject, so ` +
            `exactly ${w.citesSubject ? 1 : 0} edge(s) must be marked as the subject.\n\n` +
            `The subject is absent from its own witness in 75 of 107 GATHER-SCATTER matches (the ` +
            `witness is truncated to MIN_FANOUT) and 1 of 42 SCATTER-GATHER ones (the graph cites a ` +
            `parallel twin). Marking an edge anyway points the reader at a transaction they did not ` +
            `click.`,
        ).toHaveCount(w.citesSubject ? 1 : 0);
      }

      expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
    });
  }
});

test.describe("the oracle boundary", () => {
  // SWEPT ACROSS A SUBJECT THAT DRAWS AND ONE THAT DOES NOT.
  //
  // Until Rung 3 this ran on one subject — and that subject witnesses NOTHING, so it rendered no
  // geometry. The drawing is a NEW TEXT SURFACE: every figure carries an `aria-label` describing the
  // evidence and a `<title>` on every edge and node. A leak there would be announced to a screen
  // reader and invisible on screen — the exact blind spot the `aria-label` sweep below was written
  // for, in a surface that did not exist when it was written. So the subject that draws the most is
  // swept too.
  for (const [what, txnId] of [
    ["a subject with NO geometry", SUBJECTS.aml_transaction_id],
    ["a subject whose RING is drawn", SUBJECTS.aml_ring_txn_id],
  ] as const) {
    test(`the evidence surface never renders the label — ${what}`, async ({ page }) => {
    const misses = installMock(page);
    await page.goto("/");

    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^aml/ }).click();

    // Enter through the moat — the decision feed, where `is_fraud` legitimately lives — and click
    // through to the evidence layer. The seam hands over a bare UUID and nothing else.
    await page
      .locator(".feed__row", { hasText: txnId.slice(0, 6) })
      .first()
      .click();
    await page.getByRole("button", { name: /Interrogate the transaction/ }).click();

    const surface = page.locator(".aml");
    await expect(surface).toBeVisible();
    await page.waitForSelector(".aml__witnesses", { state: "visible" });

    // The audit layer must not merely be absent from the props (the composition guard's job — it
    // closes TYPE and MOUNT); it must be absent from the PIXELS. This is the invariant itself, and
    // a browser is the only thing that can see it. `test_composition_guard.py`'s text scan concedes
    // in its own docstring that alone it "would be a proxy" — it greps three source files and
    // cannot see a rendered string.
    //
    // AND NOT innerText ALONE. The decision feed marks fraud with an `aria-label`, NO TEXT
    // (`feed__fraud-dot`, role="img"). An innerText-only assertion would therefore have a blind
    // spot exactly where a leak is most likely to hide — a label announced to a screen reader and
    // invisible to the check. So the accessible names are swept too.
    const rendered = await surface.evaluate((root) => {
      const text = (root as HTMLElement).innerText;
      const labels = [...root.querySelectorAll("*")]
        .flatMap((el) => ["aria-label", "title", "alt"].map((a) => el.getAttribute(a)))
        .filter((v): v is string => v !== null);
      return [text, ...labels].join("\n");
    });

    for (const forbidden of [/fraud/i, /launder/i]) {
      expect(
        rendered,
        "THE ANSWER KEY IS ON THE EXAM. The evidence surface renders the ground-truth label.\n\n" +
          "It must be renderable from /interrogate ALONE — no verdict, no belief, no lineage, no\n" +
          "label. The whole meaning of the 75.4% precision exhibit is that the reader CANNOT tell a\n" +
          "benign subject from a laundering one by looking at the witnesses.\n",
      ).not.toMatch(forbidden);
    }

    // And the audit surface must not merely be quiet — it must be GONE. Two individually-legal
    // siblings still put the answer key beside the exam (this is check C of the composition guard,
    // in pixels).
    await expect(page.locator(".feed__row")).toHaveCount(0);

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
    });
  }
});

/*
 * THE HEADER ORACLE BOUNDARY — the persistent command rail must not carry the answer key onto the witness.
 *
 * ===================== WHY THE COMPOSITION GUARD IS BLIND HERE =====================
 * The `.aml` sweep above proves the label is absent from the EVIDENCE surface. But the header is
 * PERSISTENT across every view, including evidence (`App.tsx` renders `<header>` outside the body
 * ternary), and BOTH static guards are structurally blind to it — proven in NAVBAR_TRIAGE.md Part 2:
 *
 *   - composition-guard.mjs check C computes the evidence mount's "arm" as the nearest conditional
 *     consequent — `(<AmlConsole/>)` — and the header is a SIBLING of that ternary, never inside the
 *     arm. A context capsule in the header that renders a selected Decision beside the mounted witness
 *     passes check C with every colour green.
 *   - the `.aml` sweep is scoped to `page.locator(".aml")`; the header is `.console__header`, outside it.
 *
 * So the invariant "the answer key is never beside the witness" has NO automated enforcement on the
 * header EXCEPT this test — a render-time check, which is where the leak would actually appear (the
 * `view.kind !== 'aml'` conditional has already been evaluated by the time the DOM exists).
 *
 * ===================== WHY THE LEAK IS THE DEFAULT, NOT A CORNER CASE =====================
 * `selectedId` is RETAINED across the seam ("see why" calls onInterrogate, never onSelect — see the
 * justification-seam tests below). So a capsule keyed on "is a decision selected?" would render
 * "decision · txn_… · ✓ invalidated" in the header for the ENTIRE time the witness is up. The invariant
 * that makes it safe is that the capsule branches on the ACTIVE VIEW, not on retained selection: while
 * `view.kind === 'aml'`, only the evidence branch (label-free) or nothing may render. The capsule's
 * decision branch carries `data-capsule="decision"`; this asserts that marker is GONE from the header
 * once the witness is up, having first proven it PRESENT on the console (non-vacuity).
 *
 * A dot may ENCODE state — a 5px relation marker with no fact-bearing text is geometry, not a label —
 * but the capsule must carry no audit TEXT onto the witness. Both are swept: the structural decision
 * marker and the header's rendered text + accessible names.
 */
test.describe("the header oracle boundary", () => {
  test("the persistent rail carries no decision capsule onto the evidence surface", async ({ page }) => {
    const misses = installMock(page);
    await page.goto("/");
    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^aml/ }).click();

    const ring = SUBJECTS.aml_ring_txn_id;
    const item = page.locator(".feed__item", {
      has: page.locator(".feed__row", { hasText: ring.slice(0, 6) }),
    });

    // Select the decision. On the CONSOLE — the audit surface — the capsule carrying it is LEGAL: a
    // decision's outcome is an audit fact, attached to a decision already made without it.
    await item.locator(".feed__row").first().click();

    // LIVENESS. The decision capsule really does render on the console, so the "absent during
    // evidence" assertion below is not vacuously true over a capsule that never appears. `selectedId`
    // (set here) is exactly what a naive capsule would keep reading across the seam.
    const decisionCapsule = page.locator('.console__header [data-capsule="decision"]');
    await expect(
      decisionCapsule,
      "the context capsule does not carry a selected decision on the console, so this guard would " +
        'measure nothing — the capsule\'s decision branch must render `data-capsule="decision"`.',
    ).toHaveCount(1);

    // Open the witness for the SAME decision. `selectedId` is retained across the seam by design.
    await item.getByRole("button", { name: /See the witness/ }).click();
    await expect(page.locator(".aml")).toBeVisible();
    await page.waitForSelector(".aml__witnesses", { state: "visible" });

    // THE INVARIANT. The persistent header must not carry the decision/audit branch onto the witness.
    await expect(
      decisionCapsule,
      "THE ANSWER KEY IS BESIDE THE WITNESS, IN THE HEADER. The context capsule renders a selected\n" +
        "decision while INTERROGATION is open — audit context beside the exam, on the one surface the\n" +
        "whole evidence layer exists to keep clean. The composition guard cannot see this (the header\n" +
        "is outside the aml arm) and the .aml sweep cannot (the header is outside .aml). The capsule\n" +
        "must branch on the ACTIVE VIEW: while view.kind==='aml', only the evidence branch may render.\n",
    ).toHaveCount(0);

    // And the header's text + accessible names must be clean of the label too — the same discipline
    // as the `.aml` sweep, in case a future capsule renders a verdict/label word rather than a marker.
    const headerRendered = await page.locator(".console__header").evaluate((root) => {
      const text = (root as HTMLElement).innerText;
      const labels = [...root.querySelectorAll("*")]
        .flatMap((el) => ["aria-label", "title", "alt"].map((a) => el.getAttribute(a)))
        .filter((v): v is string => v !== null);
      return [text, ...labels].join("\n");
    });
    for (const forbidden of [/fraud/i, /launder/i]) {
      expect(
        headerRendered,
        "THE HEADER RENDERS THE GROUND-TRUTH LABEL while the witness is up. The persistent rail must\n" +
          "carry no audit label onto the evidence surface.\n",
      ).not.toMatch(forbidden);
    }

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
  });
});

/*
 * THE COLLAPSED FEED RAIL — the header capsule's leak class, one shell element over.
 *
 * The 56px rail is COLOURLESS: it takes only counts, never a Decision. So BOTH static guards are
 * blind to it — the composition census never colours it (there is nothing to reach the audit layer
 * THROUGH), and the `.aml` sweep scopes inside the evidence surface, not the console shell. Its
 * safety rests ENTIRELY on the render-site: the rail lives inside the console arm of App's body
 * ternary, so view.kind==='aml' unmounts it exactly as it unmounts the full feed.
 *
 * The full feed's unmount is covered by the seam test below (it opens the witness FROM a feed row).
 * The collapsed RAIL is a SEPARATE render branch with no such coverage: a refactor that persisted
 * feedCollapsed into the evidence view — a rail reading "showing N of 1,500" beside an open witness —
 * would pass composition, data, AND the `.aml` sweep. This is the render-time assertion that closes
 * it, the same shape as the header-capsule test above.
 */
test.describe("the collapsed rail oracle boundary", () => {
  test("a collapsed feed rail is unmounted on the evidence surface", async ({ page }) => {
    const misses = installMock(page);
    await page.goto("/");
    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^aml/ }).click();

    // Select an AML decision (so the Investigation offers the interrogate door), THEN collapse the
    // feed. The rail carries no row and no "see why", so the witness is opened from the Inspector —
    // the exact path that leaves feedCollapsed=true crossing into the evidence view.
    const ring = SUBJECTS.aml_ring_txn_id;
    const item = page.locator(".feed__item", {
      has: page.locator(".feed__row", { hasText: ring.slice(0, 6) }),
    });
    await item.locator(".feed__row").first().click();
    await page.getByRole("button", { name: "Collapse the decision feed" }).click();

    // LIVENESS. The rail really renders on the console, so the "gone during evidence" assertion below
    // is not vacuously true over a rail that never appears. feedCollapsed + selectedId both persist
    // across the seam — exactly what a naive persist-the-rail refactor would keep rendering.
    await expect(
      page.locator(".console__rail"),
      "the collapsed feed does not render its rail on the console, so this guard would measure " +
        "nothing — the rail must be PRESENT here before we can prove it ABSENT on the witness.",
    ).toHaveCount(1);

    // Open the witness from the Inspector (the collapsed rail carries no control that could).
    await page.getByRole("button", { name: /Interrogate the transaction/ }).click();
    await expect(page.locator(".aml")).toBeVisible();
    await page.waitForSelector(".aml__witnesses", { state: "visible" });

    // THE INVARIANT. The whole console body — the rail with it — is unmounted while the witness is up.
    // A rail reading "showing N of 1,500" beside a witness is the header-capsule leak, one shell
    // element over: colourless, guard-blind, safe only by this render-site unmount.
    await expect(
      page.locator(".console__rail"),
      "A COLLAPSED FEED RAIL IS ON SCREEN BESIDE THE WITNESS. feedCollapsed persisted into the\n" +
        "evidence view and the 56px rail — 'showing N of <total>' — is co-mounted with the witness it\n" +
        "must never sit beside. Both static guards are blind to it (the rail is colourless); its only\n" +
        "protection is rendering inside the console arm of App's body ternary. Keep it there.\n",
    ).toHaveCount(0);
    await expect(page.locator(".console__body")).toHaveCount(0);

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
  });
});

/*
 * THE JUSTIFICATION SEAM (Rung 4) — the mirror of the oracle-boundary sweep above.
 *
 * That sweep proves the label is never on the EVIDENCE surface. This proves the witness is never on
 * the AUDIT surface (the feed). The two-graph separation is guarded from BOTH sides, in pixels.
 *
 * ============== WHY THIS EARNS A PUSH-COST THE COMPOSITION GUARD DOES NOT COVER ==============
 * The seam adds a "see why" control to the feed. Single-view-mount is architectural — App renders
 * exactly one body arm, so the feed and the witness surface are never mounted together, and reading
 * App.tsx proves it. But the ONE way that could regress is an inline witness PREVIEW rendered in the
 * feed row itself ("so users don't have to click"). If that preview fetched /interrogate raw and
 * drew SVG without a typed `AmlWitness` prop, the composition guard would MISS it: check C sees no
 * evidence-coloured component to flag, and `DecisionFeed` is not an EVIDENCE_MODULE for check B. So
 * the guard is blind to that channel — the check-C shape — and the only thing that catches it is
 * asserting the rendered feed has no witness geometry in its DOM. It CAN regress; it costs one
 * assertion on a feed this job already drives; so it is guarded.
 */
test.describe("the justification seam", () => {
  test("the feed renders is_fraud but never a witness", async ({ page }) => {
    const misses = installMock(page);
    await page.goto("/");
    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^aml/ }).click();
    await page.waitForSelector(".feed__row");

    // LIVENESS. The audit surface really does render the label here — otherwise "no witness" would
    // be a vacuous pass over a feed showing nothing (a fixture whose first page had zero fraud rows
    // would make this guard measure nothing, the ninth-vacuous-check shape). The feed marks fraud
    // with a dot (role="img", NO text), so an innerText check would miss it — the count is on the
    // element.
    expect(
      await page.locator(".feed__fraud-dot").count(),
      "the aml feed's first page has no is_fraud row, so this guard measures nothing — re-capture " +
        "with `python -m scripts.capture_console_fixtures`",
    ).toBeGreaterThan(0);

    // THE INVARIANT. No witness geometry is drawn on the audit surface. The witness (`.geo`, its
    // edges and nodes) belongs to the EVIDENCE surface alone; inlining it beside `is_fraud` would
    // put the graph's structure next to the answer key — the two-graph separation collapsed in
    // pixels, and past the composition guard's blind spot (see the header).
    await expect(
      page.locator(".geo"),
      "A WITNESS IS DRAWN ON THE FEED. The audit surface renders the graph's structure beside the\n" +
        "ground-truth label. The witness belongs to the evidence surface, reached by 'see why' — it\n" +
        "must never be inlined in the feed. The two layers are joined by a view transition, never by\n" +
        "co-mounting.\n",
    ).toHaveCount(0);
    await expect(page.locator(".geo__edge")).toHaveCount(0);

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
  });

  test("see why on a feed row opens the evidence surface and unmounts the feed", async ({ page }) => {
    const misses = installMock(page);
    await page.goto("/");
    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^aml/ }).click();

    // The seam's new, shallow trigger — a per-row control, NOT the deep Investigation path. Locate
    // the ring subject's row and click ITS "see why" (a sibling of the row button, so no invalid
    // nested-button).
    const ring = SUBJECTS.aml_ring_txn_id;
    const item = page.locator(".feed__item", {
      has: page.locator(".feed__row", { hasText: ring.slice(0, 6) }),
    });
    await item.getByRole("button", { name: /See the witness/ }).click();

    // A VIEW TRANSITION, not a co-mount: the evidence surface is up, and the feed — with every
    // is_fraud on it — is GONE. The same invariant the oracle-boundary sweep pins, reached through
    // the new trigger, closing the loop that single-view-mount guarantees by construction.
    await expect(page.locator(".aml__witnesses")).toBeVisible();
    await expect(page.locator(".feed__row")).toHaveCount(0);
    await expect(page.locator(".feed__fraud-dot")).toHaveCount(0);

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
  });

  test("returning from the witness restores a feed that STILL carries no witness", async ({ page }) => {
    // POST-BACK COVERAGE. The cold-render assertion above proves the feed carries no witness on first
    // paint. But the regression it guards — a witness inlined in the feed — could be introduced on a
    // path that only manifests AFTER a return: a "recently viewed witness" preview keyed on retained
    // state. `selectedId` IS retained across the seam (see why calls onInterrogate, never onSelect);
    // `useInterrogation` is cleared (it lives inside AmlConsole, which unmounts on back). A preview
    // keyed on either would render on the RETURNED-TO feed and be invisible to the cold check. So the
    // returned-to state is asserted, not assumed.
    const misses = installMock(page);
    await page.goto("/");
    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^aml/ }).click();

    const ring = SUBJECTS.aml_ring_txn_id;
    const item = page.locator(".feed__item", {
      has: page.locator(".feed__row", { hasText: ring.slice(0, 6) }),
    });
    await item.getByRole("button", { name: /See the witness/ }).click();
    await expect(page.locator(".aml__witnesses")).toBeVisible();

    // BACK to the console — the seam's return leg.
    await page.getByRole("button", { name: /back to the console/ }).click();
    await page.waitForSelector(".feed__row");

    // The feed is restored (is_fraud is back on the audit surface, where it belongs) and STILL draws
    // no witness. A witness that only appeared after a round trip would trip HERE and pass the cold
    // check — which is the whole reason this second assertion exists.
    expect(
      await page.locator(".feed__fraud-dot").count(),
      "the returned-to feed shows no is_fraud — the back navigation did not restore the audit surface",
    ).toBeGreaterThan(0);
    await expect(
      page.locator(".geo"),
      "A WITNESS IS DRAWN ON THE RETURNED-TO FEED. Something inlined the witness into the feed on the\n" +
        "post-back path — invisible to the cold-render check, visible here. The witness belongs to the\n" +
        "evidence surface, reached by 'see why', never on the feed.\n",
    ).toHaveCount(0);
    await expect(page.locator(".geo__edge")).toHaveCount(0);

    expect(misses, `the console made a request the fixtures do not cover:\n${misses.join("\n")}`).toEqual([]);
  });
});
