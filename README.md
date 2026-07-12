<div align="center">

<img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&size=22&duration=3600&pause=900&color=E8A33D&center=true&vCenter=true&width=860&lines=Lineage%3A+agent-genealogy+belief-inheritance+forensics;A+living+agent+acts+on+beliefs+inherited+from+ancestors+it+never+met;No+flag+without+a+witness+the+graph+itself+can+re-derive" alt="Lineage" />

**Belief-inheritance forensics for AI fraud-detection fleets, on CockroachDB.**

[![CI](https://github.com/Asembris/Lineage/actions/workflows/ci.yml/badge.svg)](https://github.com/Asembris/Lineage/actions/workflows/ci.yml)
&nbsp;![tests](https://img.shields.io/badge/tests-118%20passing-brightgreen)
&nbsp;![license](https://img.shields.io/badge/license-MIT-blue)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
&nbsp;![CockroachDB](https://img.shields.io/badge/CockroachDB-v25.4-6933FF?logo=cockroachlabs&logoColor=white)
&nbsp;![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
&nbsp;![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
&nbsp;![AWS](https://img.shields.io/badge/AWS-S3%20%2B%20Lambda-232F3E?logo=amazonaws&logoColor=white)

<!-- TODO: demo video link once deployed -->

</div>

---

## The problem

AI fraud-detection agents spawn, work, and die. When an agent retires it passes its learned
**beliefs** — rules like *"merchant category 5411 under $180 is safe if account age > 6 months"* —
down to the next generation. A living agent therefore acts on beliefs it **inherited from ancestors
it never met**. A belief that was correct when a founding ancestor formed it can silently go
**stale** across generations, until a living agent approves fraud because of a rule a long-dead
agent created under conditions that no longer hold.

```mermaid
graph LR
    G0["crimson-0<br/>gen 0 · DEAD<br/><i>forms the rule</i>"]
    G1["crimson-1<br/>gen 1 · DEAD"]
    G2["crimson-2<br/>gen 2 · DEAD"]
    DOTS["…<br/>gen 3–6 · DEAD"]
    G7["crimson-7<br/>gen 7 · <b>ALIVE</b><br/><i>acts NOW on a rule<br/>it never formed</i>"]

    G0 -->|inherits| G1 -->|inherits| G2 -->|inherits| DOTS -->|inherits| G7

    subgraph clocks["TWO CLOCKS — Lineage never conflates them"]
        MVCC["<b>MVCC time</b><br/>one immutable row<br/>AS OF SYSTEM TIME proves<br/>it NEVER changed"]
        MEAS["<b>Measured staleness</b><br/>confidence 0.92 → 0.53<br/>aggregated from belief_performance<br/>QUERIED from data, never hardcoded"]
    end

    G0 -.->|"the belief:<br/>valid THEN"| MVCC
    G7 -.->|"the same belief:<br/>rotten NOW"| MEAS

    style G0 fill:#3a2a1e,stroke:#ae8c6b,color:#f7eee8
    style G7 fill:#1e3a2e,stroke:#3FE0A8,color:#e8eef7
    style G1 fill:#121821,stroke:#243040,color:#5A6678
    style G2 fill:#121821,stroke:#243040,color:#5A6678
    style DOTS fill:#121821,stroke:#243040,color:#5A6678
    style clocks fill:#1e2a44,stroke:#6b8cae,color:#e8eef7
```

The row never changes; only the world does. That gap is the entire product:

- **MVCC time** — the belief is one immutable row. `AS OF SYSTEM TIME` proves it *never changed*.
- **Measured staleness** — the belief's real performance is aggregated from outcome data over time
  windows. "Valid then, rotten now" is **queried from data**, never a hardcoded confidence drop.

When a bad decision surfaces, the supervisor traces it back through the family tree to the origin
belief, sees it was valid-then / rotten-now, and **invalidates it atomically across the whole living
fleet in one transaction**. CockroachDB — distributed vector index, real time-travel, atomic
cross-key transactions — is what makes this hard to fake on a Postgres + Pinecone stack.

---

## Core innovations

### 1 · The brake: a FLAG can never fire without a witness

The grounded AML agent may only raise a fraud FLAG if the money-flow graph *itself* produces a
structural witness — a real, re-derivable path. A negative is **conclusive** only if the search
closed without hitting the boundary of the ingested extract; otherwise it is honest uncertainty.

```
CYCLE witness search over all 1,500 evidence edges (live re-run):
   MATCH ............ 57   (43 real CYCLE members + 14 benign)    →  FLAG (witness required)
   CONCLUSIVE_NO .... 463  (search closed inside the extract)     →  NO_FLAG
   INCONCLUSIVE ..... 980  (hit a sink — the extract boundary)    →  INSUFFICIENT_COVERAGE
                    ─────
                    1,500   →  precision 43/57 = 75.4% (14 benign FPs is CYCLE's honest cost)
```

The model's *own* cited path is re-derived from the rows; real evidence plus an unfaithful citation
is still withheld. Structure — not the citation, not a label column — is the sole authority.
→ `app/services/verdict_guard.py`, `app/services/aml_graph.py`

### 2 · A certificate two machines independently agree on

Invalidation writes a tamper-evident certificate to S3. A **Lambda certifier**, running on separate
AWS compute in a different language stack, replays the pre-kill world `AS OF SYSTEM TIME`,
re-derives the closure content-hash with the *shared* canonicalizer, and reports agreement as a
tri-state (`agreed` / `disagreed` / `unavailable` — a missing counterparty never reads as a pass).

```
endpoint  (async SQLAlchemy, Windows)  ┐
                                        ├─▶  sha256:1e40b7a72fe1796cc91fa49bd119e1f2…c393ff
Lambda    (sync psycopg, Linux)        ┘     SAME hash, different machines, different reads
```

Hash-coverage proves a document has not *changed*; the AOST replay against CockroachDB's own MVCC
history is what proves it was *true*. → `lambda/certifier/handler.py`, `app/services/certificate.py`

### 3 · Structural detection wins on precision — and loses on F1, which we print anyway

On a **genuinely fresh, account-disjoint hold-out no design decision ever saw**, the frozen
structural detector reaches **CYCLE recall 100% (38/38), precision 100% (38/38) — Wilson 95% CI
lower bound 90.8%**, and SCATTER-GATHER precision 89.6%.

It does **not** beat the baselines on F1, and we will not pretend otherwise: an oracle-fit logistic
regression **out-scores it on the hold-out** (F1 78.7 vs 77.1), and a one-line `payment_format ==
ACH` rule catches **every ring member there is** (100% recall). Both do it by riding a synthetic
generation artifact — the selected positives are 100% ACH, while on the real population "flag all
ACH" has **0.75% precision**. The structural witness uses **no format field at all**, so its
advantage — precision, and an auditable path you can re-derive — is the leak-independent one.
[The full three-way comparison is below](#the-baseline-is-not-a-strawman), printed every run.
→ `scripts/eval_detection.py`

---

## Judging criteria alignment

> Answered against real, file-linked facts. **Every claim carries its own limitation in the same
> row** — an honest "we used X, not Y" is stronger than an overclaim a judge can disprove in one
> question. Nothing in the right-hand column is boilerplate; each one is a real thing we checked and
> could not truthfully claim.

| Criterion | What we built | Where it lives | Honest limitation |
|---|---|---|---|
| **Agentic Memory Design**<br/><sub>*meaningful, production-grade memory layer?*</sub> | Three deliberately-separated schemas on **one MVCC timeline** — the five-table genealogy moat, a 1,500-edge AML evidence layer, a 1536-dim vector RAG corpus. Memory is queried transactionally (recursive CTE over `belief_inheritance`), aggregated (`belief_performance` windows), embedded (CRDB-native cosine search), and time-travelled (real AOST). | `models.py` · `aml_models.py` · `corpus_models.py` · `lineage.py:19` | The vector index is a **proven mechanism, not indexing at scale**. At 4 corpus rows the planner correctly brute-forces a full scan — `verify_corpus.py` asserts that plainly on every run rather than hiding it. |
| **Technical Implementation**<br/><sub>*correct, safe use of the CRDB tools?*</sub> | AOST timestamps validated and inlined as literals while the SELECT stays parameterized (they cannot be bind params in CRDB); invalidation is one serializable txn with a set-based closure `UPDATE` + a `FOR UPDATE` idempotency guard, never a per-holder loop; vector DDL uses the `CREATE VECTOR INDEX` (C-SPANN) escape hatch Alembic can't emit. | `time_travel.py:97` · `invalidation.py:88,140` · `migrations/0002:42` | **MCP Server configured, not exercised.** `.mcp.json` declares a CockroachDB Cloud MCP Server and it is available in-session, but every cluster-capability check in this project was done with **direct SQL probe scripts** (`scripts/probe_crdb.py`). The **ccloud CLI was not used at all.** |
| **Real-World Impact**<br/><sub>*how big an impact on real workflows?*</sub> | Belief inheritance is a real emerging failure mode: a rule formed under one regime outlives its validity and no living agent remembers forming it. Lineage makes it auditable and correctable — trace a bad decision to its origin belief, prove valid-then/rotten-now **from data**, correct the whole fleet in one commit, emit an S3 certificate an independent auditor re-verifies. Scored on **IBM's real 5M-transaction AML dataset**. | `lineage.py` · `counterfactual.py` · `s3_audit.py` | Scored against pattern-typology **ring membership**, not general fraud detection — and against a *deliberately adversarial* benign set (noise anchored to the same accounts as the fraud). Both caveats travel with every number below. |
| **Production Readiness**<br/><sub>*secure, resilient, access-controlled?*</sub> | Bounded exponential-backoff retry on transient CRDB errors at the two mutating surfaces; concurrency-safe per-IP/route rate limiter; nil-actor rejection on the one governed write; a **physically isolated `demo` database** so the destructive demo cannot touch console state; CORS allow-list; gitignored secrets; CI running the full suite against the real cluster on every push. | `resilience.py` · `ratelimit.py` · `demo_db.py` · [details](#production-readiness--security) | The cluster is **single-region** (`aws-eu-central-1`). What is *demonstrated* is atomic **cross-key / cross-holder** invalidation at one commit, measured against an eventual baseline. Atomic **cross-*region*** is CockroachDB's documented property, **argued here, not measured** — we say so rather than let the distributed-DB framing imply it. |
| **Creativity & Originality**<br/><sub>*a genuinely new idea about agentic systems?*</sub> | An agent's *inherited memory* is a distinct object from its code or its current state: it has a genealogy, a formation time, a **measured** decay, and a blast radius when wrong. The two-clock staleness model, the witness-required brake, and the cross-machine hash agreement all exist because agentic systems inherit and act on state their authors never inspected. | the two-clock model, throughout | The fleet is **synthetic** — 24 deterministically-seeded agents across 2 bloodlines. The failure mode is modeled, instrumented, and measured here; it is **not** an observation harvested from a production fleet in the wild. |

**The one claim worth reading twice.** One transactional store spanning **graph + vectors +
time-travel** is the whole competitive thesis. On a Postgres + Pinecone split, a belief's embedding
lives in one system and its provenance in another, so no single transaction can invalidate both and
no `AS OF SYSTEM TIME` can reconstruct the pair as they stood together. Here the RAG corpus shares
`defaultdb` and the same MVCC timeline as the genealogy — which is why a **vector retrieval can be
time-travelled** with the same `SET TRANSACTION AS OF SYSTEM TIME` the belief trace uses. That is
the thing that is hard to fake, and it is why the memory layer is CockroachDB rather than a cache in
front of a vector store.

---

## Sponsor-technology usage

Every capability maps to the exact module that uses it.

| Capability | Where it lives | What it does |
|---|---|---|
| **`AS OF SYSTEM TIME`** (time-travel) | `app/services/time_travel.py:97` · `replay.py:103` · `corpus.py:143` · `lambda/certifier/handler.py:178` | Deposition, closure replay, time-travelled retrieval, and the certifier's independent replay — timestamp validated + inlined, SELECT stays parameterized |
| **C-SPANN distributed vector index** | `migrations/0002:42` · `0005:62` · `app/types_crdb.py:19` · `corpus.py:109` · `agent_brain.py:69` | `CREATE VECTOR INDEX` + custom `VECTOR(1536)` type + cosine `<=>` search over beliefs and the typology corpus |
| **Atomic serializable transaction** | `app/services/invalidation.py:88,131,140` | One txn flips the belief *and* every closure edge with a set-based `UPDATE` — the fleet-wide kill-shot, no per-holder loop |
| **Recursive CTE traversal** | `app/services/lineage.py:19` | Walks `belief_inheritance` back to the origin ancestor |
| **Amazon S3** | `app/services/s3_audit.py:35,50` · `aws_client.py:74` | Real `put_object`/`get_object` for invalidation certificates, real TLS verification |
| **AWS Lambda** | `lambda/certifier/{handler,build,deploy}.py` | Deployed `lineage-certifier` — independent AOST replay + hash re-derivation + S3 write on AWS compute |

CockroachDB Cloud is **v25.4.10**, single-region `aws-eu-central-1`. An MCP Server for the cluster is
configured in `.mcp.json`; see the Technical Implementation note above for its honest scope.

---

## API surface

**14 routes.** One governed write; everything else is a read. Interactive docs at `/docs`.

| Route | What it does | Console |
|---|---|---|
| `GET /health` | liveness | — |
| `GET /agents` | full genealogy — every agent, `parent_id` edges | tree |
| `GET /agents/{id}/beliefs?as_of=` | **the deposition** — one agent's beliefs at a past instant, real `AS OF SYSTEM TIME` | time-travel |
| `GET /beliefs` | belief catalog | inspector |
| `GET /beliefs/{id}/lineage` | **the trace** — recursive CTE back through `belief_inheritance` to the origin ancestor | trace |
| `GET /beliefs/{id}/performance` | measured staleness windows — the real 0.924 → 0.528 curve, each point with its sample size and 95% Wilson interval | time-travel |
| `GET /beliefs/{id}/replay` | content-hashed closure snapshot, AOST-reproducible | — |
| `GET /beliefs/{id}/provenance-audit` | **A1–A4 provenance-integrity verdict** — CLEAN / ANOMALOUS / INCONCLUSIVE | ledger *(top-line verdict only)* |
| `GET /beliefs/{id}/counterfactual-invalidation?at=T` | **"what if we'd killed it at T?"** — N approvals withdrawn, M of them real fraud. `at` is **business time, not the MVCC clock** | no UI yet |
| `POST /beliefs/{id}/invalidate` | ⚠️ **the one governed write** — atomic fleet-wide kill in a single serializable txn + S3 certificate | invalidate |
| `GET /decisions` | fleet-wide decision feed, or one agent's history | feed |
| `GET /aml/transactions/{id}` | one money-flow edge | — |
| `GET /aml/transactions/{id}/interrogate` | **click-to-interrogate** — re-derives the structural witness across all four typologies, surfaces competing structures | no UI yet |
| `GET /demo/consistency/stream` | SSE — real observer samples of the atomic-vs-eventual proof (isolated `demo` db) | consistency |

The two "no UI yet" routes are real, tested, and verified against live cluster data — they simply
have no console surface, and the [honesty ledger](#honesty-ledger) says so rather than leaving them
undiscoverable.

---

## Evaluation results

### The structural detection eval (per-edge, ring-membership ground truth, 95% Wilson CI)

The **hold-out is account-disjoint from every design decision**; the development set is in-sample and
labeled as such. The full honest picture — including SCATTER-GATHER's weaker, disclosed recall:

| Typology | Dev recall | Dev precision | Hold-out recall | Hold-out precision |
|---|---|---|---|---|
| **CYCLE** | 100% (43/43) | 75.4% (43/57) | 100% (38/38) | **100% (38/38) — Wilson CI lower bound 90.8%** |
| **SCATTER-GATHER** | 40.6% (39/96) | 92.9% (39/42) | **50.0% (43/86)** | 89.6% (43/48) |
| GATHER-SCATTER | 83.1% (64/77) | 59.8% (64/107) | 62.7% (69/110) | 77.5% (69/89) *(not flag-capable)* |
| STACK | 7.1% (6/84) | 17.1% (6/35) | 7.1% (7/98) | 22.6% (7/31) *(not flag-capable)* |

**Two limitations that travel with these numbers, neither quotable alone:**
- **CYCLE flags roughly one benign transaction in four** on the dev set (precision 75.4%) — it
  catches every real cycle and pays for it in false positives. The hold-out's 100% precision reflects
  a benign draw that fired 0 false witnesses; the stable claim is *high precision, perfect recall*,
  with the **90.8% Wilson floor** shown next to it, not a trumpeted "100%".
- **SCATTER-GATHER misses over half of real edges** (recall 40.6% dev / 50% hold-out). When it fires
  it is nearly always right, but that is a false-negative profile, not a precision success.

#### The baseline is not a strawman

The honest comparison, printed by `eval_detection.py` on every run. **The structural detector does
not win on F1** — read the bolded cells:

| Detector (CYCLE ∪ SCATTER-GATHER members vs benign) | Dev P / R / F1 | Hold-out P / R / F1 |
|---|---|---|
| **Structural witness** (no format field) | **82.8** / 59.0 / 68.9 | **94.2** / 65.3 / 77.1 |
| Logistic regression (all raw fields, oracle-fit) | 62.4 / 76.3 / 68.6 | 76.9 / **80.6** / **78.7** |
| Best single raw feature — `payment_format == ACH` | 38.7 / **100.0** / 55.8 | 50.4 / **100.0** / 67.0 |

The logistic regression **ties on dev and beats us on the hold-out**, and the one-line ACH rule
**misses nothing at all**. Both do it by exploiting a synthetic-generation artifact: the selected
ring positives are **100% ACH** while benign noise spans six formats, so `format == ACH` alone gives
perfect recall. On the **real** population, "flag all ACH" has **0.75% precision** (4,483 of
600,797) — the leak does not survive contact with reality.

The structural witness reads **no format field**, so the thing it is actually better at — precision,
and a cited path re-derivable from the rows — is the part that transfers. The eval prints the
`payment_format × label` crosstab every run so anyone can check the artifact for themselves.

**Scope:** scored against pattern-typology **membership** (ring detection), not general fraud
detection; measured against the AML evidence layer's *deliberately adversarial* benign set (noise
anchored to the same accounts). Both caveats travel with every quote.

### The RAG-grounding faithfulness eval (secondary to the detection eval)

Scores the only LLM-generated prose in the pipeline — the agent's `rationale` — for faithfulness to
the evidence it actually saw. Judge is **Ollama gemma (free, primary) + NVIDIA nemotron
(cross-check)** — a parameter, **never OpenAI**.

**Three numbers over three different denominators. They are not interchangeable, and the last
column is why:**

| Denominator | What it measures | Result | What it is **not** |
|---|---|---|---|
| **10 labeled tuples**<br/><sub>2 verified-faithful anchors + 8 authored adversarial negatives</sub> | per-tuple accuracy against independently-verified ground truth | **8/10** | **not** a score over the 40-tuple set |
| **8 authored hallucinations** | instrument delta: our GEval rubric vs DeepEval's built-in Faithfulness | GEval **0.287** — catches 7/8<br/>Built-in **0.771** — misses them | **not** an accuracy figure — it compares two *instruments*, and the built-in is kept as the honest un-tuned control |
| **40 tuples**<br/><sub>32 real + 8 authored</sub> | descriptive category means over tuples with **no per-tuple label** | descriptive distribution only | **not** an accuracy figure. **8/10 must never be read as "over 40."** |

**The two misses, disclosed rather than smoothed:** a fabricated-hop negative scored 0.50 and one
genuinely faithful SCATTER-GATHER anchor scored 0.40. DeepEval's built-in metric is contradiction-only
and rates **4 of the 8 additive hallucinations as "fully faithful"** — which is the entire reason the
custom rubric exists.

This is a credible *secondary* result — "a judge that catches the prose-entailment hallucinations the
deterministic guard structurally cannot, with its own instrument limits disclosed" — **not** a number
to rival the detection eval's precision.

---

## Production readiness & security

| Concern | Implementation | Where |
|---|---|---|
| **Resilience** | Classifies transient CRDB errors (SQLSTATE `40001` serialization, `08xxx`/`57Pxx` connection/shutdown, the "indexes being dropped" TRUNCATE string); bounded exponential backoff, `TransientRetryExhausted` → 503. Applied at exactly two mutating surfaces. | `app/resilience.py`; `routers/beliefs.py:98`, `routers/demo.py:79` |
| **Rate limiting** | Hand-rolled per-(IP, route-template) fixed window; UUID path segments collapsed so varying the id can't dodge the limit; `asyncio.Lock` with no await in the critical section; 60/min, `/health` exempt, 429 + `Retry-After`. | `app/ratelimit.py`, `main.py:34` |
| **Access control** | The one governed write requires a well-formed non-nil `actor_id` (nil UUID → 422); deliberately not checked against the fleet `agents` table (the human supervisor is not a fleet agent). | `app/schemas.py:97` |
| **Blast-radius isolation** | The destructive consistency demo runs its whole lifecycle in a dedicated `demo` database, injected via optional engine params, so it physically cannot name a `defaultdb` table. | `app/demo_db.py`, `routers/demo.py` |
| **Transport / secrets** | Explicit CORS allow-list (Vite dev origins only, not `*`); secrets loaded via pydantic-settings from a gitignored `.env`, no hardcoded credentials; real TLS `verify-full` to CRDB and outbound 443. | `main.py:55`, `app/config.py`, `app/services/aws_client.py` |
| **CI** | Two workflows: full live-cluster backend suite on every non-frontend push (`cancel-in-progress`, 15-min timeout) + an offline frontend typecheck/lint/build gate. | `.github/workflows/{ci,frontend-ci}.yml` |

---

## Honesty ledger

Every claim this system makes, labeled by provenance. **This table is also a live console view** —
the [Ledger](frontend/src/components/HonestyLedger.tsx) tab reads the LIVE rows straight from the
cluster, so the document and the running system cannot quietly disagree.

| Item | Label | Note |
|---|---|---|
| Agent genealogy | **synthetic** | 2 bloodlines, 8 inheritance edges. Deterministically seeded — the inheritance edges are real rows, the population is fabricated. *(The console reads the agent/belief counts live.)* |
| AML transactions (648 accounts / 1,500 edges / 20 instances / 300 members) | **real + sampled** | Real IBM HI-Small AML data; benign negatives are `is_laundering=0` rows *anchored to the same accounts* as the fraud (deliberately adversarial), capped 4:1 |
| `decisions` / `belief_performance` | **measured, reproducible** | Whether the cluster is currently populated depends on demo activity — the destructive invalidation demo consumes it. A deterministic `python -m seed.backfill_decisions` repopulates 4,000 rows + 8 windows (curve conf 0.924 → 0.528, byte-identical every run). *(The console reads populated-or-empty live, so this row can never go stale.)* |
| Belief embedding vector | **crimson: placeholder · azure: real** | Stated as it actually is on the live cluster, not as an intent. The **crimson** belief's stored vector **is still the deterministic placeholder** (measured cosine distance 0.000000000 from `seed.placeholder_embedding(1536)`); the **azure** laundering belief carries a real `text-embedding-3-small` vector. This is not a to-do item that was forgotten: `seed.seed()` **re-plants the placeholder on every reseed**, so anything `scripts/embed_beliefs.py` writes is discarded by the next reseed — "just run embed_beliefs" is therefore not a fix, and closing it properly needs its own decision. Embeddings drive vector *search*, never the staleness signal, so a placeholder is honest — but the previous label ("placeholder → real") described a transition that had not happened. |
| Detection eval — dev-set numbers | **in-sample** | Selection decisions (`FLAG_CAPABLE`, SG tightening) were made on this set; the hold-out is the never-tuned figure |
| Faithfulness eval — GEval rubric | **partly in-sample** | Rubric iterated on 5 of the calibration examples; generalizes 5/5 on fresh authored negatives, but "never tuned" is false for the calibration subset |
| Faithfulness eval — judge | **open-model** | Ollama gemma / NVIDIA nemotron — never OpenAI; unreliable on dense structural-reasoning prose (disclosed) |
| MCP Server / ccloud CLI | **configured, not exercised** | MCP Server declared in `.mcp.json`; verification done via direct SQL probes; ccloud CLI not used |
| Regulatory corpus (FATF/FFIEC/FinCEN) | **not built** | Gated on a `data/raw/` drop (sources block automated fetch); `typology_corpus` holds the 4 IBM typology definitions only |
| Certificate authorship | **integrity, not authorship** | `content_hash` is an unkeyed sha256 — it proves integrity + (within the GC window) AOST-reproducibility, not authorship; asymmetric signing is documented, not built |
| Staleness curve — uncertainty | **measured, with its interval** | The certificate's `0.924 → 0.528` is no longer a bare point estimate: every window carries the sample size behind it and a 95% Wilson interval (present day is `0.528`, CI `[0.466, 0.589]`). `n` is **re-aggregated from `decisions`**, not persisted — `belief_performance` still has no denominator column, and the five-table schema is unchanged. If a persisted confidence stops reproducing from the decisions it summarizes, the intervals are **withheld** (`sample_agreement: disagreed`) rather than pairing a fresh denominator with a stale estimate. The document asserts a measured decay only when a Fisher exact test supports it — there is **no minimum-sample gate**, so a one-decision window disqualifies itself on the evidence |
| Staleness uncertainty — **not** cross-checked | **shared computation, not an independent check** | The certifier Lambda computes the intervals with the *same shared function*, but it does **not** re-derive and compare them the way it does the closure hash — and that is deliberate. A confidence interval is arithmetic over `(k, n)`, not a claim about the world, so there is no independent oracle to check it against; both halves read `belief_performance` at current committed state, so neither is a check on the other. A `staleness_verification: agreed` block would fabricate the *appearance* of the closure hash's guarantee while proving nothing |
| Provenance-integrity audit | **verification, not a patch** | The two legitimate `belief_inheritance` writers preserve the A1–A4 invariants by construction, so **no live vulnerability exists**; `GET /beliefs/{id}/provenance-audit` is verification + out-of-band tamper detection. OWASP `ASI06` primary-verified; MITRE ATLAS `AML.T0080` **secondary-sourced**, not confirmed on the authoritative page |
| Counterfactual invalidation query | **measured, exact — verdict-aware** | `GET /beliefs/{id}/counterfactual-invalidation?at=T` returns **exact** counts, not estimates. The counts are split by the verdict that actually happened: **N** = belief-driven **approvals** withdrawn, **M** = their real `is_fraud` subset (the harm), `withdrawn_blocks`, and `frauds_caught_by_block` (what invalidating would **forfeit**). No fabricated "fraud we'd have caught" — no faithful per-row fallback verdict exists, so none is invented. **Correction:** this row previously asserted *"the belief only ever approves"*. That was true of the crimson card belief and is **false of the fleet** — the azure laundering belief **blocks** (57 of its 1,500 decisions). Under the old un-split aggregate the endpoint counted the 43 laundering rows it correctly **blocked** as *auto-approved frauds*, crediting its catches as its harms. Fixed, with a regression test that fails against the old aggregate. A belief with no measured windows returns `windows: null`, never a grid of zeros. |
| Explanation-faithfulness guard | **probabilistic guard** | Scores the agent's `rationale` against the exact evidence it saw; `SUPPORTED` means "passed the check", **not "proven faithful"** (documented false-negatives — the faithfulness eval's fabricated-hop 0.50, faithful SG anchor 0.40). Cites OWASP `LLM09:2025 Misinformation`; **explicitly not** a retrieval/memory-poisoning defense (`LLM08`/`ASI06`) — it checks prose against retrieved rows, not whether those rows are poisoned |
| Interrogate / provenance-audit / counterfactual endpoints | **built, no UI yet** | `GET /aml/transactions/{id}/interrogate`, `/beliefs/{id}/provenance-audit`, `/beliefs/{id}/counterfactual-invalidation` are built, tested, and verified against real cluster data but have no console surface yet — each is a separate plan-gated frontend session; listed here rather than left undiscoverable |

---

## Getting started

**Prerequisites:** Python 3.12, Node 24 / npm 11, a CockroachDB (Cloud Serverless free tier works),
and an OpenAI key. AWS (S3 + Lambda) and an NVIDIA/Ollama judge are optional (certificates and the
grounding eval respectively).

### Backend

```bash
python -m venv .venv
.venv/Scripts/activate           # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

# Configure secrets (never commit .env)
#   DATABASE_URL=cockroachdb+psycopg://USER:PASS@HOST:26257/defaultdb?sslmode=verify-full
#   OPENAI_API_KEY=sk-...           (required for app import; only the agent/embed paths call it)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION / S3_BUCKET   (optional — certificates)
#   NVIDIA_API_KEY                  (optional — the grounding-faithfulness eval only)

alembic upgrade head               # apply migrations 0001–0005 to the cluster
python -m seed.seed                # seed the genealogy (24 agents, 1 belief, 8 inheritance edges)
python -m seed.backfill_decisions  # optional: 4,000 decisions + 8 performance windows (~4 min)

uvicorn app.main:app --reload      # serves on http://localhost:8000  (sets the Windows selector loop itself)
```

`GET http://localhost:8000/health` → `{"status": "ok"}`. Interactive API docs at `/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173  (VITE_API_BASE defaults to :8000)
```

The backend CORS allow-list is exactly `localhost:5173` / `127.0.0.1:5173`, so the dev server must
run on 5173.

### Tests & evals

```bash
pytest                                          # 118 tests against the real cluster (~2m30s)
PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/eval_detection.py    # structural detection eval
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| API | FastAPI (async) + Uvicorn |
| Database | CockroachDB Cloud v25.4 (Postgres-wire), distributed vector index, `AS OF SYSTEM TIME` |
| DB access | SQLAlchemy 2.x async + psycopg 3 + `sqlalchemy-cockroachdb` dialect |
| Migrations | Alembic (hand-written DDL) |
| Agent / embeddings | OpenAI `gpt-4o-mini` + `text-embedding-3-small` |
| AWS | S3 (certificates) + Lambda (certifier) |
| Eval judge | NVIDIA NIM (nemotron) / Ollama (gemma) via DeepEval — never OpenAI |
| Frontend | React 19 + Vite + TypeScript, framer-motion, react-three-fiber, oxlint |
| Tests | pytest (118, live-cluster) |

---

## Project structure

```
CockroachDB/
├── app/
│   ├── main.py              FastAPI app, rate-limit + CORS middleware, router wiring
│   ├── config.py            pydantic-settings secret loading
│   ├── models.py            the five-table moat (agents/beliefs/inheritance/decisions/performance)
│   ├── aml_models.py        AML evidence layer  — separate AmlBase metadata
│   ├── corpus_models.py     typology RAG corpus — separate CorpusBase metadata
│   ├── types_crdb.py        custom VECTOR(n) SQLAlchemy type
│   ├── resilience.py        transient-CRDB retry/backoff
│   ├── ratelimit.py         per-IP/route rate limiter
│   ├── demo_db.py           isolated `demo` database engine
│   ├── routers/             agents · beliefs · decisions · demo · aml   (14 routes)
│   └── services/            time_travel · replay · lineage · invalidation · certificate
│                            consistency · corpus · provenance_audit · counterfactual
│                            aml_graph · aml_agent · aml_interrogate · verdict_guard
│                            faithfulness · faithfulness_guard · …
├── migrations/versions/     0001 moat · 0002 vector+perf indexes · 0003 invalidation
│                            0004 AML layer · 0005 typology corpus
├── seed/                    seed.py (genealogy) · backfill_decisions.py (deterministic decisions)
├── scripts/                 probes · ingest · verify · demos · evals
├── lambda/certifier/        handler · build · deploy — the independent AOST-replay certifier
├── eval/grounding/          32-tuple golden set + 8 authored adversarial negatives
├── tests/                   25 files, 118 tests (all live-cluster)
├── frontend/                React 19 + Vite console — three views:
│                            console (tree · feed · inspector) · consistency demo (2D + 3D)
│                            · honesty ledger
└── data/raw/                IBM HI-Small AML dataset (Trans.csv + Patterns.txt)
```

---

## Roadmap status

**Shipped — every row verified against the live cluster, CI green.** *(The bracketed letters are
internal engineering-log labels; they index into [NOTES.md](NOTES.md) and mean nothing on their own.)*

| Capability | What it is |
|---|---|
| **Cluster isolation** *(0)* | Dedicated `demo` database so the destructive stream can't touch console state |
| **AML evidence-layer ingestion** *(1)* | Four additive `aml_*` tables — a bounded, verified subgraph of IBM HI-Small |
| **Deterministic replay** *(2)* | Reversible, content-hashed closure snapshot over the lineage timeline |
| **CockroachDB-native RAG** *(3)* | Typology corpus, real embeddings, AOST-over-vector-search |
| **The witness-construction brake** *(4)* | Grounded AML agent where a FLAG is unreachable without a structural witness |
| **Click-to-interrogate** *(5)* | Re-derive a transaction's witness on demand, surfacing competing structures |
| **The cross-machine certifier** *(6)* | Content-addressed pre-kill state + an independent Lambda that re-derives the same hash |
| **The structural detection eval** *(7)* | Dev + never-tuned hold-out precision/recall, with the baselines that beat us printed |
| **The RAG-grounding faithfulness eval** *(8)* | Open-model judge over the agent's prose, two denominators kept apart |
| **The honesty ledger** *(9)* | The provenance table above, as a live console view reading the cluster in real time |
| **The provenance-integrity audit** *(A)* | A1–A4 invariant verifier over a belief's inheritance closure — CLEAN / ANOMALOUS / INCONCLUSIVE |
| **The counterfactual invalidation query** *(B)* | "Had we killed this belief at T, what changes?" — exact N approvals withdrawn, M of them real fraud |
| **The explanation-faithfulness guard** *(E)* | Live, fail-closed check that withholds any rationale asserting more than the rows support |
| **The hero demo storyboard** *(F)* | Two-act walkthrough, every beat tagged LIVE / FRESH / HISTORICAL — see **[DEMO.md](DEMO.md)** |
| **Measured uncertainty on the staleness curve** | The numbers justifying the one irreversible write now carry their sample size and 95% Wilson interval — certificate schema 1.2, computed by the endpoint and the certifier Lambda through one shared function |

Also complete: Phases 1–3 (the belief-inheritance spine, agents, and the money-shots), Phase 4
hardening, and the full React console (Frontend Phases 1–6).

### Investigated and cut

Recorded rather than silently dropped — a roadmap line that the data refuses to support is a finding,
not a gap.

| Capability | Verdict |
|---|---|
| **Temporal drift / belief-decay detection** *(C)* | **CUT — the data does not support it.** The item was conditional (*"build only if the data supports a real signal — verify first, never decorative"*). It was verified first. **As detection it is a duplicate:** the decay is already computed from real outcomes, rendered as the full 8-window curve, embedded in the certificate, and made actionable by the counterfactual — and an automated "this belief is rotting" verdict over a population of **one** belief would return a constant, with two of its three states structurally unreachable. **As drift *characterization* it is refuted:** the curve's one non-monotonic feature, the gen-6 dip, is **not distinguishable from noise** (p = 0.12 at n=250/window), and the modeled campaign's true effect (+0.0227) is **smaller than the per-window noise SD (0.03)** — so it does not shrink with more data. A detector that named it correctly could only do so by reading the generator's own constants. Full numbers: [NOTES.md](NOTES.md) → *Roadmap Item C*. |
| **Confidence propagation through the inheritance chain** *(D)* | **CUT — nothing propagates.** Same conditional gate as C, and it fails for a **different reason, which is the finding.** The belief is **one immutable row**: `belief_inheritance` has no confidence or weight column and `belief_performance` has no `agent_id`, so the only quantity that varies per hop is a **timestamp**. Looking up its window is a *join*, not a propagation — no hop transforms anything, so uncertainty lives on the windows (shared by every holder), never on the edges. **D passes C's generator-lookup test and is still meaningless:** *computable, generator-free, and meaningless.* Proven, not argued — in a world with **zero decay** (every window pinned at 0.924) the compounded number still reports the 7-hop holder as **15% more degraded** than the 5-hop one: it measures **path length, not health**. And the two agents who share window 5 have **identical true reliability by construction**, yet differ by 0.137 at **p = 0.046** — the shipped data realizes the one-in-twenty false positive that any per-holder confidence metric would inherit. The two living holders are **statistically indistinguishable** (0.528 vs 0.459, p = 0.30). Full numbers: [NOTES.md](NOTES.md) → *Roadmap Item D*. |

**Next:** the regulatory corpus (FATF/FFIEC/FinCEN, gated on a `data/raw/` drop with structure-aware
chunking), the `decisions.aml_transaction_id` grounding seam that would join the two graphs into one
causal chain, an AML console surface, the recorded demo video, and the Time-travel sparkline rendering
the confidence band the API now serves.

---

## Where to go next

| Document | What's in it |
|---|---|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | The deep technical dive — seven mermaid diagrams over real code: schema separation, the atomic invalidation txn, AOST + deterministic replay, the certificate/certifier hash agreement, the witness brake, the faithfulness guard, the A1–A4 provenance audit |
| **[DEMO.md](DEMO.md)** | The hero demo storyboard — two acts, ~90 seconds, every beat tagged with how it was verified and a build-time verification log |
| **[NOTES.md](NOTES.md)** | The engineering log — every decision, every dead end, every honesty call, in the order they happened |
| **[FRONTEND.md](FRONTEND.md)** | The console's design constitution — tokens, the four supervisor interactions, phase discipline |

---

## License

[MIT](LICENSE) © 2026 Mohamed Aziz Ayari

<div align="center">
<sub>Built for the CockroachDB × AWS "Agentic Memory" hackathon.</sub>
</div>
