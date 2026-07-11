# DEMO.md — Hero Demo Storyboard (Roadmap Item F)

This is the **demo spine**: the verified substance a 90-second video or live walkthrough is built
from. It is *not* the video — recording is a human task (see the README's
`<!-- TODO: demo video link -->` placeholder). Everything below is real, and every beat is tagged
with how it was verified.

---

## The one decision this document is built on: two acts, one theme, one substrate — **not** one causal chain

Lineage keeps two kinds of agent memory, on two different graphs, in one CockroachDB cluster:

- **The evidence graph** it *reads* — the AML money-flow layer (`aml_transactions`, accounts as
  nodes, transactions as edges). This is where it **detects** and where a click **interrogates**.
- **The genealogy graph** it *inherits* — the belief/agent moat (`beliefs`, `belief_inheritance`,
  `decisions`). This is where a rule is **traced**, **time-travelled**, **invalidated atomically**,
  **provenance-audited**, and **counterfactually reversed**.

These two graphs meet nowhere in the current data, and that is a verified fact, not an oversight:
`decisions.aml_transaction_id` does not exist, the one belief is a card-authorization heuristic
(`"merchant category 5411 under $180 is safe if account age > 6 months"`), and **no decision in
this system has ever cited an AML transaction** (established from source under NOTES "Roadmap Item
6"). So the demo does **not** claim one fraud ring is caught, then invalidated, then reversed as one
causal story — asserting that a six-hop cycle among bank accounts justifies killing a rule about
merchant category 5411 would be a document in which every field is individually true and the
juxtaposition is fabricated. Item 6 already considered and refused exactly that.

The two acts are instead the **same story told on two graphs**, joined honestly by:

- **Theme** — *a claim is only as good as the evidence you can re-derive; correct without
  overclaiming.* Act 1 is that discipline on the evidence graph (a FLAG needs a structural witness,
  or the honest answer is "insufficient coverage"). Act 2 is the same discipline on the genealogy (a
  rule's authority is its **measured** current performance, not its pedigree).
- **Substrate** — one CockroachDB cluster, one MVCC timeline: `AS OF SYSTEM TIME` time-travel,
  distributed vector search, and an atomic cross-key transaction. "One transactional store spanning
  graph + vectors + time-travel" is the project's whole thesis against Postgres + Pinecone.

> **A genuinely-honest single causal chain is buildable**, but it is a five-table-moat migration + a
> second belief + agent runs + a backfill (the exact five steps under NOTES "Roadmap Item 6 → THE
> HONEST PATH TO A REAL (b)"), all explicitly out of Item F's scope. It is flagged as a strong
> future addition, deliberately not forced here.

---

## Legend — how each beat is verified (the Item-9 LIVE/STATIC discipline, applied to the storyboard)

Two axes matter and are kept apart: **is the beat performed live during the demo, or is it a cited
number/artifact?** and **was it re-confirmed fresh this session, or is it a historical value?** The
three tags below carry both:

| Tag | Performed live? | Freshness |
|---|---|---|
| **⬤ LIVE** | Yes — run on camera during the demo | Command + preconditions verified **fresh this session** (2026-07-11); result in the Verification Log. |
| **✔ FRESH-REFERENCED** | No — cited as a number/artifact in narration | But **re-run fresh this session**; the exact command and captured value are in the Log. |
| **◐ HISTORICAL-REFERENCED** | No — cited as a number/artifact | A real recorded value from a **prior session**, **not** re-run this session (paid/non-deterministic, needs external creds, or destructive). The when/where is stated so it is never mistaken for a live measurement. |

This mirrors exactly the distinction Item 9's honesty ledger made a first-class UI concept: never let
a cited historical number read as a live one — and never let a freshly-re-confirmed one be dismissed
as stale.

---

## Pre-flight (demo prep — do this before recording, not on camera)

1. **Restore console state** (the feed, the staleness curve, the counterfactual, and the certificate
   staleness all read `decisions` / `belief_performance`, which are empty on a fresh cluster):

   ```bash
   PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe -m seed.backfill_decisions
   ```

   Deterministic (~4–5 min): restores 24 agents, 1 **active** belief, 8 inheritance edges, 4,000
   decisions, and 8 performance windows (confidence `0.924 → 0.528`, byte-identical every run).

2. **Bring the stack up:**

   ```bash
   PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
   cd frontend && npm run dev        # http://localhost:5173  (CORS allows 5173 only)
   ```

3. Confirm `GET http://localhost:8000/health` → `{"status":"ok"}` and the console loads at
   `http://localhost:5173`.

**Budget ≈ 90 seconds of narration.** Beat timings below sum to ~90s; stage directions and commands
are for the operator, not spoken.

---

# ACT 1 — Detection on the evidence graph (~32s)

*The agent reads a money-flow graph and refuses to flag without a witness it can re-derive.*

### Beat 0 — Cold open (~8s)

- **On screen:** the problem framing (README's gen0→gen7 diagram, or the console header).
- **Narration:** *"AI fraud agents inherit each other's memory. Lineage keeps two kinds — the
  evidence it reads, and the beliefs it inherits — both on one CockroachDB cluster. Watch it stay
  honest on both."*

### Beat 1 — The detection numbers, framed honestly (~8s)

The headline is **not** "we catch what the baseline misses" — verified fresh, that claim is false and
the roadmap's premise does not survive contact with the data.

- **✔ FRESH-REFERENCED** *(re-run fresh this session; see Log #1)*:
  `PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/eval_detection.py`
- **The honest facts to state (all re-confirmed this session):**
  - The **naive** single-feature baseline (`payment_format == ACH`) has **100% recall** — it misses
    *nothing*; the oracle-fit logistic regression even **out-recalls and out-F1s** the structural
    detector on the hold-out (logreg R 80.6% / F1 78.7% vs structural R 65.3% / F1 77.1%).
  - Structure's real, leak-independent edge is **precision + auditability**: hold-out **CYCLE recall
    100% (38/38), precision 100% (38/38), Wilson 95% lower bound 90.8%**; the witness uses no format
    field, so it does not ride the synthetic ACH artifact (real-population "flag all ACH" precision =
    **0.75%**).
- **Narration:** *"The detector isn't magic — a dumb 'flag every ACH transfer' rule catches more
  rings. What it can't do is tell you why. Lineage only flags when the graph itself hands it a path
  it can re-derive — and on fresh data it does that at 100% precision, riding no format leak."*

### Beat 2 — The hero: interrogate a real laundering cycle (~12s)

- **On screen (terminal, or `/docs`):**

  **⬤ LIVE — re-confirmed this session** *(Log #2)*:
  ```bash
  curl -s "http://localhost:8000/aml/transactions/045adfd2-a822-566f-9cd2-6a17fc150539/interrogate"
  ```
  Returns `CYCLE MATCH`, `kind: RING`, `flag_capable: true`, and a **re-derivable 10-hop closed
  ring**: `045adf → 148a71 → d3b7bc → 1579aa → d76933 → 07ffb8 → 609cd1 → 291bb1 → c793d7 → 13f812`
  (→ back to `045adf`). This subject is a real `is_laundering=true` CYCLE member — instance 6, a
  clean 10-account single-component cycle (`num_components=1`); `has_competing_structure=false` and
  all three other typologies return `CONCLUSIVE_NO`. Deterministic, no LLM, free, replayable.
- **Narration:** *"Here's a real laundering ring. Click any hop and the graph re-derives the whole
  cycle — ten real transactions, closing back on themselves. That path is the witness. No path, no
  flag."*

### Beat 3 — The honest cost, and honest uncertainty (~7s)

- **⬤ LIVE — re-confirmed this session** *(Log #3)*:
  ```bash
  curl -s "http://localhost:8000/aml/transactions/3cda6d1d-f765-5001-9342-0478b1a92232/interrogate"
  ```
  This transaction is **benign** (`is_laundering=false`) yet CYCLE **and** GATHER-SCATTER **and**
  STACK all witness it (`has_competing_structure: true`) — it *would* flag. It is the honest face of
  CYCLE's 75.4% dev precision. The same response shows SCATTER-GATHER returning `INCONCLUSIVE` with a
  **named boundary account** — the search hit the edge of the ingested extract and says so rather
  than guessing.
- **Narration:** *"And it shows its costs. This one is benign, but three structures fire on it — so
  it would flag, and the console says so. When the search runs off the edge of the data, it doesn't
  guess; it says 'insufficient coverage' and names where it stopped."*

### Beat 4 — The narration guard (~5s, reference)

- **◐ HISTORICAL-REFERENCED** *(Item 8 full run + Item E live gemma demo, 2026-07-11;
  not re-run this session — needs Ollama + the scoped-TLS workaround Item E flagged)*:
  the agent's prose rationale is scored for faithfulness. On Item E's real 6-hop CYCLE anchor
  (`185f748d…`, a different real ring from the hero above), a faithful
  rationale scored **1.00 (SUPPORTED, prose shown)**; an authored "within a 24-hour window"
  fabrication scored **0.40 (UNSUPPORTED, prose withheld, deterministic reconstruction shown)**; an
  unreachable judge → **UNAVAILABLE, fail-closed**. Source: `scripts/demo_faithfulness_guard.py`.
- **Narration:** *"Even the model's own explanation is checked. If the prose claims more than the
  rows support, it's withheld and replaced with the facts."*

---

## TRANSITION (~5s) — theme + substrate, no causation

> **Discipline:** the script says nothing like "and *because* of that fraud ring…". The two graphs
> share a theme and a cluster, never a causal thread. The transition states exactly that.

- **On screen:** switch from the terminal/AML view to the console (`localhost:5173`).
- **Narration (verbatim, non-causal):** *"That was the memory Lineage **reads**. It keeps a second
  kind it **inherits** — not transactions, but beliefs, passed down a family tree of dead agents.
  Same cluster, same rule: authority isn't correctness. Here, the evidence is time."*

---

# ACT 2 — Governance on the genealogy graph (~40s)

*A rule inherited across eight generations went stale — proven from data, corrected in one commit.*

### Beat 5 — The inherited, stale belief (~8s)

- **On screen:** the console — decision feed (worst-case window-7 density), genealogy tree
  (crimson spine gen0→gen7, azure mirror), Inspector. Click a window-7 decision → **Investigate**:
  the driving belief resolves, tagged **inherited** (the deciding agent is not the originator).
- **⬤ LIVE — re-confirmed this session** *(Log #4, the data the console renders)*. The belief is
  `898ad0e5…` — `"merchant category 5411 under $180 is safe if account age > 6 months"`, formed by
  crimson-0 (`108cf7…`), acted on by the living crimson-7 (`3fb55c…`).
- **Narration:** *"This living agent just approved a transaction on a rule it inherited — a rule a
  founding ancestor formed, that no living agent remembers making."*

### Beat 6 — Trace to the origin (the signature animation) (~8s)

- **On screen:** **Trace** — warmth spreads backward edge by edge through the cold, dead tree,
  igniting the origin ancestor. Conclusion: *"originated with crimson-0 — 7 generations ago."*
- **⬤ LIVE — re-confirmed this session** *(Log #5)*:
  ```bash
  curl -s "http://localhost:8000/beliefs/898ad0e5-b4f8-5863-abe3-4145c9b5af68/lineage"
  ```
  Real recursive CTE over `belief_inheritance`: **9 nodes**, origin crimson-0, with the **fork at
  depth 5** (the living branch holder crimson-5b `cd75b3…` alongside the dead spine crimson-5).
- **Narration:** *"Trace it back through the family tree — every hop a real inheritance edge — to the
  ancestor that formed it, seven generations up."*

### Beat 7 — Time-travel: valid then, rotten now (~8s)

- **On screen:** **Time-travel** — the measured curve (big number `0.92` healthy → `0.53` stale) +
  the real `AS OF SYSTEM TIME` deposition showing the belief `held · ACTIVE` (one immutable row —
  MVCC proves it never changed).
- **⬤ LIVE — re-confirmed this session** *(Log #6)*:
  ```bash
  curl -s "http://localhost:8000/beliefs/898ad0e5-b4f8-5863-abe3-4145c9b5af68/performance"
  ```
  8 measured windows: confidence `0.924 → 0.528`, frauds_approved `19 → 118`, with the real gen-6
  recession dip (`0.556 → 0.624 → 0.528`) a monotone curve could never fake. **Measured from
  `belief_performance`, never hardcoded.**
- **Narration:** *"The row never changed — time-travel proves that. But its measured performance
  rotted: 92% confidence when formed, 53% today. Same rule, drifting world."*

### Beat 8 — Counterfactual: what earlier action would have been worth (~6s)

- **⬤ LIVE — re-confirmed this session** *(Log #7)*:
  ```bash
  curl -s "http://localhost:8000/beliefs/898ad0e5-b4f8-5863-abe3-4145c9b5af68/counterfactual-invalidation?at=2025-05-27"
  ```
  T = 2025-05-27 (window-4 start, where confidence first cracks `0.852 → 0.724`): **N = 1000**
  belief-driven approvals withdrawn, **M = 392** of them real fraud, across **5 holders**. Exact
  (each window is exactly 250 rows), reported as *approvals-withdrawn* — never a fabricated "fraud
  we'd have caught."
- **Narration:** *"Had we killed this belief when the data first cracked, 1,000 downstream approvals
  lose their justification — 392 of them real fraud."*

### Beat 9 — INVALIDATE: the atomic climax (~10s)

- **On screen:** the confirmation-gated **Invalidate** — the closure reveal (both living holders,
  the spine tip **and** the branch), then both flip to corrected in **one shared transition** (the
  simultaneity *is* the single-commit message). The real certificate outcome is shown: sealed
  pre-kill state, `certificate_status`, full sha256, S3 key, snapshot HLC.
- **⬤ LIVE ON CAMERA — the one governed, destructive write.** Contract + preconditions verified
  fresh this session (Log #8); the destructive fire itself is the on-camera action (covered by 4
  passing `test_atomic_invalidation` tests + Item 6's recorded end-to-end):
  ```
  POST http://localhost:8000/beliefs/898ad0e5-b4f8-5863-abe3-4145c9b5af68/invalidate
  Body: {"actor_id": "5e5e0000-0000-4000-8000-000000000001"}
  ```
  One serializable CockroachDB transaction flips the belief **and** all 8 closure edges at once — a
  set-based `UPDATE`, no per-holder loop — then writes a tamper-evident certificate to S3.
- **◐ HISTORICAL-REFERENCED** *(real AWS Lambda invocation 2026-07-10; **not** re-invoked
  this session, per plan)*: an independent **Lambda certifier**, in a different language stack,
  replays the pre-kill world `AS OF SYSTEM TIME` and re-derives the closure content-hash with the
  *shared* canonicalizer. Endpoint (async SQLAlchemy, Windows) and Lambda (sync psycopg, Linux)
  produced the **same** hash from different machines and different reads:
  `sha256:1e40b7a72fe1796cc91fa49bd119e1f239c889c651fc7dbaa70963eb38c393ff`.
- **Narration:** *"So the supervisor kills it — the belief and every living holder, in one
  CockroachDB commit. Then a second machine on AWS replays the pre-kill world from scratch and
  re-derives the same hash. Two stacks, one truth. That's the correction, and the proof it can't
  lie."*

> ### ⚠️ Production reset note — for whoever records this (read before doing multiple takes)
>
> The Invalidate beat is **destructive by design**: it consumes the active belief
> (`status → invalidated`), leaves `belief_performance`/`decisions` intact but the belief dead, and
> writes a real S3 certificate. **Between takes**, restore with:
>
> ```bash
> PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe -m seed.backfill_decisions
> ```
>
> That reseeds the genealogy (24 agents, belief back to **active**, 8 inheritance edges) and
> repopulates 4,000 decisions + 8 performance windows (curve `0.924 → 0.528`). ~4–5 min,
> deterministic. **You cannot re-record Beats 6–9 without it** — a dead belief has no staleness
> curve and the Invalidate returns `409 already-invalidated`. Do **not** fire two backfills at once
> (documented `TRUNCATE`/schema-change collision). The console's **Invalidate** is the *only* console
> action that requires this reset; the isolated consistency demo (below) does not.

### Beat 10 — (optional supporting exhibit) atomic vs eventual, made visible

- **◐ HISTORICAL-REFERENCED** *(SSE capture, Frontend Phase 4 + `consistency.py`;
  live-safe to run but not re-run this session)*. The console's Consistency view streams the real
  observer samples of the **eventual** per-holder fan-out (the SPLIT window opens — some holders
  still live on the dead belief) versus the **atomic** path. Commit points: **eventual 9 vs atomic
  1**. It runs entirely in the **isolated `demo` database** (Roadmap Item 0), so — unlike Beat 9 — it
  needs **no** backfill afterward.
  ```bash
  curl -sN "http://localhost:8000/demo/consistency/stream?strategy=eventual"   # ~7s, isolated demo db
  ```
- **Narration (if used):** *"The same kill done the naive way — holder by holder — opens a torn
  window where some agents still trust a dead rule. Atomic closes it in one commit. Nine commit
  points versus one."*

---

## CLOSING (~5s) — the shared theme, stated as the honest seam

- **Narration (verbatim, names the non-causal structure):** *"Two memory surfaces, one CockroachDB
  cluster. On the evidence graph, a flag needs a re-derivable witness or it admits it doesn't know.
  On the genealogy, a rule that rotted is corrected across every living holder in one commit, with a
  certificate a second machine re-derives from scratch. Different graphs, no shared causal thread —
  the same refusal to claim more than the data proves."*

---

## Build-time Verification Log (fresh, 2026-07-11)

Every ⬤ LIVE beat above was run this session; the captured results:

| # | Beat | Command / endpoint run this session | Result (fresh) |
|---|---|---|---|
| 1 | Detection numbers | `scripts/eval_detection.py` | Reproduced byte-for-byte: naive ACH rule **R 100%** (misses nothing); hold-out logreg **F1 78.7 > structural 77.1**, structural **P 94.2 > 76.9**; CYCLE hold-out **R/P 100% (38/38), Wilson floor 90.8%**; all positives 100% ACH, benign spans 6 formats |
| — | Deterministic ids | `uuid5` probe | belief `898ad0e5…`, crimson-0 `108cf7…`, crimson-7 `3fb55c…`, crimson-5b `cd75b3…` — all match |
| 2 | Interrogate hero | `GET /aml/transactions/045adfd2…/interrogate` | `CYCLE MATCH · RING · flag_capable · 10 hops (closed)`: `045adf→148a71→d3b7bc→1579aa→d76933→07ffb8→609cd1→291bb1→c793d7→13f812`; `has_competing_structure=false`; instance 6, `num_components=1`; oracle `is_laundering=true`. (The 6-hop `2f1c1d6c…` is an equally-real but weaker alternate: fewer hops, and its SCATTER-GATHER returns `INCONCLUSIVE` rather than `CONCLUSIVE_NO`.) |
| 3 | Benign-cost exhibit | `GET /aml/transactions/3cda6d1d…/interrogate` | oracle `is_laundering=false`; CYCLE(10-hop)+GATHER-SCATTER+STACK all `MATCH`; `has_competing_structure=true`; SCATTER-GATHER `INCONCLUSIVE` w/ named boundary |
| — | Backfill (prep) | `python -m seed.backfill_decisions` | 4,000 decisions, 8 windows, conf **0.924→0.528**, gen-6 dip (0.556→0.624→0.528) intact, frauds_approved 19→118 |
| 4 | Investigate data | `GET /beliefs` (active) | 1 active belief `898ad0e5…` |
| 5 | Trace / lineage | `GET /beliefs/898ad0e5…/lineage` | 9 nodes, origin crimson-0, **fork at depth 5** (crimson-5b alive + crimson-5 dead), 2 living holders |
| 6 | Time-travel / perf | `GET /beliefs/898ad0e5…/performance` | 8 windows `0.924→0.528`, frauds_approved 19→118 |
| 7 | Counterfactual | `GET /beliefs/898ad0e5…/counterfactual-invalidation?at=2025-05-27` | **N=1000, M=392**, 5 holders, total_belief_driven=2000 |
| — | Provenance audit | `GET /beliefs/898ad0e5…/provenance-audit` | **CLEAN**, 8 edges, 0 anomalies |
| 8 | Invalidate contract + preconditions | source read + live reads | `POST {actor_id}` → `InvalidateResponse` (pre_invalidation_state + certificate outcome + content_hash + snapshot_hlc); preconditions fresh: belief **active**, closure **8 edges**, **2 living holders** |
| — | Frontend build | `tsc -b` / `oxlint` / `vite build` | all exit 0 (the >500 KB three.js chunk warning is the documented, accepted Phase-5 bundle) |

**◐ HISTORICAL-REFERENCED (NOT re-run this session):**

| Artifact | Value cited | Source / when |
|---|---|---|
| Cross-machine certificate hash | `sha256:1e40b7a72fe1796cc91fa49bd119e1f239c889c651fc7dbaa70963eb38c393ff` | Real AWS Lambda invocation, `scripts/demo_certifier.py`, 2026-07-10 (README §Core innovations #2). Not re-invoked per plan. |
| Faithfulness guard scores | SUPPORTED 1.00 / UNSUPPORTED 0.40 / UNAVAILABLE; GEval 0.287 vs built-in 0.771 on 8 authored negatives; 8/10 labeled | Item 8 full run + Item E live gemma demo, 2026-07-11. Needs Ollama + scoped-TLS; not re-run. |
| LLM FLAG verdict | gpt-4o-mini FLAG citing the origin belief / a real cycle | `scripts/demo_grounded_agent.py`. Paid + non-deterministic; referenced, not re-fired. |
| Consistency 1-vs-9 | eventual 9 commit points vs atomic 1; SPLIT window | SSE capture (Frontend Phase 4) + `app/services/consistency.py`. Live-safe (isolated `demo` db); not re-run this session. |

---

## Live-vs-referenced decision, per beat (rationale)

| Beat | Choice | Why |
|---|---|---|
| Detection numbers (1) | Reference (re-run fresh) | Deterministic, read-only, ~20s; cite the crosstab so anyone can check the ACH artifact. |
| Interrogate hero + cost (2, 3) | **Live** | Deterministic, free, offline-replayable; no OpenAI, no reseed. Safe to hammer. |
| Faithfulness guard (4) | Reference | Paid-adjacent (Ollama up) + the scoped-TLS caveat; nothing new shown by re-running. |
| Console reads: Investigate/Trace/Time-travel/Counterfactual (5–8) | **Live** | Read-only GETs, no cost, no writes; require the backfill (prep). |
| **Invalidate (9)** | **Live on camera** | The kill-shot and the whole thesis. Destructive → the reset note. |
| Certifier cross-machine hash (9) | Reference | Real AWS compute; the recorded agreement is the proof — no need to re-invoke the Lambda on camera. |
| Consistency demo (10) | Reference (live-safe) | Isolated `demo` db, so safe to run live if wanted; numbers cited from the real capture. |

---

## Not in scope for Item F (confirmed)

No migration, no new persisted table, no touch to the regulatory corpus, no Item C/D work, and no
new frontend surface. The AML act (Act 1) is narrated through the existing terminal / `/docs` /
demo scripts — an **AML console is deferred entirely** as a strong future addition (its own
plan-gated frontend ladder, per Item 5's and Item 9's own sizing), not folded into F.
