/*
 * RestoreHint — THE RESTORE PROCEDURE HAS EXACTLY ONE DEFINITION IN THIS FRONTEND, AND IT IS HERE.
 *
 * History, because the shape of this bug is the reason the component exists:
 *
 *   * Restore instructions in this repo have lied FOUR times. Three were sentences a human wrote
 *     (README setup, DEMO pre-flight, DEMO reset note). The fourth was worse: a FIFTH and SIXTH
 *     site found by the pre-push sweep (2026-07-13), both RENDERED IN THE PRODUCT inside the
 *     honesty ledger — the one surface whose entire job is to be trustworthy. Each named a SINGLE
 *     command, and each single command is a way to break the world:
 *       - `seed.backfill_decisions` alone restores the CARD world and silently leaves the grounding
 *         seam empty (it opens with a reseed, and `seed.seed()` DELETEs every row of `decisions`);
 *       - `seed.backfill_aml_decisions` alone REFUSES with exit 1, because the card backfill has
 *         not gone first.
 *     The sweep's fix was a single shared definition, and its note closed with: "A SEVENTH SITE IS
 *     A BUG."
 *
 *   * It was a bug. `DecisionFeed`'s empty state said "Rerun the backfill to populate the feed" —
 *     singular, unnamed, and therefore the destructive half-restore. Prose review had missed it for
 *     the same reason it missed the fifth and sixth: it is not prose in a doc, it is a value a
 *     component renders. So the definition moved OUT of HonestyLedger and into this module, which
 *     is the only place the procedure is written down in the frontend.
 *
 *   * It was a bug again, and the EIGHTH site was the worst of all of them: the CONSISTENCY DEMO's
 *     confirmation gate — the screen you read while agreeing to TRUNCATE AND RESEED the cluster —
 *     said the fleet "will read empty until it is re-backfilled (`python -m seed.backfill_decisions`)".
 *     One command, handed to the one operator GUARANTEED to need the procedure, at the moment of
 *     maximum consequence, for a demo that destroys the AML decisions too. Following it leaves the
 *     grounding seam dead.
 *
 * The defence is not a better sentence. It is one definition, imported — and now an executable
 * assertion (`tests/test_restore_instructions.py`) that greps for the SHAPE across every doc and
 * every component, so a ninth site fails CI instead of being found by the next sweep. If you are
 * about to write a restore instruction anywhere in this app: import this instead.
 */

/** The two ordered commands, and NOTHING else — composable into any sentence. Naming only the
 *  first is the destructive half-restore; that is the bug this module exists to make unwritable. */
export const RestoreCommands = () => (
  <>
    <code>python -m seed.backfill_decisions</code> → <code>python -m seed.backfill_aml_decisions</code>
  </>
);

export const RestoreHint = () => (
  <>
    empty — run <RestoreCommands />, in that order (the first alone leaves the seam empty; the
    second alone refuses)
  </>
);
