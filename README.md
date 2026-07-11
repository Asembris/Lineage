<div align="center">

<img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&size=22&duration=3600&pause=900&color=E8A33D&center=true&vCenter=true&width=860&lines=Lineage%3A+agent-genealogy+belief-inheritance+forensics;A+living+agent+acts+on+beliefs+inherited+from+ancestors+it+never+met;100%25+hold-out+ring+recall+-+90.8%25+Wilson+precision+floor" alt="Lineage" />

**Belief-inheritance forensics for AI fraud-detection fleets, on CockroachDB.**

[![CI](https://github.com/Asembris/Lineage/actions/workflows/ci.yml/badge.svg)](https://github.com/Asembris/Lineage/actions/workflows/ci.yml)
&nbsp;![tests](https://img.shields.io/badge/tests-89%20passing-brightgreen)
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

```
  gen0            gen1            gen2     ...      gen7  (alive)
 ┌──────┐        ┌──────┐        ┌──────┐         ┌──────┐
 │crimson│──────▶│crimson│──────▶│crimson│──····──▶│crimson│   acts NOW on
 │  -0   │ forms │  -1  │inherit│  -2  │         │  -7  │   an inherited rule
 └──────┘  rule  └──────┘        └──────┘         └──────┘
     │                                                 ▲
     │   belief valid THEN  ───────────────────────▶   │  world drifted; belief ROTTED
     └──────────────────────  same immutable row  ─────┘
```

Two clocks run, and Lineage keeps them separate:

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

### 3 · Structural detection wins on precision, and says so honestly

On a **genuinely fresh, account-disjoint hold-out no design decision ever saw**, the frozen
structural detector reaches **CYCLE recall 100% (38/38), precision 100% (38/38) — Wilson 95% CI
lower bound 90.8%**, and SCATTER-GATHER precision 89.6%. An oracle-fit logistic-regression baseline
only ties on F1 by riding a synthetic ACH generation artifact (positives are 100% ACH; the real ACH
base rate is 0.75%). The witness uses **no format field at all**, so it doesn't ride the leak.
→ `scripts/eval_detection.py`

---

## Judging Criteria Alignment

> Answered against real, file-linked facts. Where the fit is weaker, it says so — an honest "we used
> X, not Y" is stronger than an overclaim a judge can disprove in one question.

### Agentic Memory Design — *does CockroachDB play a meaningful, production-grade role as the agent's memory layer?*

Yes, and for more than toy queries. One cluster holds three deliberately-separated schemas on the
**same MVCC timeline**: the five-table genealogy/belief moat, a `1,500`-edge AML evidence layer, and
a `1536`-dim vector RAG corpus. The agent's memory is **queried transactionally, embedded, and
time-travelled** — inheritance is a real recursive CTE over `belief_inheritance`; staleness is
aggregated from `belief_performance` windows; retrieval is CockroachDB-native cosine vector search;
and any past state is reconstructable with real `AS OF SYSTEM TIME`. That single transactional store
spanning graph + vectors + time-travel is the whole thesis against a Postgres + Pinecone split.

### Technical Implementation — *is the integration with CockroachDB tools quality software engineering? Does the agent use the tools correctly and safely?*

The primitives are wired correctly and safely (see the [sponsor-tech table](#sponsor-technology-usage)):
AOST timestamps are validated and inlined as literals with the SELECT staying parameterized (they
cannot be bind params in CRDB); invalidation is a single serializable transaction with a set-based
closure update and a `FOR UPDATE` idempotency guard, never a per-holder loop; vector DDL uses the
`CREATE VECTOR INDEX` (C-SPANN) escape hatch Alembic can't emit. **Honest limitation:** a
CockroachDB Cloud **MCP Server is configured** (`.mcp.json`) and available in-session, but the
cluster-capability verification in this project was done through **direct SQL probe scripts**
(`scripts/probe_crdb.py`), not MCP tool calls — and the **ccloud CLI was not used**. The vector
index is a *proven mechanism* (column + `<=>` operator + C-SPANN DDL + AOST-over-vector-search all
wired end to end), not a demonstration of indexing at scale — at 4 corpus rows the planner correctly
brute-forces a full scan, and `verify_corpus.py` asserts that plain every run.

### Real-World Impact — *how big an impact could this have on real workflows?*

Belief inheritance across agent generations is a real emerging failure mode for long-lived agentic
systems: a rule formed under one regime silently outlives its validity and no living agent remembers
forming it. Lineage turns that into an auditable, correctable artifact — trace a bad decision to its
origin belief, prove valid-then/rotten-now from data, and correct the whole fleet atomically with an
S3 certificate an independent auditor can re-verify. The detection layer is scored against **IBM's
real 5M-transaction AML dataset**, not a toy.

### Production Readiness — *secure, observable, scalable? Resilience, access control, failure handling?*

See [Production readiness & security](#production-readiness--security). Real, not aspirational:
bounded exponential-backoff retry around transient CRDB errors (SQLSTATE `40001` + connection
families) at the two mutating surfaces; a concurrency-safe per-IP/route rate limiter; nil-actor
rejection on the one governed write; a **physically isolated `demo` database** so the destructive
consistency demo cannot wipe console state; explicit CORS allow-list; gitignored secrets; and CI
that runs the full suite against the real cluster on every push.

### Creativity & Originality — *a genuinely new idea? Insight into what makes agentic systems different?*

The insight is that an agent's *inherited memory* is a distinct object from its code or its current
state — it has a genealogy, a formation time, a measured decay, and a blast radius when wrong. Most
of the surface (the two-clock staleness model, the witness-required brake, the cross-machine hash
agreement) exists because agentic systems inherit and act on state their authors never inspected.

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

## Evaluation results

### Item 7 — structural detection (per-edge, ring-membership ground truth, 95% Wilson CI)

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

**The baseline is not a strawman.** An oracle-advantaged logistic regression on raw fields matches
(dev F1 68.6 vs 68.9) or beats (hold-out 78.7 vs 77.1) the frozen structural detector — but only by
exploiting a synthetic-generation artifact: the selected ring positives are 100% ACH while benign
noise spans six formats, so `format == ACH` alone gives 100% recall. On the real population "flag all
ACH" has **0.75% precision**. The structural witness uses no format field, so its advantage —
precision and an auditable cited path — is leak-independent. The eval prints the `payment_format ×
label` crosstab every run so anyone can check it.

**Scope:** scored against pattern-typology **membership** (ring detection), not general fraud
detection; measured against Item 1's *deliberately adversarial* benign set (noise anchored to the
same accounts). Both caveats travel with every quote.

### Item 8 — RAG-grounding faithfulness (secondary to Item 7)

Scores the only LLM-generated prose in the pipeline — the agent's `rationale` — for faithfulness to
the evidence it actually saw. Judge is **Ollama gemma (free, primary) + NVIDIA nemotron
(cross-check)** — a parameter, **never OpenAI**. **Two denominators, kept apart:**

- **Headline accuracy: 8/10** on the 10 tuples with independently-verified per-tuple ground truth
  (2 manually-confirmed faithful FLAG anchors + 8 hand-authored adversarial negatives). Misses:
  fabricated-hop (0.50) and one faithful SCATTER-GATHER anchor (0.40) — both disclosed, not smoothed.
- **Metric delta on the 8 authored hallucinations:** the custom GEval rubric scores them **0.287**
  (catches 7/8 below threshold); DeepEval's built-in Faithfulness scores **0.771** (misses them —
  it's contradiction-only and rates 4/8 additive hallucinations as "fully faithful"). The built-in
  metric is kept alongside as the honest un-tuned control.
- The **"full 40" category means** (32 real + 8 authored) are descriptive distribution over tuples
  with **no per-tuple label** — not an accuracy figure. 8/10 must not be read as over 40.

Item 8 is a credible *secondary* result — "a judge that catches the prose-entailment hallucinations
the deterministic guard structurally cannot, with its own instrument limits disclosed" — not a
number to rival Item 7's detection precision.

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

| Item | Label | Note |
|---|---|---|
| Agent genealogy (24 agents, 2 bloodlines, 1 belief, 8 inheritance edges) | **synthetic** | Deterministically seeded; the inheritance edges are real rows, the population is fabricated |
| AML transactions (648 accounts / 1,500 edges / 20 instances / 300 members) | **real + sampled** | Real IBM HI-Small AML data; benign negatives are `is_laundering=0` rows *anchored to the same accounts* as the fraud (deliberately adversarial), capped 4:1 |
| `decisions` / `belief_performance` | **measured, reproducible** | Currently empty on the live cluster; a deterministic `python -m seed.backfill_decisions` repopulates 4,000 rows + 8 windows (curve conf 0.924 → 0.528, byte-identical every run) |
| Belief embedding vector | **placeholder → real** | Phase-1 seed uses a deterministic placeholder; real `text-embedding-3-small` vectors via `scripts/embed_beliefs.py` |
| Item 7 dev-set numbers | **in-sample** | Selection decisions (`FLAG_CAPABLE`, SG tightening) were made on this set; the hold-out is the never-tuned figure |
| Item 8 GEval rubric | **partly in-sample** | Rubric iterated on 5 of the calibration examples; generalizes 5/5 on fresh authored negatives, but "never tuned" is false for the calibration subset |
| Item 8 judge | **open-model** | Ollama gemma / NVIDIA nemotron — never OpenAI; unreliable on dense structural-reasoning prose (disclosed) |
| MCP Server / ccloud CLI | **configured, not exercised** | MCP Server declared in `.mcp.json`; verification done via direct SQL probes; ccloud CLI not used |
| Regulatory corpus (FATF/FFIEC/FinCEN) | **not built** | Gated on a `data/raw/` drop (sources block automated fetch); `typology_corpus` holds the 4 IBM typology definitions only |
| Certificate authorship | **integrity, not authorship** | `content_hash` is an unkeyed sha256 — it proves integrity + (within the GC window) AOST-reproducibility, not authorship; asymmetric signing is documented, not built |
| Provenance audit (Item A) | **verification, not a patch** | The two legitimate `belief_inheritance` writers preserve the A1–A4 invariants by construction, so **no live vulnerability exists**; `GET /beliefs/{id}/provenance-audit` is verification + out-of-band tamper detection. OWASP `ASI06` primary-verified; MITRE ATLAS `AML.T0080` **secondary-sourced**, not confirmed on the authoritative page |
| Counterfactual invalidation (Item B) | **measured, exact** | `GET /beliefs/{id}/counterfactual-invalidation?at=T` returns **exact** counts (each generation window is exactly 250 rows, not estimated): N = belief-driven approvals withdrawn, M = their real `is_fraud` subset — reported as approvals-withdrawn, never a fabricated "fraud we'd have caught" (the belief only ever approves; no faithful per-row fallback verdict exists, so none is invented) |
| Explanation-faithfulness guard (Item E) | **probabilistic guard** | Scores the agent's `rationale` against the exact evidence it saw; `SUPPORTED` means "passed the check", **not "proven faithful"** (documented false-negatives — Item 8's fabricated-hop 0.50, faithful SG anchor 0.40). Cites OWASP `LLM09:2025 Misinformation`; **explicitly not** a retrieval/memory-poisoning defense (`LLM08`/`ASI06`) — it checks prose against retrieved rows, not whether those rows are poisoned |
| Interrogate / provenance-audit / counterfactual endpoints | **built, no UI yet** | `GET /aml/transactions/{id}/interrogate` (Item 5), `/beliefs/{id}/provenance-audit` (Item A), `/beliefs/{id}/counterfactual-invalidation` (Item B) are built, tested, and verified against real cluster data but have no console surface yet — each is a separate plan-gated frontend session; listed here rather than left undiscoverable |

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
#   NVIDIA_API_KEY                  (optional — Item 8 grounding eval only)

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
pytest                                          # 89 tests against the real cluster (~2m39s)
PYTHONIOENCODING=utf-8 PYTHONPATH=. python scripts/eval_detection.py    # Item 7 detection eval
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
| Tests | pytest (89, live-cluster) |

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
│   ├── routers/             agents · beliefs · decisions · demo · aml
│   └── services/            time_travel · replay · lineage · invalidation · certificate
│                            consistency · corpus · aml_graph · aml_agent · verdict_guard · …
├── migrations/versions/     0001 moat · 0002 vector+perf indexes · 0003 invalidation
│                            0004 AML layer · 0005 typology corpus
├── seed/                    seed.py (genealogy) · backfill_decisions.py (deterministic decisions)
├── scripts/                 probes · ingest · verify · demos · evals
├── lambda/certifier/        handler · build · deploy — the independent AOST-replay certifier
├── eval/grounding/          32-tuple golden set + 8 authored adversarial negatives
├── tests/                   21 files, 89 tests
├── frontend/                React 19 + Vite console (genealogy tree · decision feed · inspector)
└── data/raw/                IBM HI-Small AML dataset (Trans.csv + Patterns.txt)
```

---

## Roadmap status

**Shipped (Roadmap Items 0–8, verified, CI green):**

| # | Item |
|---|---|
| 0 | Cluster isolation — dedicated `demo` database for the destructive stream |
| 1 | AML evidence-layer ingestion (four additive `aml_*` tables, bounded verified subgraph) |
| 2 | Reversible-deterministic replay over the lineage timeline (content-hashed closure snapshot) |
| 3 | CockroachDB-native RAG — typology corpus with real embeddings + AOST-over-vector-search |
| 4 | Grounded AML agent with the STRICT-BRAKE (FLAG requires a witness) |
| 5 | Click-to-interrogate a transaction, with competing-witness conflict surfacing |
| 6 | Content-addressed pre-kill state + the independent cross-machine certifier |
| 7 | Forensic detection eval — dev + never-tuned hold-out precision/recall |
| 8 | RAG-grounding faithfulness eval (open-model judge) |

Also complete: Phases 1–3 (the belief-inheritance spine, agents, and the money-shots), Phase 4
hardening, and the full React frontend (Frontend Phases 1–6).

**Next:** the regulatory corpus (FATF/FFIEC/FinCEN, gated on a `data/raw/` drop with structure-aware
chunking), the `decisions.aml_transaction_id` grounding seam, and a hero attack demo.

---

## License

[MIT](LICENSE) © 2026 Mohamed Aziz Ayari

<div align="center">
<sub>Built for the CockroachDB × AWS "Agentic Memory" hackathon.</sub>
</div>
