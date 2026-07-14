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
import { installMock, SUBJECTS } from "./mock.js";

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
  await page.locator(".feed__row", { hasText: "txn-w7-p0207" }).first().click();

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
    await page.locator(".feed__row", { hasText: "txn-w7-p0207" }).first().click();
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

test.describe("the oracle boundary", () => {
  test("the evidence surface never renders the label", async ({ page }) => {
    const misses = installMock(page);
    await page.goto("/");

    const filter = page.getByRole("group", { name: "Filter decisions by kind" });
    await filter.getByRole("button", { name: /^aml/ }).click();

    // Enter through the moat — the decision feed, where `is_fraud` legitimately lives — and click
    // through to the evidence layer. The seam hands over a bare UUID and nothing else.
    await page
      .locator(".feed__row", { hasText: SUBJECTS.aml_transaction_id.slice(0, 6) })
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
});
