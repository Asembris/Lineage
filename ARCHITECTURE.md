# Architecture

A technical dive into how Lineage uses CockroachDB as an agent-memory layer. Every mechanism here is
wired in code; file paths are given so each claim is checkable. For the judge-facing overview and the
evaluation numbers, see [README.md](README.md).

## Contents

1. [Three deliberately-separated schemas](#1--three-deliberately-separated-schemas)
2. [The atomic invalidation transaction](#2--the-atomic-invalidation-transaction)
3. [AS OF SYSTEM TIME and deterministic replay](#3--as-of-system-time-and-deterministic-replay)
   — including [when *not* to use it](#when-not-to-use-as-of-system-time--the-counterfactual-and-the-two-clocks)
4. [The certificate and the independent certifier](#4--the-certificate-and-the-independent-certifier)
5. [The witness-construction brake](#5--the-witness-construction-brake)
   — and [5.1 the explanation-faithfulness guard](#51--the-same-discipline-applied-to-prose-the-explanation-faithfulness-guard)
6. [Verifying the provenance graph itself (A1–A4)](#6--verifying-the-provenance-graph-itself-a1a4)

Seven diagrams, each over real code. The two newest — the faithfulness guard (§5.1) and the
provenance audit (§6) — are the system's two answers to *"who checks the checker?"*: one guards the
model's **prose** against the evidence, the other guards the **evidence** against tampering.

---

## 1 · Three deliberately-separated schemas

One CockroachDB cluster holds three schemas on **one MVCC timeline**, each on its own SQLAlchemy
`DeclarativeBase` metadata. The separation is **structural, not stylistic** — it makes the
data-model boundaries impossible to cross by accident.

```mermaid
graph TB
    subgraph moat["FIVE-TABLE MOAT — Base metadata (app/models.py)"]
        A[agents<br/>genealogy graph] --> B[beliefs<br/>rule_text + VECTOR embedding]
        A --> BI[belief_inheritance<br/>provenance + closure state]
        B --> BI
        A --> D[decisions<br/>verdict + driving_belief]
        B --> D
        B --> BP[belief_performance<br/>measured staleness windows]
    end
    subgraph aml["AML EVIDENCE LAYER — AmlBase metadata (app/aml_models.py)"]
        ACC[aml_accounts] --> ATX[aml_transactions<br/>money-flow edges + is_laundering]
        API[aml_pattern_instances] --> APM[aml_pattern_members<br/>answer key — tests only]
        ATX --> APM
    end
    subgraph corpus["RAG CORPUS — CorpusBase metadata (app/corpus_models.py)"]
        TC[typology_corpus<br/>VECTOR 1536 + C-SPANN index]
    end

    D -. "the ONE seam (deferred):<br/>nullable aml_transaction_id FK" .-> ATX
    TC -. "validated-string join<br/>typology == aml_pattern_instances.typology" .-> API

    style moat fill:#1e2a44,stroke:#6b8cae,color:#e8eef7
    style aml fill:#2a2440,stroke:#8c7bae,color:#e8eef7
    style corpus fill:#2a1e2e,stroke:#ae7b9c,color:#e8eef7
```

**Why three metadatas and not one.** The cluster-isolation work provisions a throwaway `demo`
database with `Base.metadata.create_all`. Because `aml_*` and `typology_corpus` live on *different*
metadata, that
call **physically cannot** create empty evidence/corpus tables in the demo database — the isolation
is enforced by Python object identity, not discipline. It also keeps Alembic's `target_metadata`
(the moat) clean and leaves the five-table moat exactly five. Verified structurally: querying
`information_schema`, **no foreign key crosses the moat / `aml_*` / corpus boundary** in either
direction (`scripts/verify_aml_ingest.py` check #7).

**Why the corpus still shares the cluster.** Unlike a Postgres + Pinecone split, `typology_corpus`
lives on `defaultdb` and shares the AOST timeline — so a vector retrieval can be *time-travelled*
with the same `SET TRANSACTION AS OF SYSTEM TIME` the genealogy uses. One transactional store
spanning graph + vectors + time-travel is the competitive thesis.

**The two seams, both additive and non-restructuring:**
- The RAG corpus joins to real ingested pattern instances by a **validated string** (`typology`),
  gated at load time and re-checked at verify — not a cross-metadata FK (which would break the clean
  separation). Every returned `typology` is guaranteed present in `aml_pattern_instances`.
- A `decisions` row may *eventually* cite a real `aml_transactions` row via a nullable
  `aml_transaction_id` FK (the "one real seam", deferred). No FK ever runs **from** the evidence
  layer **into** the moat — the agent layer *reads* the evidence, nothing inherits a transaction.

> `aml_pattern_members` is the **answer key**, not evidence. `aml_graph.py` recomputes structure
> from the *unlabeled* edge set and selects no label column; membership and `is_laundering` are read
> only by tests (as a scoring oracle) and by demos (to print an oracle column after the fact).

---

## 2 · The atomic invalidation transaction

Invalidating a belief closes its whole inherited closure — every holder — in **one serializable
CockroachDB transaction**. This is the kill-shot: a loop of per-holder updates would let an observer
witness a torn state where some agents act on a dead belief while others don't.

```mermaid
sequenceDiagram
    participant R as router (beliefs.py)
    participant Snap as read connection
    participant Txn as write txn (invalidation.py)
    participant S3 as S3 (s3_audit.py)

    R->>Snap: capture snapshot_hlc<br/>(cluster_logical_timestamp) BEFORE write opens
    Note over Snap: HLC monotonic ⇒ strictly earlier<br/>than the commit ts (non-circular)
    R->>Txn: run_with_retry(invalidate_belief) — 40001-safe

    activate Txn
    Txn->>Txn: SELECT belief FOR UPDATE<br/>(idempotency guard → AlreadyInvalidated)
    Txn->>Txn: UPDATE beliefs SET status='invalidated' WHERE id=X AND status='active'
    Txn->>Txn: UPDATE belief_inheritance SET invalidated_at=… WHERE belief_id=X<br/>(EVERY closure edge, set-based, NO loop)
    Txn->>Txn: INSERT audit_log (actor, counts, commit_hlc)
    Txn-->>R: ONE commit
    deactivate Txn

    R->>R: replay closure AS OF snapshot_hlc → closure_content_hash
    R->>S3: PUT certificate (asyncio.to_thread)
    Note over R,S3: post-commit, best-effort —<br/>a failed PUT stamps cert_status='failed',<br/>NEVER rolls back the durable invalidation
```

Key properties, each load-bearing (`app/services/invalidation.py`):

- **Set-based, not looping** (`:140`): `UPDATE belief_inheritance SET invalidated_at=:t … WHERE
  belief_id=:b AND invalidated_at IS NULL` flips all holders in one statement. `affected_edge_count`
  is the statement's `rowcount`. The strong-path consistency test measures **0 split reads** against
  this real commit; the eventual per-holder baseline shows split reads — 1 commit vs 9.
- **Idempotent** (`:94`): the `FOR UPDATE` guard serializes concurrent invalidations; a second call
  raises `AlreadyInvalidated` → HTTP 409, and only one `audit_log` row is written.
- **Snapshot HLC captured *before* the write** (router): HLC is monotonic, so `snapshot_hlc` is
  strictly earlier than the commit timestamp. Capturing it *inside* the write txn could make it equal
  the commit ts (an MVCC read at exactly `ts` sees writes at `ts`), which would make the
  certificate's "belief was active before" proof circular.
- **Retry-wrapped** (`routers/beliefs.py:98`): the whole txn is the retry unit for a `40001`
  serialization error; `BeliefNotFound` / `AlreadyInvalidated` short-circuit to 404/409, exhaustion
  maps to a clean 503.
- **Provenance never blocks durability**: the closure replay + S3 write happen post-commit; a failed
  replay leaves `closure_content_hash` null (honest), a failed PUT stamps `cert_status='failed'`.

The lineage CTE stays **unfiltered** — revocation is annotated, never deleted — so provenance and
the trace path still fully resolve after an invalidation.

---

## 3 · AS OF SYSTEM TIME and deterministic replay

The whole staleness/audit story rests on real CockroachDB time-travel. Two mechanisms build on it:
the **deposition** (`time_travel.py`) reads one agent's beliefs as of a past instant; the **replay**
(`replay.py`) reconstructs a belief's full closure as of a past instant and content-hashes it.

```mermaid
flowchart TD
    IN["as_of (ISO-8601 or HLC decimal)"] --> N{normalize_as_of}
    N -->|ISO| Q["parse → re-serialize → quote"]
    N -->|HLC| BARE["use bare decimal"]
    N -->|else| E400["ValueError → HTTP 400"]
    Q --> CONN
    BARE --> CONN

    subgraph CONN["one physical connection · one explicit txn"]
        SET["1st statement:<br/>SET TRANSACTION AS OF SYSTEM TIME &lt;literal&gt;"]
        SET --> SEL["parameterized SELECT<br/>(belief + closure CTE, same snapshot)"]
        SEL --> CLT["cluster_logical_timestamp() == t0<br/>(AOST-engaged assertion)"]
    end

    CONN --> HASH["canonical (sorted-key) JSON<br/>→ sha256 content_hash"]
    SET -.->|out-of-GC-window| GC["CRDB errors at SET →<br/>caught → HTTP 400, never 500"]

    style CONN fill:#1e2a44,stroke:#6b8cae,color:#e8eef7
```

- **The timestamp cannot be a bind parameter** in CockroachDB, so it is validated and **inlined as a
  literal** while the SELECT stays fully parameterized — the raw request string never reaches SQL
  (`time_travel.py:36` normalize, `:97` the `SET`).
- **Transaction-scoped**: `async with engine.connect()` guarantees one pooled DBAPI connection;
  `async with conn.begin()` an explicit (non-autocommit) txn; the `SET` is the first statement, so
  every following read is at the historical snapshot.
- **AOST-engaged proof**: inside the txn `cluster_logical_timestamp()` equals the requested HLC
  exactly — the Phase-1 done-test asserts this, so a passing test proves the read really time-travels
  (t0 captured → commit a write → re-query @t0 still returns the old world).
- **GC-bounded, and honest about it**: raw AOST reaches back only `gc.ttlseconds` (4500s ≈ 75 min on
  this tier). An out-of-window `as_of` fails inside CRDB at the `SET` and is translated to a **400,
  never a 500** (`_AOST_RANGE_ERRORS`). Durability past that window is the certificate's job (§4).
- **"Byte-identical" is falsifiable**: `content_hash` is sha256 over the canonical JSON of the
  *reconstructed world only*. The test commits a closure-changing write (a new inheritance edge),
  then shows the replay at the OLD timestamp still hashes identically (MVCC hides it) while a current
  read shows a grown closure with a different hash. Three real non-determinism sources would break
  this — non-total row ordering (fixed by `ORDER BY depth, generation, agent_id` over a unique UUID),
  non-canonical serialization, and any `now()`/random in the path.

### When *not* to use `AS OF SYSTEM TIME` — the counterfactual, and the two clocks

The most instructive AOST decision in this codebase is the one where we **refused to use it**.

`GET /beliefs/{id}/counterfactual-invalidation?at=T` answers *"if this belief had been invalidated at
T, which downstream verdicts change?"* An earlier design note assumed it would call
`replay.closure_snapshot(belief, as_of=T)` — reusing the machinery above. That would have been a
**category error**, and naming it is the clearest statement of the project's central distinction:

|  | **MVCC time** (`as_of`) | **Business time** (`at`) |
|---|---|---|
| What it addresses | the state of the **database** | the state of the **world** |
| Where it comes from | `cluster_logical_timestamp()`, an HLC | `decisions.decided_at` / `belief_performance.window_start` |
| Reachable range | **75 minutes** (`gc.ttlseconds = 4500`) | ~400 days of modeled history |
| Answers | *"what did we know then?"* | *"what was happening then?"* |

`T` for the counterfactual is a `decided_at` instant roughly **400 days ago**. AOST is therefore both
the **wrong clock** *and* **out of window** — and worse, the rows in question were all physically
`INSERT`ed at seed time, so they **never existed in MVCC history at `T` at all**. Time-travelling to
`T` would faithfully reconstruct a database that contained none of them.

No reconstruction is needed anyway: every belief-driven decision already carries `driving_belief_id`
and `decided_at`, so the affected set is a plain deterministic `WHERE` over immutable columns —
`{ driving_belief_id = X AND decided_at > T }`. The parameter is deliberately named `at`, **not**
`as_of`, so the API surface itself signals which clock is in play (`counterfactual.py:42-54`).

> This is the same discipline as the honesty ledger: an immutable row and a rotted rule are two
> different facts, and the whole system is built to never let one impersonate the other.

---

## 4 · The certificate and the independent certifier

An invalidation emits a tamper-evident certificate to S3. Its integrity model is **sha256 +
AOST-reproducibility — no HMAC** (a shared secret would let the verifier forge too). The strong claim
is that a second machine, in a different language stack, independently re-derives the same content
address of the pre-kill world.

```mermaid
flowchart LR
    subgraph EP["endpoint — async SQLAlchemy · Windows"]
        E1[invalidate + capture pre_state] --> E2["build_certificate<br/>belief before/after · staleness_evidence<br/>from real belief_performance · pre_invalidation_state<br/>+ closure_content_hash"]
        E2 --> E3["content_hash = sha256 over<br/>canonical JSON minus the hash"]
        E3 --> S3E[(S3)]
        E3 --> AL[(audit_log<br/>commit_hlc · content_hash)]
    end

    subgraph LAM["Lambda lineage-certifier — sync psycopg · Linux"]
        L1[read audit_log → snapshot_hlc] --> L2["replay closure<br/>AS OF SYSTEM TIME snapshot_hlc"]
        L2 --> L3["re-derive hash with the SHARED<br/>canonicalizer (certificate.py)"]
        L3 --> L4{compare}
        L4 -->|match| AG["agreed"]
        L4 -->|differ| DIS["disagreed (reported, not raised)"]
        L4 -->|no counterparty| UN["unavailable (≠ pass)"]
        L3 --> S3L[(S3 — own cert)]
    end

    AL --> L1
    S3E -. re-fetch + re-verify sha256 .-> LAM

    style EP fill:#1e2a44,stroke:#6b8cae,color:#e8eef7
    style LAM fill:#1e3a2e,stroke:#6bae8c,color:#e8eef7
```

Real end-to-end result, from a live AWS invocation: the endpoint's issue-time hash and the Lambda's
AOST-replayed hash are the **same value** —
`sha256:1e40b7a72fe1796cc91fa49bd119e1f239c889c651fc7dbaa70963eb38c393ff` — computed on different
machines, in different async/sync stacks, from different reads.

Why the certifier **re-derives** instead of trusting the embedded hash: hash-coverage proves a
document has not *changed*; it can never prove the document was *true*. A certifier that signed over
an app-computed hash would be attesting to the one claim it took on faith. So it reconstructs the
world from CockroachDB's own MVCC history and hashes it independently — the same discipline the brake
applies by recomputing structure instead of trusting a label (§5).

**The canonicalizer is deliberately shared.** `canonical_json` / `canonical_digest` / `closure_world`
live in `certificate.py` (import-safe, zero app deps — which is exactly why the Lambda can reach
them) and both sides call them. Two independently-implemented canonicalizers that merely *agree
today* would make the cross-check a false guarantee waiting to silently diverge. Verified: the async
and sync halves hash identically at current state *and* at a past HLC via AOST
(`scripts/probe_closure_hash_parity.py`), and a test asserts the two SELECTs project the same column
set so a forgotten column fails loudly rather than producing a spurious `disagreed`.

**Stated honestly** (from the honesty ledger): the tri-state agreement is *recorded* in the
hash-covered certificate body but not prominently surfaced (no `audit_log` column yet); `content_hash`
is unkeyed so it proves integrity, not authorship (asymmetric signing is documented, not built); and
`staleness_evidence` / `affected_closure` counts are not part of the cross-checked closure hash.

---

## 5 · The witness-construction brake

The grounded AML agent's verdict passes through a deterministic brake before it can raise a FLAG. The
governing invariant: **a FLAG always requires a structural witness the graph itself produces** — the
LLM's citation is checked against the rows, never trusted.

```mermaid
flowchart TD
    CLAIM["LLM claim<br/>typology (free string) + cited path"] --> G0{"Gate 0:<br/>typology ∈ retrieval results?<br/>(k=3 vs 4-doc corpus)"}
    G0 -->|no| INS1["INSUFFICIENT_COVERAGE<br/>typology_not_retrieved"]
    G0 -->|yes| G1A{"Gate 1a:<br/>typology FLAG_CAPABLE?<br/>{CYCLE, SCATTER-GATHER}"}
    G1A -->|no| INS2["INSUFFICIENT_COVERAGE<br/>typology_not_decidable"]
    G1A -->|yes| GRAPH{"Gate 1b:<br/>recompute structure<br/>from UNLABELED edges"}

    GRAPH -->|MATCH| VER{"verify_witness_path<br/>re-derive cited path from rows"}
    GRAPH -->|CONCLUSIVE_NO| NF["NO_FLAG<br/>(search closed, no sink)"]
    GRAPH -->|INCONCLUSIVE| INS3["INSUFFICIENT_COVERAGE<br/>search_reached_extract_boundary<br/>(boundary account named)"]

    VER -->|faithful| FLAG["FLAG ✓ witness required"]
    VER -->|unfaithful / superset| INS4["INSUFFICIENT_COVERAGE<br/>unfaithful_citation"]

    style FLAG fill:#3a2a1e,stroke:#ae8c6b,color:#f7eee8
    style GRAPH fill:#1e2a44,stroke:#6b8cae,color:#e8eef7
```

Why three graph outcomes and not two: the evidence layer is a **bounded extract** (1,500 edges of a
5M-row universe), and **220 of 648 accounts are sinks** with no outgoing edge in the slice. So "the
cycle search found nothing" has two meanings. A negative is `CONCLUSIVE_NO` only if the search closed
*without touching a sink*; if it hit one, the honest answer is `INSUFFICIENT_COVERAGE` and the
boundary account is named. This — not a confidence threshold — is what genuine uncertainty means
here.

What the brake deliberately does **not** do:
- **It never reads a label.** `aml_graph.py` recomputes structure from the unlabeled edge set and
  selects no label column — otherwise the LLM's reasoning would be decorative.
- **Retrieval distance gates nothing.** A `distance < τ` rule was measured and rejected: an
  out-of-corpus FAN-IN description retrieves an in-corpus doc *closer* than every in-corpus query, so
  a distance gate would confidently ground a verdict on a typology the corpus can't cover. The margin
  is recorded as provenance and decides nothing; safety is preserved because a witness is still
  required.
- **It rejects supersets.** A cited path with one extra transaction is rejected exactly like a
  fabricated one — accepting supersets lets a model cite the whole neighbourhood and always "contain"
  a cycle.

`FLAG_CAPABLE = {CYCLE, SCATTER-GATHER}` is not hand-picked — it is the set measured to have **zero
cross-typology false witnesses** over all 1,500 edges, and a living-invariant test enforces it. That
selection **replicates on the never-tuned hold-out** (the structural detection eval): the same two
typologies come back sound on data no design decision saw.

### 5.1 · The same discipline, applied to prose: the explanation-faithfulness guard

The brake governs the **verdict**. It says nothing about the **story the model tells about the
verdict** — and a supervisor reads the story. So the agent's narrated `rationale` passes through a
second, independent guard before a human ever sees it.

```mermaid
flowchart TD
    R["LLM rationale (prose)<br/>+ the EXACT evidence it was shown"] --> J{"GEval faithfulness judge<br/>(gemma, local — never OpenAI)"}

    J -->|"score ≥ threshold"| SUP["SUPPORTED<br/>prose shown as written"]
    J -->|"score &lt; threshold"| UNS["UNSUPPORTED<br/>prose WITHHELD"]
    J -->|"judge unreachable /<br/>timeout / unparseable"| UNA["UNAVAILABLE<br/>prose WITHHELD"]

    UNS --> DET["deterministic reconstruction<br/>shown in its place"]
    UNA --> DET

    V["VerdictOutcome<br/>(FLAG / NO_FLAG / INSUFFICIENT_COVERAGE)"] -.->|"passes through<br/><b>UNTOUCHED</b>"| OUT
    SUP --> OUT["what the supervisor sees"]
    DET --> OUT

    style V fill:#1e2a44,stroke:#6b8cae,color:#e8eef7
    style UNA fill:#3a1518,stroke:#E5484D,color:#f7e8e8
    style DET fill:#1e3a2e,stroke:#6bae8c,color:#e8eef7
```

Two properties carry the whole design (`faithfulness_guard.py:11-47`):

- **The guard never touches the verdict — only the rationale.** The verdict comes from deterministic
  structural evidence and never reads the prose. An unfaithful rationale means the *explanation* is
  untrustworthy, which is a **different fact** from the verdict being wrong: the witness is real
  whether or not the model narrated it faithfully. Letting a probabilistic prose judge downgrade a
  structurally-proven FLAG would invert the entire reason the brake exists. A test asserts field-level
  equality of `verdict` / `reason` / `witness_txn_ids` / `corpus_doc` across the guard.
- **It fails closed.** Judge down, credits gone, timeout, unparseable score → the prose is
  **withheld**, never shown unguarded. This is cheap here precisely because the deterministic verdict
  and the deterministic reconstruction are *always* available: a withheld rationale still leaves a
  fully-usable finding. The supervisor loses the LLM's gloss and keeps the truth.

**The instrument's limits travel with every result.** `SUPPORTED` means *"passed the check"* — **not
"proven faithful."** The judge has a nonzero, measured false-negative rate on dense structural prose
(a fabricated-hop negative scored 0.50; a genuinely faithful SCATTER-GATHER anchor scored 0.40). This
is a probabilistic guard layered on a deterministic verdict, never a proof. And it is **explicitly not
a poisoning defense**: it compares prose against the *retrieved rows*, so if those rows were
themselves poisoned it would happily pass a claim faithful to the poison. Defending the rows is a
different control — the next section.

---

## 6 · Verifying the provenance graph itself (A1–A4)

Everything upstream trusts `belief_inheritance`. The lineage CTE walks it, the closure `UPDATE`
flips it, the certificate hashes it. So: **what verifies the edges themselves?**

`GET /beliefs/{id}/provenance-audit` walks a belief's whole closure and proves every edge was created
by a genuine spawn event, from an ancestor that actually held the belief, before any invalidation.

```mermaid
flowchart TD
    E["one belief_inheritance edge<br/>(belief, from_agent, to_agent, inherited_at, invalidated_at)"] --> M{"backing rows present?<br/>(both agents + a spawn time)"}
    M -->|no| INC["INCONCLUSIVE<br/><i>surfaced, never a silent pass</i>"]

    M -->|yes| A1{"<b>A1</b> genealogy-consistency<br/>from_agent == to_agent.parent_id"}
    A1 -->|violated| AN["ANOMALOUS<br/>(codes + per-edge evidence)"]
    A1 -->|ok| A2{"<b>A2</b> spawn-time consistency<br/>inherited_at == to_agent.spawned_at"}
    A2 -->|violated| AN
    A2 -->|ok| A3{"<b>A3</b> source-was-a-holder<br/>from_agent held it at inherited_at"}
    A3 -->|violated| AN
    A3 -->|ok| A4{"<b>A4</b> not-post-invalidation<br/>inherited_at precedes any<br/>invalidation it depends on"}
    A4 -->|violated| AN
    A4 -->|ok| OK["edge ok"]

    OK --> CLEAN["CLEAN — every edge satisfies A1–A4"]

    style CLEAN fill:#1e3a2e,stroke:#3FE0A8,color:#e8eef7
    style AN fill:#3a1518,stroke:#E5484D,color:#f7e8e8
    style INC fill:#2a2440,stroke:#8c7bae,color:#e8eef7
```

Each invariant names a distinct forgery. **A1** violated is a *phantom ancestor* — provenance grafted
onto a lineage the node never descended from. **A2** violated is an edge inserted *after the fact*, at
a timestamp corresponding to no spawn event. **A3** violated means inheriting from an ancestor that
never held the belief. **A4** violated is literally *"provenance traces to a later-invalidated
source."* The three-way outcome (**CLEAN / ANOMALOUS / INCONCLUSIVE**) deliberately mirrors the AML
brake's — a missing row is surfaced, never silently treated as clean, the same way an unreachable
judge fails closed in §5.1.

**The honest scope — this is verification, not a patch.** There is **no live vulnerability here, and
we will not dress one up.** The only two code paths that ever `INSERT` a `belief_inheritance` row are
`seed.seed()` and `lifecycle.spawn_child()`; `spawn_child()` is not exposed by any HTTP route; and
both writers maintain A1–A4 *by construction*. Through the application, an illegitimate edge **cannot
arise**. What the audit actually defends against is **out-of-band tampering** — a direct SQL write by
an actor with cluster credentials, a future write path that doesn't preserve the invariants, a buggy
migration, or a future multi-belief world. Such an edge is the *clean-label* analog: every foreign key
is real, the timestamp is plausible, and it passes every structural and referential check the database
can make. Its illegitimacy is visible **only** by walking the provenance chain. That walk is this
audit.

Proving it works required constructing the attack. `tests/test_provenance_audit.py` seeds the real
9-node / 8-edge closure into the isolated `demo` database, asserts CLEAN, then injects three poisoned
edges **by direct SQL that bypasses `spawn_child` entirely** — the exact out-of-band vector — and
asserts each trips *exactly* its own invariant while every legitimate edge stays OK. Note the last one:
a legitimately-invalidated closure is **not** flagged, because every real edge's spawn-time
`inherited_at` precedes the single invalidation commit. The test snapshots `defaultdb` before and after
and asserts byte-identity, so constructing poison never touches the console's real closure.

**Taxonomy, cited at the confidence it was actually verified:** the inheritance graph is the fleet's
long-term memory, so an out-of-band edge is memory-store poisoning. OWASP **ASI06 (Memory & Context
Poisoning)** is the primary citation — **verified against the source**, and its own guidance
("provenance metadata on every memory write", "periodic evaluation against ground truth") is precisely
what A1–A4 check. MITRE ATLAS **AML.T0080** is labeled **secondary-sourced**: `atlas.mitre.org` is a JS
SPA that could not be rendered, so the ID is corroborated across independent secondary sources rather
than confirmed on the authoritative page. It is labeled that way rather than asserted as primary —
the same standard as the MCP disclosure. → `app/services/provenance_audit.py`

---

*Every diagram above corresponds to code under `app/services/` and `lambda/certifier/`; the
engineering history and the reasoning behind each decision are recorded in [NOTES.md](NOTES.md).*
