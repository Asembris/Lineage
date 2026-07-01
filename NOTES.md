# NOTES.md — Lineage backend scratch log

Chronological log of what was tried, what worked, and CockroachDB-specific gotchas so
later sessions don't re-walk dead ends. Newest notes at the bottom of each section.

## Phase 1

### Connection / driver
- `.env` var is `DATABASE_URL` = `postgresql://...@...cockroachlabs.cloud:26257/...?sslmode=verify-full`.
  No `sslrootcert` param — CRDB Cloud serves publicly-trusted certs, so `verify-full` works
  off the system trust store. Do not add a cert file.
- App (async) uses `postgresql+psycopg://` (psycopg 3). Alembic (sync) uses the same
  `postgresql+psycopg://` scheme — psycopg 3 supports both sync and async.
- `sslmode=verify-full` is a libpq/psycopg connect arg, passed straight through in the URL.

### CockroachDB-specific decisions
- **UUID PKs** with `gen_random_uuid()` server default — CRDB best practice, avoids
  sequential-insert range hotspots.
- **VECTOR column** on `beliefs.embedding`: dim **1536** (matches OpenAI text-embedding-3-small
  so Phase 2 needs no re-migration). Phase 1 seeds a *deterministic placeholder* vector
  (fixed-seed numpy, normalized) — clearly a placeholder; real embeddings computed in Phase 2.
  Embeddings drive vector SEARCH (Phase 2), not the staleness signal, so placeholder is honest.
- **Vector INDEX** (`CREATE VECTOR INDEX`, C-SPANN): deferred to Phase 2 (not needed by either
  heart endpoint). Column created now. TODO: confirm via MCP that the index type is available
  on Basic tier so Phase 2 has no surprise.
- Custom SQLAlchemy `Vector` type (app/types_crdb.py) renders `VECTOR(n)` DDL and marshals
  list[float] <-> '[..]' literal. NOT using the pgvector package (targets PG extension; don't
  want to rely on OID-compat guesses).

### AS OF SYSTEM TIME (the heart)
- AOST **cannot take a bind parameter** — the timestamp must be inlined in SQL text.
  Therefore validate/normalize the incoming `as_of` ourselves (no raw string reaches SQL):
    * ISO-8601 -> parse with datetime.fromisoformat, re-serialize, wrap in quotes.
    * HLC decimal (what cluster_logical_timestamp() returns) -> use bare/unquoted.
    * anything else -> 400.
- Mechanism: transaction-scoped AOST. On a single AsyncConnection, in an explicit
  transaction, the FIRST statement is `SET TRANSACTION AS OF SYSTEM TIME <literal>`, then a
  normal SQLAlchemy select. Only the timestamp is ever inlined; the query stays parameterized.
- Guaranteeing same physical connection + no autocommit: use `async with engine.connect()`
  (one AsyncConnection == one pooled DBAPI connection) + `async with conn.begin()` (explicit
  txn). Engine is NOT in AUTOCOMMIT isolation. See app/services/time_travel.py.
- TODO (MCP): confirm which builtin reflects the AOST read timestamp inside the txn
  (cluster_logical_timestamp vs transaction_timestamp) for the test's positive
  "AOST-engaged" assertion.
- Time-travel depth bounded by `gc.ttlseconds`. TODO (MCP): record Basic-tier default.
  The done-test only looks seconds back, well within GC.

### CONFIRMED via scripts/probe_crdb.py (direct psycopg connection, 2026-07-01)
- **Cluster:** CockroachDB CCL **v25.4.10**, region `aws-eu-central-1` (Frankfurt).
- **VECTOR type:** works — `VECTOR(3)` DDL + `'[1,2,3]'` insert/select roundtrip OK.
- **VECTOR INDEX:** `CREATE VECTOR INDEX` is **AVAILABLE on Basic tier** — Phase 2 vector
  search has no surprise. (Still deferring index creation to Phase 2.)
- **gc.ttlseconds = 4500 (75 min)** on RANGE default → max AOST time-travel depth is 75 min.
  Done-test only reaches seconds back — safe.
- **AOST read-timestamp builtin = `cluster_logical_timestamp()`.** Inside a txn with
  `SET TRANSACTION AS OF SYSTEM TIME {t0}`, `cluster_logical_timestamp()` == t0 **exactly**;
  a fresh non-AOST read returns a later value. `transaction_timestamp()`/`now()` also reflect
  the historical wall time, but `statement_timestamp()` keeps advancing (real clock) — do NOT
  use statement_timestamp for the AOST-engaged assertion. Use cluster_logical_timestamp equality.
- **Connection stack gotchas (Windows):**
  1. psycopg async needs `WindowsSelectorEventLoopPolicy` (default Proactor loop errors).
  2. `sslmode=verify-full` + libpq 14 (bundled in psycopg[binary]) → no `sslrootcert=system`
     support (that's libpq 16+). Point `sslrootcert` at `certifi.where()` (Mozilla bundle);
     CRDB Cloud certs chain to it.
  3. Generic postgresql dialect can't parse CRDB's `version()` string → use the
     **`cockroachdb+psycopg://`** dialect (sqlalchemy-cockroachdb). Not a stack substitution;
     it's the SQLAlchemy dialect for our chosen DB, running on psycopg 3.

### Phase 1 DONE (2026-07-01)
- Migration applied to live cluster (all 5 tables + indexes). Seed: 24 agents / 2 bloodlines,
  1 founding belief down crimson spine + 1 branch edge (crimson-4 -> crimson-5b), 8 inheritance rows.
- Endpoints live and returning real cluster data:
  * GET /agents/{id}/beliefs?as_of=  (real SET TRANSACTION AS OF SYSTEM TIME; ISO + HLC forms)
  * GET /beliefs/{id}/lineage         (recursive CTE; returns 9-node tree incl. branch)
- Tests: `pytest` -> 2 passed. test_aost_hides_a_committed_write proves MVCC time-travel
  (t0 captured -> commit -> re-query@t0 still N, now N+1) + positive AOST-engaged assertion
  (cluster_logical_timestamp()==t0 inside txn). test_lineage_resolves_full_spine_and_branch.
- Diagnostic scripts kept: scripts/probe_crdb.py, scripts/demo_endpoints.py.
- DO NOT start Phase 2 (agents / belief_performance / OpenAI) without approval.

### Time concepts — do not conflate
- CRDB **MVCC time-travel** (AOST) = DB state as of a past *transaction* time. Phase 1 proves this.
- `formed_at` / `belief_performance.window_*` = app-level columns; they drive the Phase 2/3
  staleness story ("valid then / rotten now"). Different clock. Phase 1 does not touch staleness.

## Phase 2

### Plan (approved)
- Keep Phase 1 seed + tests intact. Backfill real historical `decisions` (deterministic,
  OpenAI-FREE) across the belief-holding generations; prove the live spawn once
  (crimson-7 -> crimson-8) later. Hermetic offline done-test. C-SPANN vector search. No new
  table (decisions IS the record). 250 belief-driven decisions/window (SE ~0.032 worst case).
- Integrity line: the WORLD drifts (transaction fraud labels shift over time); the belief and
  the agent's application are fixed; performance is ALWAYS the aggregation of verdict-vs-fraud.
  Never write a confidence number. The counterfactual recovery test is the proof.

### Migration 0002 (applied to live cluster, committed)
- DDL only, OpenAI-free: `CREATE VECTOR INDEX ix_beliefs_embedding ON beliefs (embedding)`
  (raw op.execute — Alembic can't emit CRDB vector DDL) + btree
  `ix_decisions_belief_time` on decisions(driving_belief_id, decided_at) = the access path
  for per-window aggregation. Alembic logs "Will assume non-transactional DDL" — CRDB runs
  the vector-index create fine outside a txn. Re-embedding the belief with a REAL vector is
  deferred to the post-gate agent-brain phase (it needs OpenAI; pre-gate stays offline).

### Deterministic backfill (app/sim/transactions.py + seed/backfill_decisions.py)
- Drift is a 3-layer stochastic process, all seeded (world SEED=20260701, policy seed 7788):
  hidden logistic trend + per-window Gaussian regime shock + a modeled fraud CAMPAIGN that
  spikes ~gen5 and recedes gen6 + Bernoulli sampling per txn. Off-pattern background fraud is
  non-zero & flat. Nothing about performance is written; only labels + verdicts.
- Decision policy: target-pattern (mcc 5411, <$180, age>6mo) -> approve, driving_belief=origin
  (the belief-driven subset). Everything else -> generic verdict, driving_belief=NULL (table
  realism only; excluded from this belief's performance). Window i -> crimson-i; window 5
  splits ~30% to living branch crimson-5b.
- **Emergent curve (4000 rows, computed from raw decisions, matches the intent):**
  conf g0..g7 = .924 .952 .876 .852 .724 .556 **.624** .528 ; fraud% 7.6->47.2 ;
  frauds_approved 19->118. The gen-6 bump (.556->.624->.528) is the campaign recession dip —
  a monotone sigmoid could never produce it. fp_rate == 0 across all windows and is HONEST:
  this belief only ever approves, so its failure mode is approving fraud (false negatives),
  never false positives. Baseline off-pattern fraud wobbles 1.2%-6.4%, no trend.

### Gotchas
- `seed/` must be run as a module (`python -m seed.backfill_decisions`), NOT as a file path —
  running the file puts seed/ on sys.path[0] and shadows the `seed` package self-import.
- 4000-row ORM `add_all` + commit over CRDB Cloud takes ~60s (server-default UUID PKs force
  per-row RETURNING). Fine for a one-shot backfill; if it ever needs to be faster, switch to
  client-side uuid4 ids + a bulk `insert()` executemany.
- Python block-buffers stdout to a file — a long backfill shows NO output until it exits.
  Don't mistake a buffered run for a hang.

### Live agent (app/services/agent_brain.py + openai_client.py + embeddings.py)
- **AVG antivirus MITMs outbound HTTPS (port 443).** It presents a cert signed by AVG's local
  root CA (env leaks `SSLKEYLOGFILE=\\.\avgMonFltProxy\...`). That CA is in the Windows trust
  store but NOT in certifi's bundle -> default httpx/openai fail CERTIFICATE_VERIFY_FAILED.
  Fix: app/services/openai_client.py builds the AsyncOpenAI with an SSL context from
  `create_default_context()` + `load_default_certs(SERVER_AUTH)` (Windows ROOT store). CRDB is
  unaffected (port 26257, not intercepted) and keeps its own certifi handling in app/config.py.
- Retrieval uses CRDB cosine vector search: `embedding <=> (:qvec)::VECTOR(1536)` ORDER BY,
  over the agent's active held beliefs. Operators `<->`/`<=>`/`<#>` all work on v25.4.10.
- Decision brain: gpt-4o-mini, strict json_schema response_format, temperature 0. is_fraud
  (ground truth) is stored but NEVER put in the prompt. Model returns exact driving_belief_id;
  we validate it against the retrieved candidates (no dangling refs) before persisting.
- Live demo verified (scripts/demo_agent.py): crimson-7 APPROVED a window-7 fraud @0.95,
  citing the origin belief — the stale-belief harm, real API call end to end.

### Lifecycle (app/services/lifecycle.py)
- spawn_child(parent) = insert child + one belief_inheritance edge per ACTIVE held belief +
  retire parent, all in ONE `async with s.begin()` transaction (atomic). Child immediately
  resolves in the Phase-1 lineage CTE with zero changes to it.

### belief_performance (app/services/performance.py)
- recompute_belief_performance(belief_id, windows): DELETE this belief's rows, then one row
  per non-empty window from the SAME aggregation the backfill report used. confidence =
  correct/total; nothing hardcoded. generation_windows() (app/sim/transactions.py) gives the
  8 gen windows so backfill + performance bucket identically.

### Phase 2 DONE (2026-07-01)
- All 4 tests pass (2 Phase-1 + 2 Phase-2), Phase 1 untouched:
  * test_staleness_emerges_and_recovers — COUNTERFACTUAL: seeds controlled decisions (5% fraud
    early / 55% late) -> early conf 0.95, late 0.45; flip late is_fraud=false -> late RECOVERS
    to 1.00. Proves staleness is DERIVED, not stored (the Phase-2 kill-shot).
  * test_spawn_inherits_active_beliefs — spawn crimson-7 -> crimson-8, real inheritance edges,
    child in lineage, parent retired.
- Demos: scripts/demo_agent.py (live OpenAI verdict), scripts/demo_staleness.py (visible
  counterfactual recovery). Both reseed first.
- Openai==1.59.6 added to requirements. gpt-4o-mini + text-embedding-3-small.
- NOT YET DONE (deferred / Phase 3): belief_performance is written by an explicit recompute
  call, not yet auto-driven by a running fleet; no atomic invalidation endpoint / S3 cert /
  Lambda yet (that's Phase 3). Do not start Phase 3 without approval.
