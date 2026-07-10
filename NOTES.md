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

## Phase 3

### Plan (approved 2026-07-03)
- Approved: reuse belief_inheritance for the closure state (per-edge revocation — the
  constitution's own wording says the closure is "via belief_inheritance"; NOT a new fleet
  table, NOT a single-row belief flip which wouldn't exercise cross-key atomic txns).
  Certifier-only Lambda. Cert integrity = sha256 + AOST-reproducibility (no HMAC — the AOST
  cross-check is the stronger proof). Leaked-fraud proof uses the Phase-2 DETERMINISTIC
  predicate (matches_target), not live OpenAI (avoids flaky API calls in a timing test).
  Plain-zip Lambda deploy, no IaC. Sequenced steps 1-9, gate hard at step 1.

### Step 1 — AWS probe GATE (scripts/probe_aws.py)
- **AVG antivirus MITMs boto3 too** (same 443 interception as OpenAI in Phase 2). Fix lives
  in app/services/aws_client.py: merge certifi + the Windows ROOT store (which holds AVG's
  installed CA) into one PEM bundle, point botocore's `verify` at it. Real TLS verification,
  never verify=False. No-op on Linux (Lambda) — enum_certificates is Windows-only there.
- **Gate result:** creds OK (arn user/lineage-app, acct 265243686715); S3 head-bucket +
  PUT/GET/DELETE round-trip OK; lambda:ListFunctions OK; **iam:CreateRole DENIED**. So the
  Lambda EXECUTION ROLE cannot be created from this IAM user — needs a console-level fix by
  the account owner. This blocks ONLY step 8. Steps 2-7 use the S3 creds directly, unblocked.
- AWS config flows through Settings (pydantic reads .env but does NOT export to os.environ, so
  boto3 couldn't see the creds); aws_client passes them explicitly when present, else falls
  back to the default chain (for Lambda's execution role). Optional fields, default None.
- boto3==1.35.71 added to requirements.

### Step 2 — migration 0003 (applied to live cluster, committed)
- belief_inheritance.invalidated_at / invalidated_by (per-holder closure revocation — the
  MULTI-ROW target that makes the atomic txn load-bearing). + audit_log table (actor,
  affected counts, commit_hlc, cert pointer/status/hash, created_at). Lineage CTE stays
  UNFILTERED so provenance/trace still fully resolve (annotate revocation, never delete).

### Step 3 — atomic invalidation (app/services/invalidation.py)
- invalidate_belief(): ONE serializable txn. FOR UPDATE the belief (idempotency guard ->
  AlreadyInvalidated), then SET-BASED UPDATEs (beliefs + `UPDATE belief_inheritance ... WHERE
  belief_id=X`) — all holders at once, NO per-holder loop. audit_log written in the same txn.
- **Snapshot-HLC subtlety:** cluster_logical_timestamp() is captured on a SEPARATE read
  connection BEFORE the write txn opens. HLC is monotonic => strictly earlier than the commit
  ts. If captured INSIDE the write txn it could equal the commit ts (MVCC reads at exactly ts
  see writes at ts) and the certificate's "belief was active before" AOST proof would be
  circular. Verified: AOST @ snapshot_hlc shows active / all-8-edges-open.

### Step 4 — certificate + S3 (certificate.py, s3_audit.py)
- Cert JSON: belief before/after, staleness_evidence pulled from REAL belief_performance
  (first vs last window confidence + last-window frauds_approved — valid-then/rotten-now,
  never asserted), affected_closure, db_snapshot_hlc, content_hash = "sha256:" + hexdigest
  over canonical (sorted-key) JSON minus the hash. verify() re-derives it.
- s3_audit put/get via real boto3 (asyncio.to_thread from the async endpoint). POST-COMMIT
  side effect: a failed PUT marks audit_log.cert_status='failed', never rolls back the durable
  invalidation. Smoke: PUT->GET round-trip re-verifies the hash; AOST@db_snapshot_hlc active.

### Step 5 — POST /beliefs/{id}/invalidate (routers/beliefs.py)
- Orchestrates invalidate -> gather staleness -> build cert -> put (to_thread) -> stamp
  audit_log. BeliefNotFound->404, AlreadyInvalidated->409. Response carries the cert outcome.

### Step 6 — tests/test_atomic_invalidation.py (4 pass, ~132s, live cluster + real S3)
- atomic closure (all 8 edges flip, 0 open, audit counts right) / idempotent (2nd raises,
  1 audit row) / cert S3 round-trip + hash re-verify / **AOST reproducibility**: cert's
  db_snapshot_hlc replays AS OF SYSTEM TIME to reproduce the pre-kill world (active,
  all-open) — CRDB time-travel is the oracle, so the cert can't lie about history.

### Step 7 — consistency proof (app/services/consistency.py, test + demo)
- eventual_invalidate = per-holder fan-out baseline (N+1 separate commits, per-holder delay
  models fan-out latency). observe_closure = concurrent sampler classifying ALL_ACTIVE /
  SPLIT / ALL_INVALIDATED. Measured: strong = 1 commit / 0 split samples (transition still
  witnessed); eventual = 9 commits / split samples observed. Delay-FREE fact: 1 vs 9 commit
  points => split state unreachable vs reachable. Leaked-fraud (deterministic matches_target,
  per-holder edge view): a committed mid-fan-out state => crimson-7 APPROVES the fraud while
  crimson-5b BLOCKS; the atomic correction erases that state. 3 tests pass.
- **Observer flake fixed:** observer sets a `ready` event after its connection is live + first
  sample; the strategy gates on it. Otherwise CRDB Cloud's ~1s TLS handshake outlasts a
  guessed pre-delay and the observer misses a fast strong commit (all samples ALL_INVALIDATED).
- **CRDB gotcha:** two concurrent `TRUNCATE ... CASCADE` (I accidentally double-ran the demo)
  -> "cannot perform TRUNCATE on decisions which has indexes being dropped" (TRUNCATE drops/
  recreates indexes; overlapping jobs collide). Transient; clears when the schema-change job
  settles. Don't run two reseeds at once.

### Full regression: 11 tests pass (4 Phase-1/2 + 4 atomic-invalidation + 3 consistency).

### Step 8 — certifier Lambda DONE (2026-07-03)
- Role provided by owner: `arn:aws:iam::265243686715:role/lineage-certifier-role`
  (AWSLambdaBasicExecutionRole + inline s3:PutObject/GetObject on the bucket). lineage-app
  created the FUNCTION against it with no new IAM perms — the gate fix worked as planned.
- **Standalone independent auditor** (endpoint UNTOUCHED): invoke with {belief_id}; reads
  audit_log for snapshot_hlc, replays AS OF SYSTEM TIME to confirm belief active + closure
  8/8 open BEFORE the kill, builds the cert (+ hash-covered aost_verification stamp), PUTs to
  S3, stamps audit_log cert_status='written'. lambda/certifier/{handler,build,deploy}.py.
- **certificate.py made import-safe**: top-level app/SQLAlchemy imports moved lazily inside
  gather_staleness_evidence, so the Lambda can `import certificate` with zero app deps
  (app.config requires OPENAI_API_KEY/DATABASE_URL — would crash on Lambda). build_certificate
  gained `extra` (merged BEFORE hashing). Endpoint path unchanged, still tested.
- **Packaging (no Docker):** pip install --platform manylinux2014_x86_64 --only-binary=:all:
  --python-version 3.12 --implementation cp psycopg[binary]==3.2.3 certifi. Pulls the
  cp312-manylinux2014_x86_64 psycopg-binary wheel; zip carries real x86_64-linux-gnu .so
  (4.7 MB, direct upload). boto3 NOT packed (in the runtime). Docker was the named fallback,
  not needed. AWS CLI absent -> boto3 deploy (create_function / update_function_code+config).
- **TLS verify-full from Lambda worked FIRST TRY** — the flagged risk (Linux libpq finding a
  CA for CRDB Cloud) was pre-empted by packaging certifi + passing sslrootcert=certifi.where()
  to psycopg.connect (mirrors the app's Windows fix). No sslmode downgrade.
- Handler is SYNC psycopg 3 (no asyncio); AOST via autocommit=False + first-statement
  SET TRANSACTION AS OF SYSTEM TIME {hlc} (hlc inlined, validated numeric — our own value).
- Real invocation (scripts/demo_certifier.py): aost_verified=true, cert in S3, sha256
  re-verified after GET, audit_log.cert_status=written. DATABASE_URL/S3_BUCKET = encrypted
  Lambda env vars. Build artifacts (package/, *.zip) gitignored — rebuild via build.py.
- Redeploy: `python lambda/certifier/build.py && python lambda/certifier/deploy.py`.

### PHASE 3 COMPLETE (2026-07-03) — all 8 steps done. 11 tests pass (4 P1/2 + 4 invalidation
### + 3 consistency). Money-shots live: atomic closure invalidation endpoint, sha256+AOST
### certificate to real S3, measured atomic-vs-eventual consistency proof, certifier Lambda.

## Phase 4 (LEAN) — approved 2026-07-03

Scope: NOT full hardening. Just "nothing 500s embarrassingly in a live demo" + one scoped SSE
addition + one CI workflow. Approved with an explicit correction (see actor validation below).

### Validation + rate limiting (app/schemas.py, services/time_travel.py, ratelimit.py, main.py)
- **actor_id on POST /invalidate — do NOT validate against `agents`.** Explicit design call:
  `agents` = the supervised AI fleet (crimson/azure). The human supervisor doing the
  invalidation is NOT a fleet agent (invalidation is THE one governed human action). So we
  only require a well-formed, non-null identifier: `uuid.UUID` already gives well-formed, and
  a field_validator rejects the all-zeros nil UUID (→422) so a "null identifier" can't produce
  a dangling audit row. Real non-fleet supervisor UUIDs pass through (test proves 200). Real
  actor referential integrity would need a dedicated supervisors/actors table — out of scope.
- **AOST out-of-window → 400, not 500.** A well-formed `as_of` older than the GC TTL (4500s)
  or in the future fails INSIDE CRDB at `SET TRANSACTION AS OF SYSTEM TIME`. time_travel now
  catches that DBAPIError, matches known CRDB substrings, and re-raises ValueError → the router
  maps it to 400. Parse errors were already 400 via normalize_as_of.
- **Rate limiter: hand-rolled, no new dep (chose over slowapi).** Per-(IP, route-template)
  fixed window; route template collapses UUID segments so varying the id can't dodge the limit.
  Concurrency-safe by an `asyncio.Lock` around the read-modify-write with NO await in the
  critical section — a naive unlocked dict would over-admit under a burst (test fires 200
  concurrent checks at a budget of 100 and asserts EXACTLY 100 admitted). 60/min/route default,
  /health exempt, 429 + Retry-After. Middleware in main.py.

### SSE — GET /demo/consistency/stream (sse-starlette; routers/demo.py)
- Streams the eventual fan-out's REAL observer samples live. Verified via scripts/demo_sse.py:
  closure drains 8/8 → 7/8 → ... → 0/8 open over ~7s, SPLIT window plainly visible, then
  ALL_INVALIDATED. Events: start / sample* / summary (+ busy). Summary reports 9 commit points
  vs the atomic endpoint's 1.
- **Deliberately NOT on the lineage trace** — the recursive CTE is ms, so streaming it would be
  fake server-side pacing. The eventual fan-out has genuine multi-second timing worth streaming.
- observe_closure gained an ADDITIVE `on_sample` callback (return value + 3 consistency tests
  unchanged). Chose sse-starlette over hand-rolled StreamingResponse: it owns the event-stream
  wire format, keep-alive pings, and disconnect cleanup — not worth risking a protocol bug on
  camera.
- **This endpoint MUTATES demo state** (reseeds, runs the fan-out to invalidation). A
  module-level single-flight guard serializes concurrent streams so two reseeds can't collide on
  TRUNCATE CASCADE; a `busy` event is sent if one is already in flight. try/finally reaps the
  observer/fanout tasks on client disconnect (GeneratorExit). Repeatable — every run reseeds.
  (Guard hardened post-audit — see "Post-audit integrity fixes" below.)
- **OPERATIONAL RISK (address before any public/shared deployment):** [RESOLVED 2026-07-07 —
  see "Roadmap Item 0 — cluster isolation" at the bottom of this file. The SSE stream now runs in
  a dedicated `demo` database and can no longer wipe `defaultdb`; this risk and its "re-backfill
  after every demo run" remediation no longer apply to the SSE path.] Original note follows:
  this endpoint's blast
  radius is a FULL cluster wipe (run_seed → TRUNCATE CASCADE of all five tables), and nothing
  distinguishes "someone is deliberately watching the demo" from "a browser tab was left open."
  A stale tab's auto-reconnecting EventSource re-triggers the reseed every ~10-15s, silently
  destroying any backfilled state (observed 2026-07-05: it had wiped the 4,000-row backfill +
  8 perf windows; recovery required killing uvicorn so the endpoint couldn't refire, then a full
  reseed+backfill). Not a bug in the endpoint — the reseed-per-request behavior is correct — but
  the coupling of "casual GET" to "irreversible wipe" is unsafe outside a single-operator laptop.
  Fix before deploy: gate reseed behind an explicit intent (POST + confirm token, or an operator
  flag), and/or isolate demo runs to a throwaway database. Ties into the reset/isolation gap
  already flagged for CI (see "CI-vs-LOCAL collision" below) — same root cause: one shared cluster,
  no demo/CI isolation.

### CI — .github/workflows/ci.yml (push to main, sequential pytest)
- Single ubuntu job, Python 3.12, `pip install -r requirements.txt`, `pytest`. No matrix, no
  xdist (matches local; each test reseeds, so intra-run is serial and safe).
- **GitHub secrets required:** DATABASE_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
  AWS_REGION, S3_BUCKET. OPENAI_API_KEY is a DUMMY literal in the workflow env (needed only for
  app.config import; no test makes a real OpenAI call) — do NOT add the real key as a secret.
- **`concurrency: cancel-in-progress`** cancels an in-flight run when a newer push lands →
  prevents CI-vs-CI double-TRUNCATE collision.
- **CI-vs-LOCAL collision: DOCUMENTED, not engineered around.** There is ONE Cloud cluster, so
  if you run tests/demos LOCALLY while a push CI run is live, both reseed the same cluster and
  can collide (the "indexes being dropped" TRUNCATE gotcha) → a flaky run. Don't do both at
  once. Full isolation would need a separate CI database on the cluster (a distinct
  DATABASE_URL secret) — available if flakes ever appear, but beyond lean scope. No test-code
  changes were needed either way.

### Phase 4 (lean) status: 18 tests pass (11 prior + 6 validation/rate-limit + 1 SSE).

## Pre-frontend increment (2026-07-03) — read surface + 2 post-audit integrity fixes

Tightly-scoped increment before the frontend build. Addresses AUDIT.md §8 (missing read routes)
and two AUDIT.md Part A integrity items. Explicitly NOT any of the Part C proposals (C1–C4
deferred). Suite is now 25 tests (18 prior + 3 read-endpoint + 3 certificate + 1 SSE
single-flight); all 7 new tests pass, and every prior test exercising the changed code
(atomic-invalidation ×4 with the new pre_state assertion, consistency ×3) passes.
- **Full-suite flake — cause NOT captured, do not assume.** One 10-min serial full run came
  back 24/25 with `test_arbitrary_nonfleet_actor_is_accepted` failing. The failing traceback
  was lost (a `tail -20` on the run truncated it), so there is NO captured status/body for it.
  What was subsequently established:
    * It does NOT reproduce: passes alone; passed in a partial re-run (12/25 green, killed before
      reaching it); passed in a targeted tail rerun `test_time_travel + test_validation` (7/7).
    * The rate limiter is RULED OUT by construction (not by counting): `app/ratelimit.py` is a
      persistent session-global bucket store with no conftest reset fixture, BUT the window
      self-resets (`now - start >= window_seconds`) and this test makes exactly ONE HTTP call to
      the `/beliefs/:id/invalidate` bucket, which sees ~3 lifetime hits across the whole suite,
      each >60s apart — a 429 is unreachable. (Latent note: the global-with-no-reset IS real
      test-coupling; harmless today because no route is hit ≥60x within a window, but a future
      test that does would need an autouse limiter-reset fixture.)
    * Not a regression from this stream: the change touches neither seeding nor the accept path.
  Leading (UNCONFIRMED) candidate by elimination is shared-cluster reseed/AOST timing, consistent
  with the documented TRUNCATE-job-lag gotcha (Phase 3, Step 7) — but this was NOT confirmed with
  a captured traceback and must not be written down as the cause until it is. If it recurs,
  capture full `--tb=long` output (never pipe the run through `tail`) before concluding.

### Read surface for the frontend (app/services/catalog.py + routers)
- **GET /agents** — full genealogy (id, generation, bloodline, status, spawned_at, retired_at,
  parent_id). NO pagination: the tree needs every node and the genealogy is bounded-small by the
  data model. Optional `?bloodline=` / `?status=` filters. Envelope `{agents, count}`.
- **GET /decisions** — the decision feed. `?agent_id` is OPTIONAL (design correction from the
  approver): default is the FLEET-WIDE feed across all agents (newest first) — what the decision
  panel renders; passing agent_id narrows to one agent's history for the investigate flow.
  PAGINATED (decisions grows to thousands of rows): `?limit` (default 50, max 200, `Query(le=200)`
  → guarded 422, never 500) + `?offset`, returns `{decisions, total, limit, offset, agent_id}`.
- **GET /beliefs** — belief catalog, full list (bounded-small), optional `?status=`. Envelope
  `{beliefs, count}`. Added `invalidated_at` (optional) to BeliefOut so the frontend can flag
  invalidated beliefs; deposition/lineage SELECTs that don't fetch it default to null.
- All three are current-state reads (NO AOST — that stays the deposition path), raw SQL on
  `engine.connect()`, filters always bind params (no string interpolation). New reusable
  `app/services/catalog.py` (list_agents / list_decisions / list_beliefs), not diagnostic scripts.
- **Seed gotcha for tests:** `seed.seed()` leaves `decisions` EMPTY (Phase-1 genealogy only), so
  the /decisions test inserts a controlled set via `insert(Decision)` before asserting. Also both
  spine heads live (crimson-7 AND azure-7) + branch leaf crimson-5b = 3 alive agents; only the
  crimson belief is seeded.

### Post-audit integrity fixes
- **Self-contained certificate (closes the undocumented 75-min gc.ttlseconds exposure — AUDIT §6
  / Part B #3).** The cert body now carries `pre_invalidation_state`: the MEASURED "belief active,
  whole closure open" fact, captured INSIDE the invalidation txn BEFORE the flip (endpoint path,
  source='issue-time-read') or from the certifier Lambda's independent AOST replay
  (source='aost-replay'). It is hash-covered, so the document proves the pre-kill world on its own.
  AS OF SYSTEM TIME replay drops from SOLE integrity mechanism to a BONUS freshness cross-check —
  it no longer silently loses its guarantee once the MVCC snapshot ages past the GC TTL. Mechanics:
  invalidation.invalidate_belief captures pre-kill edge total/open in-txn and returns `pre_state`;
  certificate.build_certificate embeds it (falls back to deriving from affected counts if a caller
  omits it, source='derived', so the field is ALWAYS present). Hermetic test_certificate.py (pure
  build_certificate, ZERO cluster/S3) proves present + hash-covered + tamper-evident; the live S3
  round-trip test asserts it survives GET + hash re-verify.
- **SSE single-flight guard (closes the _stream_lock TOCTOU — AUDIT §2).** Old guard was
  `if _stream_lock.locked(): busy` THEN `async with _stream_lock` — two near-simultaneous requests
  could both see the lock free, fall through, and the second would BLOCK on acquire then run a
  surprise second reseed. Replaced with a module-level bool `_stream_active` test-and-set: a
  synchronous read-then-write with NO await between them → atomic on the single-threaded event loop,
  so a concurrent second request ALWAYS gets a clean `busy` and never queues a reseed. Outer
  try/finally releases the flag on completion, run_seed error, or client disconnect. The bool
  subsumes the old lock's reseed-serialization (only one stream runs). Hermetic test parks the first
  stream (patched run_seed on a gate) and probes with a second, asserting busy + seed called once.

## Frontend Phase 2 — the console shell (2026-07-04)

NOTE: "Frontend Phase 2" is the FRONTEND phase ladder in FRONTEND.md (scaffold → shell →
interactions → …), NOT the backend's Phase 2 (agents/OpenAI) above. Different ladders; do
not conflate the numbers. Backend is frozen this phase except nothing — no backend changes.

### Scope
- The three-region console shell (decision feed / genealogy tree / inspector), populated with
  REAL data from GET /agents, /decisions, /beliefs. Purely presentational: NO interactions
  (investigate/trace/time-travel/invalidate are Frontend Phase 3), no motion, no color warmth
  (warmth stays reserved for the Phase-3 trace so it lands hard). Tokens reused from tokens.css,
  not re-derived. No new frontend dependencies (framer-motion/lenis/r3f are later phases).
- Built in 3 stages, committed each: A shell+data layer, B the SVG tree (checkpoint), C feed+
  inspector.

### Stage A — shell + data layer (committed)
- `src/hooks/useConsoleData.ts` fires the 3 reads in PARALLEL as independent Loadable slots
  (loading|error|ready) so one slow/failed source degrades locally instead of blanking the shell.
- `src/App.tsx` = CSS-grid shell: header (fleet summary "N alive / N agents") + 3 regions
  `[feed | tree | inspector]`, `height:100vh; overflow:hidden`, each panel scrolls its own body.
  `src/components/Panel.tsx` = shared chrome (`Panel` + `Loaded<T>` render-prop for the states).
- Replaced the Phase-1 throwaway proof in App.tsx/App.css.

### Stage B — genealogy SVG tree (committed; CHECKPOINT stage)
- `src/lib/treeLayout.ts` = pure, React-free layout. gen→x (col), bloodline→band, main line on
  lane 0 with offshoots below. **Lane rule is data-driven, no name parsing:** the "main child" =
  child with the MOST descendants keeps the parent's lane (so the spine reaching gen 7 stays on
  lane 0); other children drop to the first free lane (tracked by (lane,gen) occupancy so
  offshoots at distinct gens share lane 1). Bands ordered most-alive-first (crimson on top: 2
  living holders vs azure's 1). Edges = every real parent→child link; branch = lane-changing edge.
- **The API has NO agent-name field** (AgentGenealogy = id/generation/bloodline/status/parent_id).
  So a node's identity is its real generation digit (in the circle) + a real 6-char UUID fragment
  below. We do NOT print the seed's "crimson-5b" — that string isn't in any API response, and
  FRONTEND.md forbids fabricated labels.
- `GenealogyTree.tsx` + `.css`: SVG scales to the region via `viewBox`+`preserveAspectRatio
  =xMidYMid meet` → fits without scrolling at 1280/1440/1920. Dead = hollow --ash circle; alive =
  --alive ring + halo + brightened gen/id. Bloodlines distinguished by band+label ONLY (no color —
  keeps the world cold). Branch edges dashed, spine edges solid. HTML legend overlay (alive/dead/
  branch). Gen axis 0..7 at the bottom.

### Stage B self-critique vs FRONTEND.md (screenshots at 1280/1440/1920, headless chromium)
- PASS: forensic/clinical/cold (mono ids on a cold grid, banded records); warmth-free (only
  --alive on the 3 living nodes); static SVG; dead-vs-alive strongly distinct; generational and
  no-scroll at all three widths; topology correct (crimson spine 0-7 + offshoots @2,3,5,6 with the
  living branch at gen5; azure mirror, all azure offshoots dead).
- **crimson-5b (the 2nd living belief-holder) reads clearly in the secondary lane** — user's flagged
  concern. It gets the SAME alive emphasis (ring+halo+bright label) as the gen-7 spine tips, so it
  never reads as a minor branch. Verified in all 3 shots.
- Minor/accepted: (1) vertical whitespace — the wide-short tree centers in a tall column, leaving
  calm negative space; kept centered (reads more balanced than top-anchored). More prominent at
  1920. (2) branch edges are faint cold dashed; the edge to the living crimson-5b is subtle by
  design — Phase-3 trace warmth is the right place to emphasize it, not Phase 2. (3) fonts are the
  token FALLBACK stack (Space Grotesk/Inter/JetBrains Mono not yet self-hosted) — FRONTEND.md
  defers real web fonts to a later phase.

### Cluster/tooling notes
- **/decisions is EMPTY on the cluster right now** (agents=24, beliefs=1, decisions=0). seed.seed()
  leaves decisions empty; the Phase-2/3 backfill wasn't re-run. The feed renders an honest "no
  decisions" empty state. Rerun `python -m seed.backfill_decisions` before evaluating Stage C.
- **Vite dev server binds to `localhost` (IPv6 ::1), NOT 127.0.0.1** — curl/playwright must use
  `http://localhost:5173`. Backend CORS allowlist is exactly localhost:5173 + 127.0.0.1:5173
  (app/main.py), so the dev server MUST be on 5173 or fetches CORS-fail.
- Screenshots: no repo dep added. Playwright installed in the scratchpad (`--no-save`); full
  chromium via `chromium.launch({ channel: 'chromium' })` (the default headless path wanted the
  chrome-headless-shell build which wasn't present). Script: scratchpad/shot.mjs.

### Stage C — decision feed + inspector (committed; COMPLETES Frontend Phase 2)
- **Backfill had to be re-run first** — the cluster's `decisions` was empty again at Stage-C start
  (seed.seed() truncates it), exactly as the Stage-B note warned. `python -m seed.backfill_decisions`
  reseeded genealogy (same 24 agents / 1 belief) + inserted 4000 rows in ~228s. Emergent curve
  reproduced byte-for-byte (conf g0..g7 .924 .952 .876 .852 .724 .556 .624 .528; gen-6 recession
  dip intact; fraud% 7.6→47.2). Deterministic — same numbers every run.
- **DecisionFeed** (`components/DecisionFeed.{tsx,css}`): fleet-wide feed, newest-first. Bounded
  live window, NOT a table dump — hook already fetches the backend max page (limit=200, offset 0);
  no offset paging / "load more" (that's a Phase-3 interaction). Header count is honest: `200 / 4000`
  (loaded / cluster total). Because newest-first, all 200 rows are window-7 (the DENSEST fraud gen) —
  so the rendered feed is the worst-case density by construction.
- **TWO fraud rates, different denominators — NOT a conflict (they live near each other, so state it):**
  * **~47% (this file's Phase-2 curve, w7 = 47.2%)** = fraud among the BELIEF-DRIVEN-ONLY subset
    (`driving_belief_id = origin belief`) — the population `belief_performance`/the confidence curve
    aggregates over. The belief only ever approves, so this IS its staleness signal. (The 200-row page
    catches 105 of these and measures ~48.6% — same rate, small-sample wobble.)
  * **~28% (the feed's rendered density)** = the BLENDED rate over ALL window-7 rows on the page
    (belief-driven + off-pattern baseline). The red accent flags every `is_fraud` row regardless of
    driver, so this is the right number for "how much of the visible feed is flagged." Lower because
    the off-pattern baseline (~5% fraud over the ~95 non-belief rows here) dilutes it.
    Arithmetic: 51 belief-driven frauds + 5 baseline frauds = 56 / 200 = 28%.
- **Fraud accent** = the one signal in the feed: `--alert` (fraud is the palette's designated alert
  meaning, distinct from the reserved amber/orange trace warmth — approved). Kept minimal: a 2px left
  rule + a 6px dot, NO "FRAUD" text word (a red word on ~28% of rows would be noise). Verified at real
  worst-case density across 1280/1440/1920 — reads as a scannable peripheral flag, not a wash. Erring
  subtle on purpose (Phase-2 stays quiet so the Phase-3 trace lands).
- **Inspector** (`components/Inspector.{tsx,css}`): fleet stat block (agents/alive/dead/decisions/
  beliefs-active, each read DEFENSIVELY from its own slot — a not-ready slot shows "—", preserving
  per-panel degradation) over the belief catalog (rule_text, status, origin 6-char frag, formed date,
  invalidated flag when set). Origin frag `108cf7` == the crimson gen-0 tree node — real cross-panel
  coherence, not coincidence (the belief's originating_agent IS crimson-0).
- **App wiring**: Inspector now takes all three Loadables (was beliefs-only) so the fleet block can
  count agents+decisions; belief catalog still gated on the beliefs slot via `Loaded`.
- **New** `lib/format.ts`: deterministic formatters (USD amount, thousands count, 2-dp confidence,
  6-char id frag, ISO instant sliced to date/time — NOT parsed through local tz, so the record shows
  exactly what the cluster stored). `tsc -b` clean, oxlint clean, no page errors at any width.
- Belief `ACTIVE` status reuses `--alive` green (active/healthy) — a deliberate reuse of the existing
  cold+green vocabulary, no new color, not warmth.

## Frontend Phase 4 — live consistency demo (2026-07-05)

### Verbatim SSE capture — GET /demo/consistency/stream (curl -sN, live cluster, before any design)
Captured a full run against the running backend so components are built on the REAL wire shape,
not the NOTES prose summary. Confirms exact event names + payload keys. (This run was itself the
destructive reseed→fan-out→invalidation; belief left invalidated, re-backfilled after.)

Wire format = sse-starlette: `event: <name>\ndata: <json>\n\n`, plus `: ping - <ts>` COMMENT
keep-alive lines every 15s (`ping=15`) that a consumer must ignore (they are not events).

Events observed, in order (payloads verbatim):
- `event: start`
  `{"belief_id": "898ad0e5-b4f8-5863-abe3-4145c9b5af68", "strategy": "eventual", "note": "per-holder fan-out to invalidation; watch the SPLIT window open"}`
- `event: sample` × 25 (seq 1..25). Shape:
  `{"seq": 1, "state": "ALL_ACTIVE", "open_edges": 8, "total_edges": 8, "elapsed_ms": 141}`
  - `state` ∈ {`ALL_ACTIVE`, `SPLIT`, `ALL_INVALIDATED`} (consistency.classify).
  - Real drain observed: open_edges 8→8→7→7→7→6…→1→0 across the samples; total_edges constant 8.
  - `elapsed_ms` real observer timing: 141, 360, 610, 860, 1188, 1422, 1875, 2110, 2344, 2610,
    2875, 3141, 3407, 3688, 4063, 4360, 4610, 4891, 5157, 5547, 5766(→ALL_INVALIDATED), …, 6688.
    Multi-second real gaps (per-holder delay 0.5s in the endpoint); do NOT smooth/interpolate.
  - First SPLIT at seq 3 (open 7), first ALL_INVALIDATED at seq 21 (open 0, elapsed 5766ms).
- `event: summary`
  `{"commit_points": 9, "split_samples": 18, "saw_transition": true, "total_samples": 25, "elapsed_ms": 6719}`
  - `commit_points: 9` = 8 edges + 1 belief row (the eventual baseline) — vs the atomic endpoint's 1.
    That 1-vs-9 is the STRUCTURAL fact to state plainly (9 is real from this payload; 1 is the
    documented strong-path fact from consistency.py, NOT in this stream — label its source).
  - `split_samples: 18` = committed, externally-visible torn reads (matches the 18 SPLIT samples).
- `event: busy` (NOT seen this run; sent when `_stream_active` already true):
  `{"detail": "a consistency stream is already running; retry shortly"}` — terminal, stream then ends.

Design implications locked from this capture (not assumed): consume by `event:` name; parse
`data` as JSON; ignore `:`-comment pings; `total_edges` is the denominator for a drain meter;
`open_edges` the numerator; `state` drives the ALL_ACTIVE/SPLIT/ALL_INVALIDATED coloring;
`summary.commit_points` is the only real commit count on the wire (9). Full raw capture saved to
scratchpad/sse_capture.txt during the session.

### Pre-build backend-behavior confirmations (empirical, via scratchpad httpx probes — not assumed)
Two build additions required confirming REAL backend behavior before designing the UI states
(backend is frozen this phase — if either had failed it would have been a flag, not a fix).

- **Mid-stream abort releases the single-flight guard (PASS).** Opened the stream, read until
  genuinely mid-DRAIN (past reseed, ≥3 samples), then CLOSED the TCP connection (httpx `stream`
  context exit == what the frontend's `fetch` AbortController does). Waited 3s; opened a fresh
  stream → it got a clean `event: start`, NOT `busy`. So sse-starlette detects the client
  disconnect, throws GeneratorExit, and `_consistency_events`' outer `finally` sets
  `_stream_active=False` even when aborted mid-drain (previously only exercised on clean
  completion). CONSEQUENCE: the "click Run, watch partway, hit Stop" workflow is safe — Stop won't
  wedge the backend `busy`. (Must still re-verify the SAME through the real frontend Stop button +
  component unmount during the build, per the approval.)
- **An already-invalidated belief does NOT break the stream (PASS — self-heals).** Ran a stream to
  full completion (belief → invalidated, summary `commit_points:9 split_samples:20 total 28`), then
  immediately ran another. Stream 2's seq-1 was `ALL_ACTIVE / open_edges 8 / total 8` — proving the
  endpoint's `run_seed()` (TRUNCATE CASCADE + reseed belief `status='active'` + 8 open edges) resets
  a dead belief back to active BEFORE the fan-out. So there is NO 409 and NO "corrected animation
  over nothing"; the demo always drains a real active 8/8 closure. The frontend needs no separate
  already-invalidated state for the stream path — but WILL still show honest `busy` / error / no-
  `start`-timeout terminals.
- **Reseed latency is VARIABLE (2s clean → ~30s under backlog).** A single clean run reseeds in
  ~2–3s (first capture). Rapid abort-during-reseed + immediate retry queues CRDB schema-change jobs
  (the TRUNCATE "indexes being dropped" gotcha, Phase 3 Step 7) and inflated reseed to ~30s during
  probing. DESIGN CONSEQUENCE: between Confirm and the first `start`, show an explicit "reseeding
  cluster…" waiting state with a generous timeout; never a blank hang. Aborting during the DRAIN
  (after `start`) is collision-safe; aborting during the RESEED then instantly re-running is what
  provokes the backlog — not a normal single-operator path.

### Build + verification (2026-07-05) — Frontend Phase 4 DONE
Standalone FLEET-LEVEL view (takes over the console body via a header toggle: `view` flag in
App, no router, no new dep) — NOT bolted onto the Inspector, because the demo is fleet-scoped,
not per-decision/per-agent like the four supervisor interactions (coupling it to row-selection
state + cramming a drain timeline into the 24rem column both rejected). No new dependencies;
framer-motion NOT used here (motion is opacity-only CSS, reduced-motion-guarded — the demo stays
quiet so Trace/Time-travel remain the two loud moments).
- **lib/consistencyStream.ts** — fetch + ReadableStream reader (NOT EventSource; see the client.ts
  note). Runs once, never reconnects; `summary`/`busy` terminal→abort; 20s silence watchdog reset
  by any chunk incl. 15s pings; `stop()` for Stop button + unmount. openConsistencyStream deleted.
- **components/ConsistencyDemo** — idle → arm/confirm gate (states the destructive blast radius:
  truncate+reseed, belief left invalidated, decisions/perf reset, re-backfill needed) → reseeding
  (explicit waiting state) → streaming → done | busy | error | stopped. Drain meter: closed edge =
  --alive (corrected), open-while-torn = --alert (laggard still live on the dead belief), open-at-
  rest = --ash; SPLIT banner --alert, ALL_INVALIDATED --alive. Samples append with real elapsed_ms
  (no smoothing). Stopped state states the honest consequence: the closure is left PARTIALLY
  invalidated (a real torn state, not a clean rollback) until the next run reseeds.
- **The 1-vs-9 copy (honesty-critical, reviewed):** 9 and split_samples are labeled "measured this
  run" (off summary); the atomic "1 commit / 0 split reads" is labeled "a property of the atomic
  transaction design, not a number off this stream" and cites POST /beliefs/{id}/invalidate's
  single serializable txn + Phase 3's strong-path test. Never presented as a live measurement.
- **Abort lifecycle re-verified through the REAL frontend (the mandated check):** Playwright drove
  a run mid-drain, then (A) clicked Stop and (B) left the view (ConsistencyDemo unmount). After
  BOTH, a fresh run was ACCEPTED on the first attempt — NOT busy. So the client abort (fetch
  AbortController → TCP close) releases the backend `_stream_active` guard in practice, not just in
  the httpx probe. Stop shows the honest partial-state note. (Race caveat: the guard needs ~1-3s to
  detect the disconnect; a Run-again faster than that would get an honest `busy` + Retry, which the
  UI handles — not a wedge.)
- **Screenshot-and-critique @1280/1440/1920, motion + reduced-motion, ZERO page errors** in every
  run. Rendered numbers cross-checked against the wire (eventual 9, atomic 1, "N split reads
  observed here" from summary.split_samples). tsc -b + oxlint clean. Scripts: scratchpad/
  shot_static, shot_run (--reduced), abort_test, shot_stopped; probes probe_stream/probe_reseed.
- **Cluster left testable:** each run wipes the cluster + leaves the belief invalidated, so the
  session ran the destructive stream many times; a final `python -m seed.backfill_decisions` (via
  .venv) restores belief=active + 4000 decisions + 8 perf windows for the rest of the console. Do
  NOT leave a stale Consistency-demo tab open on a completed run — the fetch reader never auto-
  reconnects, but an explicit Run still has the full reseed blast radius.
- **Phase 5 (react-three-fiber) stays gated** — untouched, not a consideration this phase.

## Frontend Phase 3 — Investigate (2026-07-05)

First of the four supervisor interactions. Selecting a decision in the feed TAKES OVER the Inspector
(approved: one evolving surface, not an accreting stack, so Trace/Time-travel/Invalidate reuse the
same space) to show the belief that drove it, tagged inherited / formed-here. ✕ returns to the
default fleet+catalog. Still cold + motionless — warmth/motion stay reserved for the Trace step.

- **NO new fetch, NO new endpoint (pure frontend).** Both facts resolve from data useConsoleData
  already loaded: the driving belief is looked up in the loaded `/beliefs` catalog by
  `driving_belief_id` (full, unfiltered, bounded list — always resolves; there is no GET
  /beliefs/{id}, and calling `/beliefs/{id}/lineage` here would be starting Trace early); the
  inherited flag is `decision.agent_id !== belief.originating_agent_id`. That comparison is
  DEFINITIONALLY exact — an agent either formed a belief (originating==agent) or holds it via a
  `belief_inheritance` edge (!=); no third way. belief_inheritance is the ground truth Trace walks;
  here the comparison yields the same verdict without the traversal. `lib/investigation.ts` is the
  pure resolver (degrades per-slot; beliefState none|pending|missing|resolved).
- **Cross-panel coherence is real, not staged:** the inherited shot's `origin 108cf7` == the crimson
  gen-0 tree node; deciding `3fb55c` == the gen-7 node. The generations shown ("formed by gen 0 ·
  acted on by gen 7 / 7 generations downstream") are looked up in the loaded `/agents` genealogy.
- **Three branches, each on REAL cluster data (screenshots @1440, zero page errors):** inherited
  (crimson-7 on the active belief), formed-here (crimson-0 == originating agent → "FORMED HERE"),
  not-belief-driven (off-pattern row, driving_belief_id null → honest "cited no belief", no badge).
  Cold styling throughout: inherited badge = `--bone` left bar, formed-here = `--ash`; fraud reuses
  `--alert`, active belief reuses `--alive`. NO amber/orange anywhere.
- **Feed rows are now real `<button>`s** (keyboard-reachable, `aria-pressed`, focus-visible outline).
  Selection = raised `--surface-2` + a `--bone` inset left bar via box-shadow, which sits INSIDE the
  border box so a selected fraud row still shows its red `border-left` rule alongside it. Clicking a
  selected row again clears it (toggle in App).
- **KNOWN, EXPECTED reachability gap (flagged, not a bug):** the "formed-here" branch is real code but
  NOT reachable from the default newest-first feed — that page is all window-7 (crimson-7, a
  descendant → always inherited). crimson-0's own decisions are the oldest rows; reaching them needs
  offset paging (a later interaction) or an agent drill-in. The branch exists so it's correct WHEN
  reached. For the verification screenshot only, the shot script pointed the feed at crimson-0's real
  `/decisions?agent_id=` page via Playwright route-fulfill (all data real; no app hack shipped).
- `tsc -b` clean, oxlint clean. Backfill was re-run first (cluster `decisions` was empty + the belief
  left invalidated by a prior demo; `python -m seed.backfill_decisions` via `.venv` — NOT the Roaming
  Python, which lacks the cockroachdb dialect — reseeded belief=active + 4000 rows, emergent curve
  reproduced). One mid-insert connection drop on the first attempt (CRDB Cloud closed the conn);
  per-window commits meant a clean re-run fixed it. Do NOT run the backfill from the global Python.

### Trace (2026-07-05) — the signature animation

Second interaction. From an investigated belief, a "Trace lineage" button walks the belief backward
through the family tree to its origin ancestor — warmth spreading edge by edge through the cold dead
tree, igniting the origin. **framer-motion 12.42.2 installed** (first phase needing it).

- **Single chain, not the closure (approved).** `GET /beliefs/{id}/lineage` returns the belief's whole
  inheritance closure (a FORK — at depth 5 two holders: the living branch cd75b330 + the spine
  d3e2c4d5). Trace animates only the single ancestral chain from the ONE investigated agent back to
  origin, via `lib/trace.ts` `deriveChain()` walking real `from_agent_id` pointers up that closure.
  Every hop is a real belief_inheritance edge; no order guessed. The fork/closure reveal is RESERVED
  for Invalidate (two living holders correcting atomically lands harder there).
- **Inheritance edges == genealogy edges** (minted parent->child at spawn), so each chain hop maps
  exactly onto an existing tree edge by key `${parent}->${child}`. GenealogyTree.computeTraceGeo maps
  the chain onto its own layout geometry; `reversedEdgePath` flips each edge to draw child->parent (the
  backward direction). Straight spine edges AND the curved offshoot bezier are both reversed correctly
  — verified by tracing a crimson-5b decision (chain curves gen4->5b; conclusion "5 generations ago").
- **Warmth is an additive overlay** (`TraceOverlay.tsx`, `<g className="tree__trace">`) layered above
  the untouched cold tree — reset is trivial, base tree stays cold. NO JS color interpolation: each
  `--trace` edge is DRAWN OVER its cold counterpart via `pathLength 0->1` (opacity/transform/pathLength
  only, per CLAUDE.md's transforms/opacity guidance), so it visibly turns warm as it draws. Nodes tint
  `--ash`->`--trace` on `opacity`; origin ignites to `--origin` + a `filter:blur` glow ramp + one scale
  pulse [1,1.12,1]. Sequence: STAGGER 150ms/hop, edge 160ms, ignite 500ms -> ~1.55s for the 7-hop spine.
- **Architecture:** App.tsx owns `TraceState` (idle|loading|error|empty|ready{phase}) because three
  regions coordinate — trigger in the Inspector, animation in the tree, conclusion back in the
  Inspector. App fetches the lineage, derives the chain, passes `{chain, playToken, onComplete}` to the
  tree (which owns PLAYBACK) and a projected `InvestigationTrace` + handlers to the Inspector. The tree
  reports origin-ignition via `onAnimationComplete`->`onTraceComplete`->App flips `phase:"done"`->the
  Inspector reveals the conclusion ("Belief 898ad0 originated with agent 108cf7 — 7 generations ago",
  real formed_at, warm accents = the only warmth in the Inspector). Changing the selected decision
  resets trace to idle (effect on selectedId) so a stale warm path never lingers.
- **Re-trigger + reset:** the overlay `<g>` is keyed by `playToken`; Replay increments it -> remounts
  from the cold `initial` state -> that remount IS the snap-back-to-cold-before-replay. Verified: replay
  mid-frame shows cold reset + fresh spread.
- **prefers-reduced-motion** (`useReducedMotion`): all delays/durations -> 0, so the sequence collapses
  to its FINAL state instantly (full warm spine, origin ignited, conclusion shown) — no crawl; fires
  onComplete on mount. Verified via a `reducedMotion:"reduce"` Playwright context.
- **Empty-chain fallback** (defensive, per approver): if `deriveChain` returns [] (agent not a holder),
  App->status "empty"->Inspector shows "No inheritance chain resolved for this agent — nothing to
  trace." rather than crashing (same discipline as Investigate's "not belief-driven").
- **Verification:** frame-sampled the animation with Playwright at real elapsed times (rAF pauses
  during each screenshot, so animation-time < wall-clock — samples confirm the per-hop backward spread +
  origin ignition, not exact pacing). Main spine, branch hop, reduced-motion, and Replay all captured,
  zero page errors, `tsc -b` + oxlint clean. Timing constants (STAGGER/EDGE_DUR/IGNITE_DUR in
  TraceOverlay.tsx) are the tuning knobs if the real-time feel needs adjustment after live viewing.

### Time-travel (2026-07-05) — the two-signal staleness reveal

Third interaction. A "Time-travel ⟲" control in the Investigation surface (sibling to Trace, only for a
resolved belief) opens a panel fusing the project's TWO real time signals — kept on SEPARATE clocks, the
NOTES §"Time concepts" discipline. The thesis it makes visible (user's verbatim framing, worth keeping):
*"the belief is the same immutable row — MVCC proves it never changed — yet it rotted, because staleness
is measured performance against a drifting world, not a mutated field."*

- **New backend read endpoint `GET /beliefs/{id}/performance`** (committed separately, backend piece):
  `catalog.list_belief_performance` returns the ordered belief_performance windows; None→404 unknown
  belief, `[]`→200 for a known-but-unmeasured belief (not-measured != not-found). DTOs
  BeliefPerformanceWindow/Response (the 5 real columns; NO synthetic generation field — ordinal position
  IS generation). No migration.
- **Closed a real AUDIT gap (Option A, approved):** belief_performance was populated ONLY by
  test_staleness — a plain backfill left it empty, so the Phase-3 cert's staleness_evidence AND this read
  saw zero rows on a fresh cluster. `seed/backfill_decisions.py` now calls recompute_belief_performance at
  the end (same aggregation the report prints), so every reseed leaves the table consistent with
  decisions. One source of truth. NOTE left to generalize the recompute over all beliefs if a second is
  ever seeded. Verified live after backfill: 8 windows, conf 0.924 (when formed) → 0.528 (present day),
  frauds_approved 19→118, gen-6 recession dip intact. test_belief_performance (404 / 200-empty / measured
  curve through the real HTTP surface) passes.
- **Signal 1 — real MVCC deposition** (`TimeTravel.tsx`): TWO genuine `?as_of=` calls — a within-GC past
  instant (`Date.now()-20s`, the deepest AOST can honestly reach) + present. Both return the belief
  `held · ACTIVE` → proves time-travel is real AND the row is immutable. AOST is GC-bounded (~75 min), so
  it CANNOT reach the formation date; the panel states this explicitly rather than mislabelling the read
  as "when formed" (that trap is why the two clocks stay separate).
- **Signal 2 — real measured curve**: `getBeliefPerformance` drives a WHEN FORMED / PRESENT DAY toggle
  (first vs last window) — big confidence number `--alive` 0.92 healthy → `--alert` 0.53 stale, real
  window dates, frauds_approved. An SVG sparkline draws the FULL 8-window curve with an `--alive`→`--alert`
  gradient (fixed [0,1] domain so the decay is true-scale, never auto-fit-exaggerated); the active endpoint
  dot is ringed. This is the healthy→stale shift, derived not asserted.
- **Placement:** inside the Investigation take-over (the "one evolving surface"), after the Trace block.
  Self-contained state (closed→loading→ready/error) owned by the component — unlike Trace it needs no
  cross-region coordination, so it does NOT lift state to App. Resets to closed when the investigated
  belief/agent changes (effect on beliefId/agentId) so a stale curve never lingers.
- **Colors:** `--alive` (healthy) / `--alert` (stale) ONLY. NO amber/orange — `--trace`/`--origin` stay
  reserved for Trace. Motion is opacity/pathLength only (CLAUDE.md); `useReducedMotion` → curve draws
  instantly, readout appears without slide.
- **Verification (Playwright @1440, live cluster):** zero page errors in BOTH motion + reduced-motion.
  Rendered data checks: present-day conf 0.53 (`--alert`), when-formed 0.92 (`--alive`), derivation
  "0.92 → 0.53 across 8 measured windows", both deposition rows `held · ACTIVE` at a real AOST timestamp +
  present. Reduced-motion collapses to the identical final state. `tsc -b` + oxlint clean.

### Invalidate (2026-07-05) — the one governed write, and the closure reveal

Fourth and final supervisor interaction. From a resolved belief, a confirmation-gated "Invalidate
belief fleet-wide" corrects the belief AND its whole inherited closure at ONE commit. This is the
step deliberately reserved (since the Trace plan) to reveal the FORK: the belief has TWO living
holders — the spine tip crimson-7 (`3fb55c`) AND the branch crimson-5b (`cd75b3`), which Trace's
single chain never lit — and both correct together atomically.

- **Backend contract (verified against code, not memory) + the one additive change.** POST
  /beliefs/{id}/invalidate takes `{actor_id}` (non-nil uuid → 422 on nil; NOT checked against
  `agents` — the human supervisor is not a fleet agent). 404 unknown / 409 already-invalidated.
  Response `InvalidateResponse` carried the certificate OUTCOME (certificate_id, content_hash,
  certificate_s3_key, certificate_status, db_snapshot_hlc, affected/living counts, audit_id) but
  NOT the cert body — and there is no GET route for the cert. **Approved change: serialize
  `pre_invalidation_state` onto the response** (the self-contained "belief active, closure fully
  open" record). It is the SAME `inv["pre_state"]` dict `build_certificate` already embeds and
  hash-covers — one source of truth, no re-derivation, no new query, no migration. Rationale
  (approver): a client-side reconstruction would be a second computation path that can silently
  disagree — the same risk rejected when choosing the /performance data source. **Round-trip test**
  (tests/test_atomic_invalidation.py) now asserts `response.pre_invalidation_state ==` the value
  fetched from S3 and hash-verified — proven identical, not look-alike. 4 atomic-invalidation tests
  pass (~136s, live cluster + real S3). Deliberately NOT duplicated into the response:
  living-holder IDENTITIES (come from the lineage closure) and staleness_evidence (the /performance
  endpoint) — each stays sourced where it already lives.
- **Frontend architecture mirrors Trace.** App owns `InvalidateState`
  (idle→arming→confirming→invalidating→done|error) because the confirm gate is in the Inspector
  while the closure-reveal + atomic-correction animation is in the genealogy tree (two regions
  coordinate). Arming fetches `GET /beliefs/{id}/lineage`; `deriveClosure` = alive nodes
  (livingHolders) + non-origin nodes (edgeCount). Confirm → real POST; 409 → honest "already
  invalidated" branch. Supervisor actor = a fixed non-nil constant `lib/supervisor.ts` (non-fleet,
  per the backend design).
- **Tree `InvalidateOverlay`** (additive `<g>`, like TraceOverlay): "armed" pulses both holders
  `--alert`; "corrected" flips both to `--alive` in ONE shared transition (no per-node stagger —
  the simultaneity IS the single-commit message). Azure's gen-7 stays green (different bloodline,
  not a holder) — the overlay marks only real closure holders. transforms/opacity only;
  prefers-reduced-motion collapses each phase to its static final state.
- **Cert outcome shown honestly** (`Invalidate.tsx`, not a generic toast): the sealed pre-kill
  state (`belief active · closure 8/8 open · 2 living holders · issue-time-read`), certificate
  status, full sha256, S3 key, snapshot HLC, audit id. `certificate_status:"failed"` is surfaced as
  "invalidation durable, S3 write retriable". Belief card reflects the just-done invalidation
  (status→invalidated from the authoritative response) so the header never contradicts the outcome.
- **Colors: `--alert` (consequential action) + `--alive` (correction) ONLY.** No amber/orange —
  `--trace`/`--origin` stay Trace's. Motion is the tree's; the Inspector outcome just fades in.
- **Cluster state finding (flagged before building).** The belief was ACTIVE (no reseed needed for
  the write) BUT `decisions`/`belief_performance` were EMPTY (Phase-1 genealogy only) — the feed was
  empty and the cert staleness would be unavailable. So a backfill was required regardless. Each real
  invalidation consumes the active belief, so this stream ran `python -m seed.backfill_decisions`
  (via `.venv`, ~228s each) THREE times: before the motion run, before the reduced-motion run, and a
  final handoff reseed leaving the belief ACTIVE + 4000 decisions + 8 perf windows for the user to
  test the whole flow.
- **Verification (Playwright @1440, live 5173→8000 stack).** Motion + reduced-motion both:
  2 holders armed → 2 corrected, `certificate_status=written`, real pre-state
  `belief active · closure 8/8 open · 2 living holders (issue-time-read)`, real sha256, ZERO page
  errors. Reduced-motion collapses straight to the final corrected state. `tsc -b` + oxlint clean.
- **Env gotcha:** stale uvicorn (8000) + vite (5173/5174) from prior sessions were already up; a new
  vite landed on 5175 (CORS only allows 5173). Confirmed the running 8000 server already served the
  new `pre_invalidation_state` (its OpenAPI carried it — a --reload instance had picked up the edit),
  so the existing 5173→8000 stack was current and used directly. Redundant self-started 8001/5175
  were killed. Do NOT run two backfills at once (the documented TRUNCATE-CASCADE collision).

## Frontend Phase 5 — 3D consistency scene (react-three-fiber) (2026-07-05)

The ONE sanctioned r3f use in the whole project (FRONTEND.md Phase 5): a 3D rendering of the SAME
real `GET /demo/consistency/stream` samples the Phase-4 2D view consumes. Additive only — the 2D
view is the shipped fallback and was not weakened. Genealogy tree stays SVG; no r3f anywhere else.

### The atomic-observability question — resolved by reading code, then the minimal real backend add
- **Finding (cited):** NO route streamed a live ATOMIC invalidation before this phase. `observe_closure`
  (app/services/consistency.py) is strategy-BLIND — it just samples the closure and classifies each
  read. The Phase-4 SSE endpoint (app/routers/demo.py) HARD-CODED `strategy:"eventual"` + kicked off
  `eventual_invalidate(...)`. The "strong = 1 commit / 0 split" fact existed ONLY inside
  `tests/test_consistency_window.py::test_strong_invalidation_has_no_split_window`, which runs the REAL
  `invalidate_belief` (the production endpoint fn) concurrently with the SAME observer. So the observer
  machinery already streamed; only the mutation task was eventual-locked.
- **Minimal real backend addition (the "where explicitly noted" change):** added `?strategy=eventual|strong`
  (`Literal`, default `eventual`) to the SSE route. `strong` swaps the fanout task to the REAL
  `invalidate_belief(ORIGIN, ACTOR)`; everything else (observer, queue, sample/summary plumbing,
  single-flight guard, reseed) is reused verbatim. The no-arg call is byte-for-byte the Phase-4 path,
  so the 2D view is untouched. FastAPI 422s a bad `strategy` BEFORE any reseed. NOT a fabrication:
  `split_samples:0` is MEASURED by the observer against the real atomic commit; only `commit_points:1`
  is structural (symmetric with eventual's computed `len(edges)+1`).
- **Observer interval:** `0.02` for strong (vs `0.1` eventual). Empirically the ~180ms closure-read
  round-trip to Frankfurt is the real sampling floor, so `0.02` just means "as fast as the cluster
  answers" — tightening further would not help, and can't expose a split (snapshot isolation makes 0
  structural). `saw_transition` is guaranteed regardless of interval: the ready-gate's first sample is
  pre-flip ALL_ACTIVE and the observer's post-stop final sample is ALL_INVALIDATED.

### Live strong capture — reproduced 5× (scratchpad/sse_strong_run{1..5}.txt)
- Every run: `split_samples:0`, `saw_transition:true`, `commit_points:1`. open_edges jumps 8→0 in ONE
  sample (no SPLIT ever). 5–6 ALL_ACTIVE samples then 1–3 ALL_INVALIDATED. The pre-flip active window
  is REAL: it's the `invalidate_belief` Cloud round-trip latency (snapshot-HLC read + write txn), not an
  artificial sleep. Consistent — this is the honest live atomic measurement, not a replay of old numbers.

### Strong mid-run abort — verified transport AND real UI (short window is where races hide)
- Transport probe (scratchpad/probe_strong_abort.py): abort mid ALL_ACTIVE window → fresh strong stream
  gets `start`, not `busy`; strong→eventual alternation clean. Guard releases.
- **Real frontend (scratchpad/abort_strong_ui.mjs, Playwright):** (A) Stop button mid-run → Stopped state
  → fresh strong run ACCEPTED (not busy). (B) leave the view mid-run (ConsistencyDemo UNMOUNT) → return →
  fresh strong run ACCEPTED. Both PASS, zero page errors. So the client abort (fetch AbortController → TCP
  close) releases the backend `_stream_active` in practice for the strong path too, not just eventual.
  NOTE: unmount resets ConsistencyDemo's local state (strategy → eventual default, render → 2d) — expected;
  a returning operator re-selects Strong.

### Dependencies (exact, no kitchen sink)
- `@react-three/fiber@^9.6.1` (v9 = the React-19 line; peer `react >=19 <19.3`, our 19.2.7 satisfies it),
  `three@^0.185.1`, `@types/three@^0.185.0` (dev). **NO drei** — its helpers (OrbitControls/loaders/HDRI)
  are exactly what scope + reduced-motion forbid; the scene is a static camera + hand-written meshes/lights.
- **Bundle cost:** three pushes the prod bundle to ~1.25 MB (348 KB gzip) with a Vite >500 KB chunk warning.
  Acceptable for one gated demo feature; not code-split (single-page demo console). Noted, not fixed.

### The 3D scene (src/components/ConsistencyScene3D.tsx)
- Renders the SAME derived state as the 2D DrainMeter — `total_edges` holder nodes on a deterministic
  fibonacci shell (no Math.random → stable screenshots) around a central --ghost wireframe belief core,
  thin --line edges. Counts only, never labelled as specific agents (same honesty note as the 2D meter).
- Kind per node = 2D semantics exactly: closed → --alive (corrected); open+SPLIT → --alert (laggard still
  live on the dead belief); open+rest → --ash. EVENTUAL drains one-by-one (real elapsed_ms, visible torn
  red+green SPLIT frame); STRONG flips all nodes to --alive together (open 8→0 in one sample, never red).
- Motion = per-node emissive/color `useFrame` lerp + a one-shot scale pulse on flip (transforms/opacity per
  CLAUDE.md). **reduced-motion SNAPS** each node to its current-sample state (no lerp, no pulse); camera is
  STATIC in BOTH modes (no orbit/flythrough ever). Verified the torn state renders statically under reduce.
- Tokens only: --alive/--alert/--ash/--void/--line, plus --ghost core. NO amber/orange (Trace's --trace/
  --origin untouched). WebGL renders in headless chromium (channel:chromium) with zero page errors.
- Camera `[0,0.6,7.4]` fov 42, RADIUS 2.05 — pulled back from a first pass that crowded the frame bottom;
  frames all 8 nodes with margin at 1280 (short 15rem preview canvas is the worst case) through 1920.

### Placement + UI (src/components/ConsistencyDemo.tsx — extended, not replaced)
- A 2D/3D render toggle in the view header (default **2d** = Phase-4 look) + a strategy selector
  (Eventual/Strong, default **eventual**) in the idle panel, with an idle PREVIEW of the resting closure so
  the toggle has visible effect before a run. Chose a toggle INSIDE the existing Consistency-demo view over
  a third header mode: same phenomenon, same wire, and it reuses the already-audited destructive lifecycle
  (arm→confirm gate, reseeding/streaming/done/busy/error/stopped, Stop+unmount abort) verbatim — only the
  closure VISUAL swaps. render mode can flip any time, even on a finished run's stored samples.
- **Summary made strategy-aware:** whichever strategy ran is "measured this run" (numbers off `summary`);
  the OTHER is the cited structural contrast ("switch strategy to X to measure it live"). eventual run =
  9 measured / 1 cited (Phase-4 wording preserved); strong run = 1 measured (real endpoint) / 9 cited. Keeps
  the honesty rule symmetric — the atomic "1/0" is a live measurement ONLY when strong actually ran.
- **Heavier strong confirm-gate (per the approval):** distinct copy + thicker double-ruled --alert border.
  "This is the real governed write, not a preview." — states it executes the same atomic fleet-wide
  invalidation as the supervisor Invalidate action, "genuinely invalidated across every holder in one
  irreversible commit; there is no rollback." Button: "Confirm — invalidate fleet-wide for real." Not
  mistakable for a lower-stakes preview.
- lib/consistencyStream.ts gained an optional `strategy` arg (default eventual) → appends `?strategy=`; all
  the Phase-4 lifecycle guarantees (fetch not EventSource, run-once/never-reconnect, 20s stall watchdog,
  stop() on Stop+unmount) are unchanged.

### Verification (Playwright @1280/1440/1920, channel:chromium, live 5173→8000 stack)
- Scripts: scratchpad/shot_idle (non-destructive 2D/3D previews all widths), shot_strong (strong 3D done +
  gate, motion + reduced-motion, summary cross-checks), shot_eventual (3D SPLIT frame caught at 5/8 & 6/8
  open, done, summary; 2D done = Phase-4 untouched; reduced-motion torn frame), abort_strong_ui.
- Rendered numbers cross-checked vs the wire: strong summary = atomic 1 measured / 0 split / saw_transition
  true / eventual 9 cited; eventual summary = 9 measured / 1 cited / N split reads. ZERO page errors in every
  run (motion + reduced-motion). `tsc -b` clean, oxlint clean, `vite build` OK.
- **2D (Phase 4) confirmed untouched:** eventual+2d done renders the identical meter + log + 9-vs-1 summary
  as Phase 4 (only additive change: a truthful "switch to Strong to measure it live" pointer).
- 5 backend regression tests pass (test_sse_stream ×2 + test_consistency_window ×3, 146s) — the additive
  strategy param regressed nothing; both SSE tests call with no strategy → eventual → unchanged.
- **Cluster restored:** the session ran many destructive strong+eventual invalidations, so a final
  `python -m seed.backfill_decisions` (via .venv) restores belief=active + 4000 decisions + 8 perf windows.
- **The payoff:** the eventual SPLIT frame shows 3 --alive + 5 --alert nodes ("5 still live on the invalidated
  belief") vs strong's all-flip-together-no-red — the split state made VISIBLE, directly targeting the audit's
  worst-ranked criticism (atomic-across-regions argued-not-demonstrated).

## Frontend Phase 5.1 — the 3D scene made an interactive instrument (2026-07-06)

Turned the static Phase-5 render into a forensic instrument: orbit/zoom camera, hover→real-data
cross-highlight, click→real holder detail. Additive; 2D untouched.

### Diagnose-first (the mandated step) — the "frozen/dead-hover/immovable" hypothesis was WRONG
- Temp-instrumented the real scene (DOM `onPointerMove` on `.cx3d` + r3f `onPointerOver` on a mesh),
  swept the mouse via Playwright: **DOM pointermove fired 83×, r3f mesh pointerover fired 2×, 0 errors.**
  So there is NO overlay / `pointer-events:none` / stacking bug — events reach the canvas AND meshes
  are raycast-hittable. Root cause: **the interaction layer was never written** (Phase 5 was a
  deliberately static render). Camera immovable = OrbitControls never mounted (true), not an overlay.
  Reverted the temp handlers before building. Lesson banked: don't accept a stated root cause — the
  brief's "r128 / CDN / CapsuleGeometry / decoupled-from-data" premises did NOT match this repo
  (it's three@0.185 + r3f v9 via npm; nodes DO animate; scene renders real counts).

### The identity problem (the real integrity fork, flagged to the approver before building)
- The SSE stream carries COUNTS ONLY (open/total/state) — **no per-holder identity.** So node i was
  count-based, and a naive "hover node → its observer-sample row" is a category error (nodes = holders
  / a spatial axis; sample rows = time-points / a temporal axis). Real identity lives in a DIFFERENT
  endpoint the panel didn't load: `GET /beliefs/{id}/lineage`. Approved to add that fetch (additive,
  already trusted by Trace/Invalidate, does NOT touch the observer-sample pipeline).
- **VERIFIED the load-bearing ordering claim from source (not asserted):** eventual fan-out closes
  `ORDER BY inherited_at` (`app/services/consistency.py:145-146`, loop `:158-163`); strong is one
  set-based `UPDATE ... WHERE belief_id=:b AND invalidated_at IS NULL` with NO order
  (`app/services/invalidation.py:128-129`). So the k-th edge to close IS the k-th holder by
  inherited_at (eventual); strong closes all at one commit. The 8 `inherited_at` values are distinct →
  total, deterministic order. This is why node i can bind to `holders[i]` (lineage sorted by
  inherited_at) without inventing identity. Honest hover mapping: holder i → the observer-sample that
  witnessed its edge closing = first sample with `open_edges <= total-(i+1)` (strong → the single
  commit sample). Item 2 reinterpreted to this, approved.

### Build (all four decisions locked with the approver)
- **Camera:** zero-dep — `three/examples/jsm/controls/OrbitControls.js` wired via `useThree`+`useEffect`
  +`useFrame` (NOT drei; same "one helper ≠ a whole dep" call as the base scene). `enablePan=false`,
  min/max distance clamp, **`enableDamping = !reducedMotion`** (no non-user motion under reduce), no
  autoRotate. tsc/oxlint/build all resolve the jsm import; +~20 KB to the bundle.
- **Identity:** `getBeliefLineage(start.belief_id)` on the SSE `start` (post-reseed → this run's edges);
  `holders[] = path.filter(from_agent_id!=null).sort(by inherited_at)`. node i == holders[i]. Failure
  leaves the scene non-interactive (holders=null), never blocks the demo.
- **Hover:** node `onPointerOver/Out` lifts `hoveredHolder`; node gets emissive+scale boost; the
  observer-samples table row that witnessed that holder's closure gets an `is-witness` --bone bar.
- **Click:** `onClick` selects; a 3D-only detail panel renders REAL lineage fields (agent frag, gen,
  bloodline, alive/dead, inherited_at, parent frag, closure edge state, "closed at sample #N·Xms"),
  cites `GET /beliefs/{id}/lineage`. `onPointerMissed` deselects. Selected node gets a --bone wireframe
  halo. Colors: state tokens + --bone accent only (no amber).
- **2D untouched:** DrainMeter and the 2D path are byte-unchanged; detail/hint/is-witness are all gated
  on `render==="3d"`; hovered/selected cleared when leaving 3D. Parity is data-level: cell i and node i
  are the same edge (same samples), and node i now additionally carries real identity.

### Verification (Playwright @1440, channel:chromium, live 5173→8000)
- **Item 1 (camera):** drag changed the render AND wheel changed the render (identical images would mean
  dead controls) → PASS, 0 errors. Screenshot confirms a real rotation. (scratchpad/verify_orbit.mjs)
- **Item 2 (hover):** hovering a node lit its witnessed row — e.g. hovered holder `82c31a` → row
  **#6 / 1360ms / 6/8 open**, and the node enlarged. Real: `82c31a` is holder index 1 by inherited_at,
  `witnessSeq(1)=`first sample with open≤6 = #6. (verify_interaction.mjs, interact_hover.png)
- **Item 3 (click):** detail showed REAL `82c31a` gen2 crimson dead, inherited from `43c136` @2024-11-08,
  edge invalidated, closed at sample #6·1360ms — cross-checked: `82c31a` ∈ the 8 real lineage frags.
  Not a placeholder. (interact_click_detail.png)
- **Item 4 (parity/2D-untouched):** toggling to 2D on the SAME data → 8 meter cells, 0 detail panels,
  0 3D hints. (interact_2d_parity.png)
- Internal consistency: the hovered witness row (#6) == the detail's "closed at sample" (#6). tsc -b +
  oxlint + vite build clean, ZERO page errors across all runs. Cluster restored via backfill after the
  one destructive eventual verification run.

### Follow-ups (2026-07-06) — idle-hover legibility + a token-discipline fix
- **At-rest hover is inert BY DESIGN (confirmed empirically, not asserted).** Re-instrumented the scene
  (DOM `onPointerMove` + unconditional mesh `onPointerOver` logging the gate): at the idle preview,
  events reach the canvas (117 moves) AND the spheres ARE raycast-hit (`interactive=false`), so hover is
  gated off by the identity gate, not a broken pipeline. `interactive = holders !== null`, and holders is
  fetched only on the SSE `start` — and at rest there are also no observer samples, so there'd be no real
  witnessed-row to light. Chose option A (legibility, not new capability): appended a one-line idle hint
  `drag to orbit · run the proof to inspect holders`. Rejected fetching lineage at mount (option B) — it
  still couldn't deliver the row cross-highlight at rest, so it would have created two different "sort-of-
  works" hover flavors instead of one clean "not active yet."
- **Token fix — node colour now SNAPS, never cross-fades.** The mid-drain torn frame revealed a transient
  TAN/amber node: an RGB lerp from --alert (red) to --alive (green) passes through off-palette warm hues
  for ~0.3s on every close — a real violation of the "no amber/orange (Trace's)" rule. Fixed: `mat.color`
  and `mat.emissive` are set to the token instantly; the flip still reads via the scale pulse + emissive
  brightness ramp (dropped under reduced motion). Also matches the 2D meter, which switches cell colour
  crisply. Re-captured torn frame = clean 2 green / 6 red, zero amber.
- **Full evidence set (scratchpad/verify_runs.mjs, @1440, 0 page errors):** idle hint; eventual torn frame
  (6/8 open, clean red+green); hover→node + witnessed row #14 (3/8 open); click→REAL detail for the LIVING
  branch holder cd75b3 (gen5 crimson ALIVE, inherited 2025-08-15, closed at #14) — status/consistency all
  real; strong all-8-flipped (log 8/8→0/8, no SPLIT). Orbit/zoom already confirmed (verify_orbit.mjs).

## Frontend CI (Tier 1) — .github/workflows/frontend-ci.yml (2026-07-06)

- **What it checks:** on pushes touching `frontend/**` (or its own workflow file), one
  ubuntu job — `npm ci` → `npx tsc -b` (typecheck) → `npm run lint` (oxlint) → `npx vite build`
  (build), Node 24 with npm cache. Exactly the manual checks every frontend phase ends with per
  FRONTEND.md/NOTES discipline; run as distinct steps so a failure points at the exact stage.
- **Why separate from the backend job (ci.yml):** this job is fully OFFLINE — no DATABASE_URL,
  no AWS creds, no CockroachDB, no secrets. It cannot collide with the backend CI's live-cluster
  reseeds (the TRUNCATE ... CASCADE gotcha, Phase 3 Step 7). That isolation is the whole point,
  so it lives in its own workflow, not as a job bolted onto the secrets-laden backend suite.
- **Path filters both directions:** frontend-ci fires ONLY on `frontend/**` (+ its own file);
  ci.yml gained `paths-ignore: ['frontend/**']` (separate commit) so a frontend-only push no
  longer triggers a live cluster reseed. Any non-frontend change (app/migrations/seeds/tests/
  root docs like CLAUDE.md/NOTES.md/a workflow file) still runs the full backend suite.
- **Tier 2 is DEFERRED, not started this session.** Actual test coverage — Vitest unit tests on
  the pure logic (`deriveChain`, the investigation resolver, `treeLayout`, `consistencyStream`'s
  SSE frame parser, the `format.ts` formatters) — is a known, deliberate next step. No test
  runner, config, or scaffold was added; only the build/typecheck/lint gate exists today.

## Frontend Phase 6 — motion + polish audit (2026-07-07)

The last rung on FRONTEND.md's ladder. A holistic audit-and-harmonize pass over the whole
assembled app, NOT a feature phase — the four interactions + consistency demo already worked.
Three independent audits (reduced-motion, keyboard focus, motion timing) drove the plan; they
came back far cleaner than a five-session build had any right to, so the phase was confirmatory
more than corrective. Two commits: motion harmonization + reduced-motion reconciliation
(8f9a8cc), and the 3D-canvas focus fix (f462f27).

### Two library/behaviour decisions (both recorded so they don't get re-litigated)
- **Lenis (smooth-scroll): NOT added.** FRONTEND.md recommends it "confirm in your plan"; the
  answer is no. This app has no page-level scroll to hijack — the shell is `height:100vh;
  overflow:hidden`, a fixed CSS grid, and every scroll surface is a modest internal panel body
  (`.panel__body`, the demo body + its sample log). Lenis's inertial "alive under your hand"
  momentum has nothing to attach to, would need one instance per independent scroller running a
  continuous rAF loop, must be disabled under reduced-motion anyway, and — the real objection —
  imposes consumer-flashy momentum on a dense forensic feed, against the "clinical, dense, calm"
  language. The lighter alternative (scoped `scroll-behavior:smooth`) has no trigger either: no
  scroll-to-selection, no anchor nav. Net: no scroll library, no scroll-smoothing.
- **Springs: the app still uses NONE — a real, deliberate deviation from FRONTEND.md.** The
  stack section asks for "genuine spring physics, not just eased CSS transitions"; the motion
  audit found every framer-motion animation is a TWEEN, zero springs. Phase 6 chose Path A —
  harmonize the tweens in place, do NOT convert them to springs — because converting the two
  verified signature moments (Trace ignite, Invalidate corrected halo) for spec-purity is churn
  a polish pass shouldn't take on. The gap is flagged here as a KNOWN deviation, not closed. If
  a future pass wants to honour the spring language, the two atomic-bloom moments are the place.

### Motion harmonization (Path A) — lib/motion.ts is the new single source
- Before: the same gesture appeared at different durations across five sessions, and four
  framer animations silently inherited framer's default ease. New `lib/motion.ts` exports `EASE`
  (out / inOut only) + `DUR` (reveal 0.2 / sweep 0.9 / bloom 0.5 / pulse 1.5) + `BLOOM_TIMES`.
  Grouped by GESTURE, not feature. Trace/Invalidate/TimeTravel point at it; the CSS-driven
  animations (kill-fade, cx-fade, cx-pulse, cx-split-pulse) can't import TS, so they carry the
  SAME literals with a "keep in sync with lib/motion.ts" comment.
- Collapsed duplicates: panel/readout reveal 0.25s(framer)+180ms+200ms(CSS) → one 0.2s;
  live-pulse 1.6s(framer armed halo)+1.4s+1.5s(CSS) → one 1.5s; the 0.5s atomic bloom that Trace
  ignite + Invalidate corrected halo already shared is the anchor, kept. The four ease-unspecified
  animations now name an explicit ease (all EASE.out — reveals/tints/draws/blooms decelerate to
  rest; EASE.inOut reserved for the self-contained sparkline sweep + the infinite pulses).
- Named the previously-bare literals (TraceOverlay `EDGE_FADE`; the TimeTravel + InvalidateOverlay
  inline durations now resolve to DUR.*). NOTES' called-out Trace knobs (STAGGER/EDGE_DUR/NODE_DUR)
  stay LOCAL to TraceOverlay for live tuning; only IGNITE_DUR moved to DUR.bloom (it already ==0.5).

### Reduced-motion — reconcile the drift, do NOT unify the five idioms
- The audit confirmed all five animated features DO collapse to a static final state today (via
  five unrelated idioms: Trace runs a duration:0 anim that snaps; TimeTravel mounts already-
  complete; InvalidateOverlay swaps an infinite pulse for a steady value; Invalidate + 2D demo
  rely on CSS `@media (prefers-reduced-motion: no-preference)` gates; the 3D scene imperatively
  snaps in useFrame). Forcing one idiom across five independently-verified features is churn for
  tidiness, not a bug fix — left as-is. The shared DEFINITION ("collapse == the identical final
  state a full-motion viewer ends on") is documented at the reconciled sites.
- The ONE substantive fix: reduced-motion "final" values had drifted from the animated end-state
  — Trace origin glow settled 0.6 vs animated 0.55; Invalidate corrected halo 0.65 vs 0.6. Both
  now match, so reduced and full motion land pixel-identical (verified — the two Trace end-frames
  are indistinguishable).
- **The one load-bearing reduced-motion path — VERIFIED, not assumed.** Trace is the only feature
  whose conclusion is gated on an animation callback (`onAnimationComplete` → reveals the origin
  conclusion). Under reduce it runs a duration:0 anim and relies on framer still firing that
  callback. Playwright strand check (reducedMotion:"reduce"): after the real lineage fetch
  resolves, the conclusion text IS present → callback fires, no strand. (First check false-alarmed
  because a 400ms wait didn't clear the network round-trip — the button read "Tracing…"; lengthened
  past the fetch and it passes. Lesson: never assert a reduced-motion conclusion before the fetch
  it waits on has resolved.)

### Keyboard focus — every DOM control already at baseline; one canvas gap
- Audit result: every DOM interactive surface (feed rows, view toggle, strategy radios, 2D/3D
  toggle, Run/Stop/Confirm/Cancel, Invalidate arm/confirm/cancel, Trace/Replay, Time-travel
  toggle+open, the ✕ closes) already meets the decision-feed-row baseline — native control +
  `:focus-visible` + correct ARIA. Five sessions held the line; nothing to fix there.
- The ONLY gap: the 3D holder nodes are three.js meshes in `<canvas>`, so orbit/hover/select are
  pointer/raycast-only and the container wasn't in the tab order — keyboard focus skipped straight
  over the scene into nothing. Honest-minimal fix (per FRONTEND.md's own framing of 3D as the
  gated enhancement over the fully-accessible 2D meter): `.cx3d` is now a focusable REGION
  (tabIndex=0) with an inset `:focus-visible` ring in the feed-row convention, its aria-label +
  the affordance hint state plainly that 3D inspection is pointer-driven and the 2D view is the
  keyboard path. Full 3D keyboard parity (tab-cycle nodes, arrow-key orbit) is a real feature with
  its own scope — deliberately NOT smuggled into a polish phase. Verified: tabindex=0, reachable by
  keyboard Tab, ring renders (--bone, distinct from the resting --line border).

### Verification (Playwright @1280/1440/1920, channel:chromium, live 5173→8000)
- **Responsive:** Console + Consistency-demo, 2D and 3D, at all three widths — ZERO page errors,
  no crowding/overflow. The header-toggle-crowding concern didn't materialise (the demo's 2D/3D
  toggle sits in the content region, clear of the app header; wide layout is max-width capped, not
  stretched; the tree fills the wider centre well at 1920). No responsive fixes needed.
- **Console interactions, motion + reduced-motion:** Investigate → Trace (identical final frame
  both modes, warmth ONLY on the spine+origin+conclusion, strand check passes) → Time-travel
  (0.53 --alert stale / real MVCC held·ACTIVE depo, harmonized readout swap) → Invalidate arm gate
  (both real living holders 3fb55c + cd75b3 flagged --alert). All real cluster data, mono, correct
  colours, zero page errors.
- **Consistency demo:** reduced-motion collapse of the CSS pulses proven by computed style —
  no-preference → cx-split-pulse / cx-pulse; reduce → `animationName:none` for both. One PATIENT
  live eventual run under the harmonized code caught the split window (1 --alive + 7 --alert torn,
  real observer timings) and the done summary (all corrected, 9-vs-1 measured contrast intact,
  14 split reads) — red/green only, no amber leak, zero page errors.
- `tsc -b` clean, oxlint clean, `vite build` OK (the ~1.27 MB three chunk + >500 KB warning is the
  known accepted Phase-5 bundle, unchanged).

### CLUSTER GOTCHA (banked) — do NOT fire consecutive demo runs that abort mid-reseed
- First destructive attempt ran three demo runs back-to-back, each polling only 45s then closing
  the context WHILE STILL "reseeding cluster & connecting…". That is exactly the abort-mid-reseed
  pattern (Phase 4 note): each aborted-then-immediately-relaunched run stacks CRDB schema-change
  jobs and the reseed never completes. Key finding: the TRUNCATE never COMMITTED across all three
  (a probe showed belief still active + 4000 decisions intact) — aborting before the reseed's
  truncate commits destroys nothing, but it does jam subsequent reseeds. Fix that worked: stop
  hammering, let it settle, then a SINGLE patient run (long reseed tolerance, no early abort)
  completed cleanly in one go. Rule: one demo run at a time, let each reach `summary` before the
  next; never poll-then-abort during the reseed.
- That patient run DID truncate, so a final `python -m seed.backfill_decisions` (via `.venv`)
  restores belief=active + 4000 decisions + 8 perf windows (conf 0.924→0.528) for handoff.

## Roadmap Item 0 — cluster isolation for the demo stream (2026-07-07)

Item 0 of the new roadmap is a prerequisite, not a feature: the destructive consistency-demo
stream had to stop being able to wipe shared state out from under the rest of the console before
anything downstream (an AML dataset, a forensic eval, adversarial lineage-poisoning detection)
could assume the cluster stays in a knowable state. The fix landed as physical isolation — the
SSE consistency stream now runs its entire lifecycle (provision, reseed, observe, eventual/strong
invalidate) in a DEDICATED `demo` database on the same cluster, while the console (the decision
feed and all four supervisor interactions) keeps reading `defaultdb`. A `TRUNCATE` in `demo` is
physically incapable of naming a `defaultdb` table, so a demo run — deliberate or a stray
reconnecting EventSource tab — can no longer touch the console's data. This closes the Phase-4
"OPERATIONAL RISK" at its root cause (one shared cluster, no demo/console isolation).

### ⚠️ CORRECTION TO PRIOR GUIDANCE — the SSE demo is NO LONGER destructive-by-default
The operational warnings written across Frontend Phases 4, 5, and 6 all assumed the SSE
consistency stream reseeds and invalidates `defaultdb`, and therefore told future sessions to run
a ~228s `python -m seed.backfill_decisions` after every demo run/verification to restore the
console's belief=active + 4000 decisions + 8 perf windows. **That is now WRONG for the SSE-demo
path.** After this change the stream reseeds/kills only the throwaway `demo` database;
`defaultdb`'s backfill, perf windows, and belief are never touched by it. Running the demo — or
the `test_sse_stream` / `test_demo_isolation` tests — no longer requires any backfill recovery.
The prior "budget a backfill after every demo run" rule is superseded for the SSE stream.
- STILL TRUE, do not misread this: the console's own **Invalidate** action (POST
  /beliefs/{id}/invalidate) is the one real governed write and STILL invalidates `defaultdb`'s
  real belief by design, consuming it and requiring a reseed to restore. It was deliberately NOT
  isolated (isolating it would make the certificate a fake — FRONTEND.md requires a real cert with
  real belief_performance staleness). So: exercise the console Invalidate flow → still re-backfill
  afterwards; run the SSE consistency demo → no backfill needed.
- STILL TRUE: the direct-function consistency tests (`test_consistency_window.py`) and
  `test_atomic_invalidation.py` call the service functions with no injected engine, so they still
  reseed/invalidate `defaultdb` and remain destructive to it. Only the SSE ENDPOINT moved to
  `demo`. Pointing all of CI at its own database is the documented follow-up below.

### Verified facts (probed live, not assumed — the tier constraint drove the design)
- `CREATE DATABASE` / `DROP DATABASE` and `CREATE SCHEMA` / `DROP SCHEMA` are ALL **permitted**
  for the app user (`mohamed_aziz`) on this Basic-tier cluster (scratchpad/probe_isolation.py).
  Basic-tier billing is aggregated per org/cluster (RU + storage), not per database, so one extra
  small database is effectively free. Cluster is single-region `aws-eu-central-1`, `defaultdb` only.
- All six tables are ORM models on `Base` (incl. `audit_log` + the post-0003 belief_inheritance
  closure columns), so the `demo` database is provisioned with one idempotent `CREATE DATABASE IF
  NOT EXISTS` + `Base.metadata.create_all` — no Alembic. The demo needs only the lightweight
  genealogy seed (24 agents / 1 belief / 9 edges); never the decisions backfill or perf windows,
  which the console alone reads. Verified live: `demo` carries all six tables, the vector embedding
  column, and the closure columns.

### Design choices (weighed, with the roads not taken)
- **Physical (separate database) over logical (a run_id threaded through the five tables).**
  Logical isolation would have meant a schema migration plus a run_id predicate on nearly every
  query in a verified five-phase surface — the lineage recursive CTE, the AOST deposition, the
  set-based atomic closure UPDATE that is the whole CRDB kill-shot — muddying the clean five-table
  data model CLAUDE.md calls the moat, and turning fast `TRUNCATE CASCADE` into `DELETE WHERE
  run_id=X` (which also rewrites the MVCC history the AOST story reads). Wrong trade for a
  prerequisite meant to UNBLOCK the roadmap.
- **A single persistent `demo` database, NOT one-per-request.** Per-request would buy true demo
  concurrency at the cost of per-run DDL, orphan-database sweeping, and the schema-change-job
  stacking this project keeps hitting (the "indexes being dropped" TRUNCATE contention). The
  realistic threat model (stray tab, judge poking around, CI) is fully covered by decoupling demo
  from console; the existing single-flight guard already serialises two concurrent demo runs into a
  clean `busy`, and that collision is now confined to `demo` and harmless to the console either way.
- **Mechanism = additive dependency injection.** `seed.seed()`, `consistency.observe_closure`,
  `consistency.eventual_invalidate`, and `invalidation.invalidate_belief` grew an OPTIONAL
  engine/session_factory param defaulting to the app globals; the SSE endpoint (routers/demo.py)
  alone injects the `demo` engine (app/demo_db.py). Zero query-text changes — every statement in
  those functions is unqualified, so it resolves against whichever database the injected connection
  is bound to. Every existing caller/test passing no engine keeps hitting `defaultdb` unchanged.

### Graceful degradation (folded in, scoped to the two exposed surfaces only)
`app/resilience.py` classifies CRDB transients (SQLSTATE 40001 serialization; the 08xxx/57Pxx
connection/shutdown family; and the "indexes being dropped" TRUNCATE schema-change contention
string) and `run_with_retry()` does bounded exponential backoff, retrying the WHOLE unit and
raising `TransientRetryExhausted` past the budget. Applied at exactly two places and nowhere else:
the invalidation endpoint retries the whole serializable txn (the correct unit for a 40001) —
BeliefNotFound/AlreadyInvalidated still short-circuit to 404/409, and exhaustion maps to a clean
503 instead of an opaque 500; and the SSE stream retry-wraps provisioning + reseed so a transient
CRDB Cloud hiccup degrades to an honest `error` event instead of a broken stream or a wedged
single-flight guard.

### Deferred (documented, NOT done this session)
Pointing the rest of CI at its own database — so `test_atomic_invalidation` and the direct
consistency tests stop reseeding `defaultdb` and can never collide with local console use — is the
complementary fix to the "CI-vs-LOCAL collision" (Phase 4). It is a config/secret change (a
distinct CI DATABASE_URL), out of this session's scope (which was the SSE stream + the invalidation
endpoint). Left as the next isolation step if CI flakes reappear.

## Roadmap Item 1 (pre-work) — AML dataset modeling spike (2026-07-07)

Verification spike, NOT ingestion. Question: does an IBM AML transaction typology (a laundering
CYCLE, a SCATTER-GATHER, etc.) fit the existing five-table belief-inheritance model, or need
something structurally different? Pressure-tested against the REAL data before any ingestion.
Diagnostic: `scripts/probe_aml.py` (READ-ONLY, streams the 470MB CSV once, never loads it whole).

### GO. The five-table moat stays untouched; AML data becomes a separate evidence layer.
The working hypothesis held under real data. IBM's data models accounts and money flow — a
different graph from agent genealogy. It becomes NEW `aml_*` tables the belief/agent layer is
grounded and measured AGAINST; nothing about agents inheriting transactions.

### Verified facts (probed, not assumed)
- **Join is perfect.** Patterns.txt data lines are VERBATIM copies of Trans.csv rows, so the
  exact-tuple join (ts + both bank/account pairs + amounts + currencies + format) is a literal
  string match: **3209/3209 labeled pattern rows match exactly one CSV row — 0 misses, 0
  collisions, 0 duplicate keys within patterns**, across all 8 typologies. The feared
  floating-point-amount and timestamp-format drift do NOT exist (both files store the identical
  string; timestamps are minute-resolution `YYYY/MM/DD HH:MM`, no seconds).
- **Scale:** CSV = **5,078,345 transaction rows** (~470MB) — the FULL transaction universe;
  laundering is a tiny labeled subset. **5,177 rows carry Is_Laundering=1, but Patterns.txt labels
  only 3,209** → **1,968 laundering txns have NO typology label** (Is_Laundering is a superset of
  the pattern-labeled rows). Formats: ACH 4483, Cheque 324, Credit Card 206, Cash 108, Bitcoin 56.
- **Degeneracy confirmed:** 43/370 (11.6%) blocks are single-transaction — all in BIPARTITE(18),
  RANDOM(13), FAN-OUT(8), FAN-IN(4). CYCLE / SCATTER-GATHER / GATHER-SCATTER / STACK have ZERO
  degenerate blocks (the structurally rich typologies to draw from). Median hops: CYCLE 4,
  SCATTER-GATHER 14, GATHER-SCATTER 14, STACK 10 (max 32).
- **Hub-reuse is real but NARROW, and the worst offenders are artifacts.** Only 100/3170 nodes
  (3.2%) appear in >1 block. The block-sharing graph has **210 connected components; 190 blocks
  (51%) are fully isolated** self-contained stories. The apparent "super-hub in 32 blocks across
  all 8 typologies" is a **labeling artifact**: that largest component's 32 blocks span only TWO
  accounts (`023691/8021353D0` ⇄ `015231/80266F880`) ping-ponging tiny Euro ACH transfers, each
  2-row exchange relabeled as STACK / CYCLE-2-hops / GATHER-SCATTER-1-degree / BIPARTITE / FAN-OUT
  etc. The `Max N hops/degree` string is the generator's PARAMETER, not the instance's real size.
- **CYCLE vs SCATTER-GATHER overlap:** the first non-degenerate CYCLE (10 rows/10 nodes) and
  SCATTER-GATHER (32 rows/18 nodes) share **zero** nodes and touch no other block — genuinely
  isolated. So a bounded subgraph is easy to select cleanly (18 isolated non-trivial CYCLEs alone).

### Selection consequence for the next session (ingestion)
Filter TWO classes of noise, not one: the 43 single-txn blocks AND the trivial 2-node ping-pong
blocks (the `Max N` label lies about size — filter on ACTUAL distinct-node/hop count ≥ some floor).
Then prefer isolated components for a genuinely bounded subgraph, OR deliberately include a
hub-connected component if a shared-intermediary story is wanted — but know the biggest "hub" is a
2-account artifact, not rich infrastructure.

### Evidence-layer table sketch (NOT built — confirms zero touch to the five tables)
- **aml_accounts** — node identity is the compound `(bank, account)`, never account alone.
  `UNIQUE(bank, account)`, surrogate uuid PK.
- **aml_transactions** — the money-flow edges (the CSV): ts, from_account_id/to_account_id (FK
  aml_accounts), amount_received, receiving_currency, amount_paid, payment_currency, payment_format,
  `is_laundering` (ground-truth label). ~5M rows if fully ingested; the bounded subgraph is a slice.
- **aml_pattern_instances** — one row per labeled block: typology, generator max-param, source
  ('ibm-hi-small'), instance_index.
- **aml_pattern_members** — join block→its rows: pattern_instance_id (FK), transaction_id (FK
  aml_transactions), hop_index. The exact-tuple join, MATERIALIZED once at ingest so it's never
  re-run.

### The ONE real seam (new info the roadmap should note)
The two graphs connect at exactly one place, and it is ADDITIVE, not a restructuring: a `decisions`
row (existing table) may eventually cite a REAL `aml_transactions` row instead of a synthetic
`txn_ref`, and that decision's `is_fraud` would be sourced from `aml_transactions.is_laundering`
(ground truth). That is the grounding bridge roadmap items 3/4/7 already assume — a nullable
`aml_transaction_id` FK added to `decisions` LATER, or reuse of the existing free-form `txn_ref`.
No FK runs FROM the aml_* tables INTO agents/beliefs/inheritance; the agent layer READS the evidence
layer. A belief stays a heuristic rule ("cyclic flows returning to origin within N hops via shared
intermediaries indicate laundering") the agents inherit; the AML data is what lets it cite real
evidence and measure real precision/recall — nothing inherits a transaction. Forcing typology into
`belief_inheritance` (e.g. "this account inherited hub-status") would be exactly the forced-fit
fabrication this project refuses. Confirmed avoided.

### Do NOT build ingestion/migrations/tables yet — that is the next session, gated on this GO.

## Roadmap Item 1 — AML evidence-layer ingestion (2026-07-08)

Item 1 delivered: migration 0004 (four additive `aml_*` tables) + a deterministic, idempotent
ingestion of a bounded, verified laundering subgraph from the REAL 470MB data into `defaultdb`,
+ a verification script that re-derives every claim against the LIVE DB and the RAW CSV (not the
spike's numbers, not the ingest run's stdout). All 20 verification checks PASS. The five-table
moat is untouched; no FK crosses the aml_/moat boundary (verified structurally, check #7).

### Separate metadata is STRUCTURAL, not a convention (do not "fix" it back onto Base)
`app/aml_models.py` defines `AmlBase(DeclarativeBase)` with its OWN metadata, deliberately NOT
`app.db.Base`. This makes "the evidence layer is a defaultdb-only concern" impossible to violate
by accident, given how often this project has momentarily forgotten a documented constraint:
- Item 0's `demo` database is provisioned by `Base.metadata.create_all`. Off `Base`, that call
  physically cannot create empty `aml_*` tables in the throwaway demo db.
- Alembic migrations here are hand-written (0001-0003 precedent), so keeping `aml_*` off
  `Base.metadata` (Alembic's `target_metadata`) costs nothing — migration 0004 is their sole DDL.
A one-line comment at the top of `app/aml_models.py` states the why on a cold read; this is the
full reasoning.

### The four decisions, as REALIZED (verified live, not intended-on-paper)
- **Benign noise = is_laundering=0 txns TOUCHING the selected accounts**, per-account capped (8)
  and globally capped at 4x fraud. Realized **1200 benign : 300 fraud = 4.00:1**; every benign row
  is confirmed anchored to a labeled-instance account (check #4, 0 stray). These are the meaningful,
  adversarial precision negatives (they share nodes with the positives), NOT random unrelated rows.
  Byproduct: benign counterparties expanded the account universe to 648 (from the ~250 labeled).
- **Scope = 4 zero-degenerate typologies, 5 isolated instances each = 20 total, 300 labeled rows.**
  CYCLE + SCATTER-GATHER + GATHER-SCATTER + STACK (FAN-IN/FAN-OUT/BIPARTITE/RANDOM excluded — they
  carry the 43 degenerate single-txn blocks). Floor = >=4 distinct accounts (filters single-txn
  blocks AND the 2-node ping-pong artifacts whose "Max N" label lies about size). Greedy file-order
  selection keeps all 20 instances pairwise account-disjoint (check #3, 0 shared). All four
  typologies yielded a full 5; realized spread {'CYCLE':5,'GATHER-SCATTER':5,'SCATTER-GATHER':5,'STACK':5}.
- **Target DB = `defaultdb`** (console/Trace/Invalidate/belief_performance), NOT the throwaway
  `demo` db. Alembic points at `sync_database_url` (=defaultdb); ingestion uses the app engine.
- **Reseed interaction:** static reference data wired into NO reseed path. `seed.seed()` only
  TRUNCATEs `belief_inheritance, decisions, belief_performance, beliefs, agents CASCADE` — and
  because there are ZERO inbound FKs from aml_* into those tables, CASCADE cannot reach aml_*. The
  SSE demo targets `demo` (which now has no aml_* at all). Ingestion idempotency = deterministic
  uuid5 ids (account=uuid5(bank/account), txn=uuid5(raw_key), instance=uuid5(source:index),
  member=uuid5(inst:txn)) + `ON CONFLICT DO NOTHING`, so re-running converges without wiping.

### STACK instances are NOT one connected story — measured, flagged for future RAG grounding
A future session reading `aml_pattern_instances` must NOT assume `num_rows`/`num_accounts` imply a
single coherent path. Measured from the loaded data: `num_components` (weakly-connected components
among an instance's edges) is **1 for every CYCLE / SCATTER-GATHER / GATHER-SCATTER instance**, but
**>1 for ALL 5 STACK instances (4, 6, 10, 11, 11 components)** — i.e. STACK bundles internally
disjoint 2-hop sub-chains under one label, exactly as the pre-work spike suspected. The
`num_components` column exists precisely so this is answerable directly from the table (and the
column comment says so), not something a reader has to re-derive or take on faith.

### Verified facts (scripts/verify_aml_ingest.py, all PASS, against live defaultdb + raw CSV)
- Row counts: accounts=648, transactions=1500, instances=20, members=300. Split: 300 laundering,
  1200 benign. No member links a benign txn.
- Join integrity re-confirmed at ingest: 300/300 labeled keys matched exactly one CSV row, 0
  collisions, across the full 5,078,345-row scan (the spike's 3209/3209 result holds for our slice).
- Positional-parse (duplicate "Account" header) fix spot-checked BY HAND: 6 stored txns (3 laundering
  + 3 benign) reconstructed and their from/to bank+account compared column-for-column against the
  exact raw CSV line — all match; stored txn ids confirmed == uuid5(raw_key). (One benign spot row is
  a legit Reinvestment self-loop from==to — a real CSV shape, valid as a negative.)
- Structural isolation: querying information_schema, NO foreign key crosses the aml_/moat boundary
  in either direction.

### Mechanics / gotchas
- `scripts/ingest_aml.py` REUSES `scripts/probe_aml.py` (loaded via importlib since scripts/ isn't a
  package): `probe_aml.parse` (the positional fix) is reused verbatim; only header-param capture,
  selection, benign sampling, and the DB load are added. CSV streamed ONCE, never loaded whole (~10s).
- ts stored timestamptz-at-UTC from minute-resolution 'YYYY/MM/DD HH:MM' (no tz in source) — a
  storage-typing convention, documented in the migration; not a semantic tz claim.
- Bulk insert is fast (~1500 rows, client-assigned uuid5 ids, single txn) — none of the per-row
  RETURNING slowness the Phase-2 backfill hit (that came from server-default UUID PKs).
- Re-run to restore after a hypothetical wipe: `PYTHONPATH=. .venv/Scripts/python.exe scripts/ingest_aml.py`
  then `scripts/verify_aml_ingest.py`. Idempotent — same 20 instances / 1500 txns every run.

### hop_index semantics — real (generator/file order) but NOT chronological (verified, do not misread)
Post-ingestion review asked whether `aml_pattern_members.hop_index` encodes anything real.
Answer: YES — it is the Patterns.txt **generator emission order** within the block, and it is NOT
arbitrary insertion order. Verified with scripts/probe_hop_index.py against the live DB + an
independent re-parse of Patterns.txt (one instance per typology, incl. a multi-component STACK):
`hop_index == independently re-derived file position` holds EXACTLY for every row of every checked
instance. BUT it is **not reliably chronological**:
- CYCLE (instance_index=1, 1 component): ts IS monotonic along hop — here file order == chronological
  (a clean traversal path).
- SCATTER-GATHER (idx=17, 1 component) and STACK (idx=3, 11 components): ts is NOT monotonic along
  hop — file/generator order, not time order. And a num_components>1 STACK has no single path at all.

Consequence (flagged for Item 5 traversal narration and any future consumer): hop_index is a stable,
reproducible generator-order index — safe to ORDER BY for a deterministic listing — but it must NOT
be treated as chronological, and NOT as a single traversal path for multi-component instances. Any
time-ordered or per-path narration must derive order from `ts` and/or the edge graph
(component-aware), not assume hop_index is it. Left as-is (it faithfully encodes what it intended —
generator order); the column comment in app/aml_models.py now states this precisely rather than the
vague "order within the block". No data changed.

### Explicitly NOT done (still gated): Item 2 (reversible replay), Item 3 (RAG/typology-regulation
### corpus embeddings — owns transaction/typology EMBEDDINGS, none here), the decisions.aml_transaction_id
### grounding FK (items 3/4/7), any change to the five tables. Do NOT start Item 2/3 without approval.

## Roadmap Item 2 — reversible-deterministic replay over the lineage timeline (2026-07-08)

Item 2 delivered: a dedicated `GET /beliefs/{id}/replay?as_of=` surface that reconstructs ONE
belief's full inheritance closure (genealogy nodes + per-edge revocation state) AS OF an
arbitrary past MVCC point via real `SET TRANSACTION AS OF SYSTEM TIME`, and emits a canonical,
content-hashed snapshot. It reuses the deposition's AOST plumbing (app/services/time_travel.py)
over the lineage path's recursive closure CTE (app/services/lineage.py). The live `/lineage`
endpoint is UNTOUCHED — it stays current-state/unfiltered for the frontend Trace contract; replay
is a separate surface. 4 tests pass (2 replay + the Phase-1 AOST test re-confirmed for the
tiebreaker; the 2 lineage/AOST originals unchanged).

### Scoping conclusion on the GC TTL tension: scope (a), stated honestly (do NOT let "any timestamp" stand)
The roadmap's "any timestamp reproduces byte-identically" is NOT literally achievable via raw AOST
and was deliberately restated, not silently adopted. Raw `AS OF SYSTEM TIME` is bounded by the
range `gc.ttlseconds` = 4500s (~75 min, confirmed Phase 1) — it can reproduce any timestamp WITHIN
that window and nothing older. So the shipped scope is **(a): byte-identical replay within the
AOST-reachable window, full stop**; an out-of-window `as_of` (older than the GC TTL, or in the
future) fails inside CRDB at the SET statement and is surfaced as a **400, never a 500** (reusing
time_travel's `_AOST_RANGE_ERRORS` translation). This is NOT a gap left open: durability past the
75-min window is the certificate's already-proven pattern (Phase 3 / post-audit fixes) — a
self-contained, hash-covered snapshot captured at a consequential event, independent of whether the
live MVCC window still reaches it. So the honest two-tier framing the roadmap collapses into one
sentence is: **live raw-AOST replay = the within-window mechanism; a captured hashed snapshot = the
durability mechanism for events that must outlive the window.** Item 2 built the first tier plus the
canonical-hashing discipline that makes the two interchangeable to a consumer.

### "Byte-identical" as a falsifiable claim (the actual mechanism, not a vibe)
`content_hash` = `sha256` over the canonical (sorted-key, stable-separator) JSON of the
RECONSTRUCTED WORLD — belief + closure — and ONLY that. The input `as_of` and the resolved
`read_hlc` are returned as provenance but are NOT hashed: the claim is that the reconstructed
closure at a given time is byte-identical, and the world at two equal timestamps is the same world
(so hashing the input would be circular). The proof (tests/test_replay.py) lifts the Phase-1
AOST done-test to the hash level: two independent reads at the same captured HLC hash identically;
then a closure-CHANGING write (a new inheritance edge extending the origin belief to crimson-2b, a
real agent deliberately outside the seeded closure) is committed, and the replay at the OLD
timestamp STILL hashes identically (MVCC hides it) while a current read shows the grown 10-node
closure with a different hash. This is genuinely falsifiable because three real non-determinism
sources would break it — non-total row ordering, non-canonical serialization, and any `now()`/random
in the path. The closure CTE's `ORDER BY depth, generation, agent_id` is already a TOTAL order
(agent_id is a unique UUID); the belief-side deposition needed a fix (below) to match.

### Tiebreaker on Phase-1's oldest code — its own item, re-confirmed separately
Item 2 required the ONE surface it does not own — the deposition's `_BELIEFS_SQL` — to also be
total-ordered: it ordered by `b.formed_at` alone (not total; two beliefs can share a formed_at), so
add `b.id` as a tiebreaker. This is the first edit to `test_aost_hides_a_committed_write`'s
subject since Phase 1. Committed separately (`fix(backend): total-order the deposition query`), and
that exact Phase-1 test was re-run and **still passes unchanged** (32.56s, 1 passed) — confirmed on
its own, not bundled into the replay test summary.

### Why per-belief-closure is the RIGHT granularity for B and D (not just today's single-belief convenience)
Replay is scoped to ONE belief's inheritance closure, which today happens to equal "the whole
genealogy" only because exactly one belief exists (CLAUDE.md Phase 1). That coincidence is NOT the
reason for the scope. The belief is the system's actual unit of inheritance AND of invalidation:
`belief_inheritance` edges are keyed by `belief_id`, and the atomic kill-shot
(`invalidate_belief`) closes exactly one belief's closure in one txn. The two items this unblocks
operate on a single belief's chain by construction:
- **B (counterfactual "what-if invalidation")** asks "if belief X had been invalidated at time T,
  what closure/holders would that have touched, and which decisions would have flipped." It calls
  `closure_snapshot(X, as_of=T)`, gets the as-of-T edge set (with each edge's open/revoked state —
  that's why the snapshot carries `edge_invalidated_at`), and replays a hypothetical kill over that
  set IN MEMORY — a pure mirror of invalidation.py's set-based closure, no DB mutation. The hash
  guarantee means the counterfactual is itself reproducible.
- **D (confidence propagation through the chain)** walks a single belief's ordered inheritance
  edges propagating a confidence/staleness signal, joined with that belief's `belief_performance`
  windows. Replay gives D the graph AS OF T; belief_performance gives D the measured numbers (two
  clocks, kept distinct per the "do not conflate" note).
Both take a per-belief closure-as-of-T as their literal input. A whole-genealogy blob would be the
wrong shape for either — they'd have to filter it back down to one belief's edges anyway.

### Multi-belief future: the per-belief contract HOLDS unchanged (verified against the CTE, not assumed)
Once items 3/4 start forming beliefs from AML typologies there will be >1 belief, and the contract
does NOT need to change. `closure_snapshot(belief_id, ...)` and its CTE are already keyed by
`:belief_id` throughout (`WHERE bi.belief_id = :belief_id`), so a second belief's closure is a
separate, independent call — no cross-belief leakage, no shared-state assumption. What WILL change is
only the incidental identity "one belief's closure == the genealogy": with multiple beliefs, an
agent can hold several beliefs, closures will overlap on shared agents, and "replay the genealogy"
(if ever wanted as a distinct concept) would be a DIFFERENT, additive surface — a union over
per-belief closures or a genealogy-scoped snapshot — NOT a change to this endpoint's contract. B and
D still want per-belief, so no pressure to build that. Flagged so a future session does not
mistake per-belief replay for a single-belief shortcut that "should" be generalized onto /replay.

### No AML touch — confirmed, not assumed
Replay reads only `agents` / `beliefs` / `belief_inheritance` (the belief/agent timeline). The
`aml_*` evidence-layer tables (Item 1) are static reference data on their own `AmlBase` metadata
with zero inbound FKs to/from the moat, and are not part of the MVCC genealogy timeline. Nothing in
this item queries, migrates, or references them. As expected — confirmed by reading the closure
SQL, not by assumption.

### Mechanics / gotchas
- `content_hash`/`_json_default` are kept LOCAL to app/services/replay.py (mirroring certificate.py's
  canonical-digest discipline) rather than importing certificate's private helpers — replay is
  app-side, certificate is deliberately import-safe for the Lambda; coupling them buys nothing.
  **[REVERSED by Item 6, 2026-07-10 — the reasoning above was correct for Item 2 and stopped being
  correct the moment something needed to COMPARE the two hashes.** The certifier Lambda now
  independently re-derives this exact closure hash and checks it against the one the certificate
  embeds. If each side kept its own canonicalizer, that comparison would only ever prove "the two
  implementations still agree" — a guarantee that evaporates silently the day one drifts, while the
  check keeps reporting success. `canonical_json` / `canonical_digest` / `closure_world` now live in
  certificate.py (already import-safe, so the Lambda reaches them) and replay.py calls them.
  Serialization is byte-identical, so pre-existing replay hashes are unchanged. Recorded here rather
  than overwritten: the original call was not a mistake, its premise expired. See "Roadmap Item 6".]
- The belief lookup and the closure traversal run in the SAME explicit txn (after the one SET), so
  both read the identical MVCC snapshot — a belief that flipped between two reads can't produce a
  belief/closure mismatch within one call.
- `read_hlc` = `cluster_logical_timestamp()::string` captured inside the txn; for an HLC `as_of` it
  equals the requested literal exactly (Phase-1 property), so it's stable across reads at the same
  timestamp. It is metadata, not hashed.

### Explicitly NOT done (still gated): Item B (counterfactual what-if invalidation), Item D
### (confidence propagation), Item 3 (RAG/typology embeddings), any AML/RAG work, any change to the
### five tables. This session was scoped to the belief/agent genealogy timeline only. Do NOT start
### B/D/3 without approval.

## Roadmap Item 3 — CockroachDB-native RAG: typology corpus (2026-07-09)

Item 3 delivered the TYPOLOGY half of the RAG corpus: one additive `typology_corpus` table in
`defaultdb` holding the four IBM typology definitions embedded as REAL 1536-dim
text-embedding-3-small vectors, on the SAME cluster and SAME AOST timeline as the lineage graph and
the aml_* evidence layer, with a `retrieve_typology()` surface whose RESULTS join back to real
ingested pattern instances and whose RETRIEVAL can be time-travelled with real
`SET TRANSACTION AS OF SYSTEM TIME`. This is a mechanism-and-realness deliverable, NOT a
retrieval-quality-at-scale one — labeled as such below. Migration 0005 + 12/12 verify checks +
2 hermetic tests, all passing on the live cluster. Five-table moat untouched; no FK crosses into
it or into aml_*.

### Sourcing = two-track, and only the typology track was buildable this session (approved)
Corpus text is never fabricated (same discipline as the AML CSV). The **typology** definitions are
self-sourced from real primary text: Altman et al., "Realistic Synthetic Financial Transactions for
Anti-Money Laundering Models" (NeurIPS 2023, arXiv 2306.16424) — the paper behind the HI-Small
dataset — §3.2 / Figure 2, fetched live (arxiv.org/html/2306.16424v1) and quoted faithfully in
`app/services/corpus.py::TYPOLOGY_DOCS`. The **regulatory** corpus (FATF/FinCEN/FFIEC red flags) is
gated on a `data/raw/` drop because those sources are BLOCKED to automated fetch in this environment
(verified: fatf-gafi.org PDFs + HTML, bsaaml.ffiec.gov Appendix F HTML+PDF, fincen.gov advisories,
and web.archive.org all returned 403/timeout). Assembling it from WebSearch snippet fragments would
be lower-fidelity secondary text — refused. Precise drop list for the next session: FATF *Virtual
Assets Red Flag Indicators* PDF (or a FATF ML/TF Typologies report); the FFIEC BSA/AML Manual
Appendix F "Money Laundering and Terrorist Financing Red Flags" PDF; FinCEN advisories (e.g.
FIN-2014-A005 funnel accounts/TBML, FIN-2010-A001).

### Typology labels match EXACTLY (the join thread), enforced at load, re-checked at verify
The corpus `typology` values are the exact uppercase strings `CYCLE / SCATTER-GATHER /
GATHER-SCATTER / STACK` — the literal `BEGIN LAUNDERING ATTEMPT - <TYPOLOGY>` header tokens from
Patterns.txt, identical to what Item 1 stored in `aml_pattern_instances.typology`. The one real trap
is the paper's LOWERCASE prose ("cycle", "scatter-gather"); we store only the uppercase join key,
never the prose casing. `scripts/ingest_corpus.py` has a load-time GATE that ABORTS before any write
if a corpus typology is absent from `SELECT DISTINCT typology FROM aml_pattern_instances`, and
`verify_corpus.py` re-checks 0 orphans + all-4-covered against the live DB. NOT an FK: aml has five
rows per typology (no single parent to point at), and a cross-metadata FK would break the clean
CorpusBase/AmlBase/Base separation — a validated string + hard check is the honest equivalent
(same "materialize the join, verify against live data" discipline as Item 1).

### Structure = defaultdb, its OWN `CorpusBase` metadata (the Item-1 call, restated)
`app/corpus_models.py` defines `CorpusBase(DeclarativeBase)` with its own metadata, deliberately NOT
`app.db.Base` and NOT `AmlBase`. Same structural-safety reason as AmlBase: the throwaway `demo`
database is provisioned by `Base.metadata.create_all`, so keeping the corpus off `Base` makes it
physically impossible for a demo run to create an empty `typology_corpus`, and the five-table moat
stays exactly five. It DOES live on `defaultdb` and shares the AOST timeline — that one transactional
store spanning graph + vectors is the whole value prop over a Pinecone split. Unlike aml_*, this
table carries a `VECTOR(1536)` column + C-SPANN index, reusing `app/types_crdb.Vector` and the
migration-0001/0002 raw-`op.execute` vector-DDL pattern. Migration 0005 is its sole DDL.

### "No batch inserts" + "time-travel the retrieval" — as REALIZED
Each document is loaded in its OWN committed transaction (`scripts/ingest_corpus.py`, a `for` loop of
`async with engine.begin()` — NOT one multi-row INSERT), so each is its own AOST-addressable HLC
moment. The concrete "time-travel the retrieval" feature in scope: a corpus revision is an in-place
UPDATE (MVCC keeps the prior version), and a `retrieve_typology(query, as_of=T)` reads the corpus —
including the vector search itself — as of T. A query as of a pre-revision HLC returns the
pre-revision vector/ranking; present returns the revised one. DEFERRED to Item 4: using a retrieved
definition to ground a verdict, and the decisions.aml_transaction_id grounding FK.

### FINDING (requirement 1) — the C-SPANN vector index is NOT exercised at 4 rows. Full scan wins.
Confirmed via real EXPLAIN / EXPLAIN ANALYZE on the retrieval query against the live 4-row corpus:
the plan is `scan typology_corpus@typology_corpus_pkey` with `spans: FULL SCAN` feeding a `top-k`
sort (`order: +d, k: 3`); KV decodes all 4 rows; the vector index `ix_typology_corpus_embedding`
does NOT appear in the plan. So at this scale the planner brute-forces a full table scan + top-k, and
the vector index is not engaged. This is expected and is stated plainly: **Item 3 is a MECHANISM
proof (the VECTOR column, the `<=>` cosine operator, the C-SPANN index DDL, and AOST-over-vector-
search are all really wired end to end), NOT a demonstration of vector INDEXING at scale.** Meaningful
index exercise needs far more rows (the regulatory corpus, chunked, will help) — flagged for whoever
grows the corpus. `verify_corpus.py` captures and asserts this plan every run, so a silent divergence
would fail the check.

### FINDING (requirement 2) — the revision genuinely RE-EMBEDS (new vector, not stale text)
Proven two ways. The hermetic test (`tests/test_corpus.py`) EXPLICITLY asserts `new_vec != old_vec`
before checking AOST differentiation — the AOST proof is only meaningful because the vector moved,
and the test states that, it does not assume it. The LIVE demo (`scripts/demo_corpus_timetravel.py`)
exercises the REAL `embed_text()` path: it revises CYCLE's body, re-embeds via
text-embedding-3-small, asserts `new_vec != old_vec` (measured cosine distance old↔new ≈ 0.037), and
shows that querying with the OLD vector self-matches CYCLE at distance 0.000000 AS OF t0 (v1) but at
0.036719 now (v2) — the stored vector genuinely moved and CRDB time-travels the vector search to
reproduce the pre-revision embedding. The demo is REVERSIBLE (writes the saved originals back), so it
leaves the corpus pristine (re-verified: CYCLE self-retrieves at 0 again).

### CONSTRAINT (requirement 3) for the FUTURE regulatory-corpus session — NOT optional
This session did NOT touch chunking, correctly: the four typology definitions are already atomic,
self-contained units, so one-document-one-embedding is right and no chunking logic exists. But the
regulatory corpus (FATF / FFIEC / FinCEN) is HIERARCHICAL — sectioned, nested, heading-structured —
and naive fixed-window or recursive-character chunking is the WRONG default for it. Whoever ingests
that corpus MUST use structure/heading-aware chunking, and MUST prepend each chunk's section path as
context BEFORE embedding it (e.g. "FATF §3.2 Virtual Asset Red Flags > Structuring: <chunk text>"),
so a retrieved fragment carries its own provenance and a query about "structuring" ranks the right
subsection. This is a requirement that session must honor, not a simplification it may skip. (It is
also what will finally give the vector index enough rows to actually be exercised — see finding 1.)

### What Item 4 can call once this ships (the analog to Item 2's closure_snapshot())
`app.services.corpus.retrieve_typology(query_vec, *, k=3, as_of=None, source=None) -> list[dict]`.
Returns cosine-nearest corpus rows `{id, typology, title, body, source, version, distance}`, ordered
by ascending cosine distance. GUARANTEE: every returned `typology` is a value present in
`aml_pattern_instances.typology` (load-gate enforced), so Item 4 retrieves a grounded definition and
joins it straight to real pattern instances → real transactions with NO fuzzy matching. `as_of`
(ISO-8601 or HLC) time-travels the retrieval with real AOST; out-of-window/malformed → ValueError
(map to 400), never 500 — same contract as the deposition/replay. `source` scopes the search (e.g.
to `altman-2306.16424` vs a future regulatory provenance). Item 4 supplies the query vector via the
SAME `embed_text()` the agent already uses; nothing here calls OpenAI except the loader/demo.

### Mechanics / gotchas
- `retrieve_typology` reuses `time_travel.normalize_as_of` + `_AOST_RANGE_ERRORS` (no duplicate AOST
  parsing) and mirrors `beliefs_held_by_agent`'s single-connection/explicit-txn/first-statement-SET
  shape. The `<=>` cosine operator + `(:qvec)::VECTOR(1536)` cast match `agent_brain._retrieve_beliefs`.
- Idempotent load = `id = uuid5("lineage.corpus", f"{source}:{typology}")` + `ON CONFLICT
  (source, typology) DO NOTHING`. A revision is a deliberate separate UPDATE, never the loader.
- `tests/test_corpus.py` is CI-SAFE and NON-DESTRUCTIVE: hand-built vectors (no OpenAI, so the dummy
  CI key is fine), scoped to a private `source='__test_corpus__'` tag, deleted before+after. It does
  NOT call `run_seed`, so unlike the replay/consistency tests it never wipes the moat backfill.
- Windows console: EXPLAIN output uses box-drawing chars (│) that cp1252 can't encode → run the
  scripts with `PYTHONIOENCODING=utf-8` (verify/demo already ascii-'replace' the plan lines).
- Migration 0005 applied to the live cluster; re-run to restore after a hypothetical wipe:
  `PYTHONPATH=. .venv/Scripts/python.exe scripts/ingest_corpus.py` then `scripts/verify_corpus.py`.

### Explicitly NOT done (still gated): the regulatory corpus (gated on a data/raw/ drop; must use
### structure-aware chunking per the constraint above), Item 4 (the grounded fraud agent that CALLS
### retrieve_typology to ground a verdict), Item E (explanation-faithfulness guard), the
### decisions.aml_transaction_id grounding FK, any change to the five tables or the aml_* schema.
### Do NOT start Item 4 / E without approval.

## Roadmap Item 4 — grounded AML agent with STRICT-BRAKE (2026-07-10)

Item 4 delivered: a retrieval-cited AML verdict with a three-way brake that can never flag
without a witness, plus a DETERMINISTIC verdict validator. No migration, no new table, no change
to the five-table moat, `aml_*`, or `typology_corpus`. 13 tests pass (CI-safe, read-only); FLAG
verified end to end against a live OpenAI call.

### The terminology collision, resolved before a line of brake logic was written
This codebase uses "lineage" for BELIEF INHERITANCE among agents (`belief_inheritance`, walked by
lineage.py, replayed by `replay.closure_snapshot()`). The roadmap's "no corroborating lineage
evidence" does NOT mean that graph — belief inheritance has nothing to say about whether funds
returned to their account of origin. The brake checks the AML MONEY-FLOW graph
(`aml_transactions`, self-joined on from/to account). Confirmed live, not assumed: every FK
touching the evidence layer stays inside it (aml_transactions->aml_accounts,
aml_pattern_members->{aml_pattern_instances,aml_transactions}); `typology_corpus` has ZERO FKs;
`closure_snapshot()` reads only agents/beliefs/belief_inheritance. Wiring the brake against
agents/beliefs would have "worked" — it would have returned rows — and been silently wrong.

### `aml_pattern_members` is the ANSWER KEY, not evidence (the load-bearing discipline)
A membership row says "this transaction belongs to a block the generator labeled SCATTER-GATHER".
A structural check that consults it is a ground-truth lookup wearing a graph's clothes: the LLM's
reasoning becomes decorative and the "grounded agent" claim is hollow. So `app/services/aml_graph.py`
recomputes structure from the unlabeled edge set and **selects no label column at all**;
`aml_pattern_members` / `is_laundering` are read ONLY by tests, as a scoring oracle, and by the demo
to print an oracle column after the fact. Consequence, decided explicitly: the proposed check "does
the cited pattern-instance id exist and match the claimed typology" was REJECTED. Verifying an
instance id means reading the label; instance ids are never in the prompt, so the model can only
produce one by hallucination (vacuous-or-failing), and putting them in the prompt hands over the
answer key. The agent cites unlabeled transaction rows; the structure is recomputed.

### The brake (app/services/verdict_guard.py) — FLAG requires a witness, always
Gate 0: the claimed typology must be one retrieval actually returned. `k=3` against a 4-document
corpus — at k=4 the "retrieved set" IS the corpus and the gate can never fire. The claim's typology
is a free string in the JSON schema, NOT an enum, for the same reason (an enum makes the
hallucinated-citation gate unfalsifiable).
Gate 1a: the typology must be structurally decidable here (FLAG_CAPABLE, below).
Gate 1b: the graph decides. `MATCH` -> FLAG; `CONCLUSIVE_NO` -> NO_FLAG; `INCONCLUSIVE` -> INSUFFICIENT.
Then: the model's OWN cited path is re-derived from the rows (`verify_witness_path`) — real evidence
plus an unfaithful citation is still an unsupported assertion, so the flag is withheld
(`unfaithful_citation`). The graph, not the citation, is the authority: citing a genuinely real cycle
from elsewhere does NOT make the subject part of one (test asserts NO_FLAG, not FLAG).

### Why the three outcomes exist: the evidence layer is a BOUNDED extract
1,500 edges out of a 5,078,345-row universe, and **220 of 648 accounts are sinks** (no outgoing edge
in the slice). So "the cycle search found nothing" has two meanings, and conflating them is how a
detector lies. A negative is CONCLUSIVE only if the search closed WITHOUT touching a sink; if it hit
one, the honest answer is INSUFFICIENT_COVERAGE and the boundary account is named in the outcome.
This — not a confidence threshold — is what "genuine uncertainty" means here. Measured split of the
CYCLE search over all 1,500 edges: benign 458 conclusive-no / 728 inconclusive / 14 match;
CYCLE members 43 match; SCATTER-GATHER 96 inconclusive; STACK 84 inconclusive.

### FINDING — retrieval distance is NOT a coverage signal, in either direction. It gates nothing.
Two independent measurements, and together they are the strongest form of "only structure may
authorize a flag":
- A description of a FAN-IN funnel typology the corpus does NOT contain retrieves GATHER-SCATTER at
  cosine **0.391 — closer than EVERY in-corpus query (0.463-0.505)**, because "many accounts pay into
  one" is literally the gather half. A `distance < tau => confident` rule would have confidently
  grounded a verdict on a typology the corpus cannot cover.
- The agent's real query is a NEUTRAL structural summary (degrees, path lengths — no typology words),
  and those separate the top-2 documents by only **0.0005-0.02**. An early `MARGIN_FLOOR = 0.01` gate
  rejected a real STACK subject at margin 0.0005 — a reason with no bearing on whether the structure
  exists. Gating on it made the brake a wall, not a brake. The margin is now recorded as provenance
  (`VerdictOutcome.retrieval_margin`) and decides nothing. Safety is preserved because a witness is
  still required for FLAG.
Also measured: you CANNOT retrieve a typology by embedding a raw transaction row. A bare row retrieves
CYCLE at 0.680; an off-topic chargeback complaint retrieves CYCLE at 0.743 — both are just "nearest of
four distant things". Hence `structure_text()`.

**CONSEQUENCE, stated because the approved plan said otherwise:** "degenerate retrieval" is NO LONGER a
reachable INSUFFICIENT_COVERAGE reason. The plan named "a nonsense query's 0.0014 margin" as a test case
for that path; the path was removed, and the test was INVERTED rather than deleted —
`test_a_degenerate_retrieval_margin_does_not_block_a_witnessed_flag` feeds a 0.0014 margin alongside a
real witness and asserts the verdict is **FLAG**, with the margin recorded on the outcome. The four
reachable INSUFFICIENT_COVERAGE reasons are now exactly: `typology_not_retrieved`,
`typology_not_decidable`, `search_reached_extract_boundary`, `unfaithful_citation`.

### FLAG_CAPABLE = {CYCLE, SCATTER-GATHER} — measured, not hand-picked, and TEST-ENFORCED
The criterion is "the witness never fires on an edge belonging to a DIFFERENT typology", evaluated
over all 1,500 edges. Precision/recall, stated plainly because Item 7's headline eval inherits them:

| typology       | recall        | cross-typology | benign FP  | precision         |
|----------------|---------------|----------------|------------|-------------------|
| CYCLE          | 43/43 = 100%  | **0**/257      | 14/1200    | 43/57 = **75.4%** |
| SCATTER-GATHER | 39/96 = 40.6% | **0**/204      | 3/1200     | 39/42 = **92.9%** |
| GATHER-SCATTER | 64/77 = 83.1% | 6/223          | 37/1200    | 64/107 = 59.8%    |
| STACK          | 6/84 = 7.1%   | 27/216         | 2/1200     | 6/35 = 17.1%      |

**THE TWO SHIPPABLE LIMITATIONS, STATED SO ITEM 7 CANNOT INHERIT THEM BY SURPRISE.** The two
FLAG-capable typologies fail in OPPOSITE directions, and neither number may be quoted alone:
- **CYCLE flags a benign transaction roughly one time in four.** Precision **75.4%** — 14 of the 57
  edges it fires on are benign. It catches every real cycle (100% recall) and pays for it in false
  positives.
- **SCATTER-GATHER misses well over half of all real scatter-gather edges.** Recall **40.6%** — it
  finds 39 of 96. When it does fire it is nearly always right (92.9% precision), but a detector that
  silently passes 57 of 96 true positives has a false-NEGATIVE problem, not a precision success.
  This is an honest limitation of this slice, not a bug: the witness requires the SUBJECT edge to
  participate, so the gather-leg edges of a real scatter-gather never fire on their own.

"Sound" would undersell all of this.
`tests/test_aml_brake.py::test_witness_soundness_and_benign_false_positive_rates` asserts ALL of these
counts, because soundness (zero cross-typology confusion) and benign false-positive rate are
**different properties** — the first selects FLAG_CAPABLE, the second is what a real detector lives or
dies by, and soundness says nothing about it. Neither may be silently re-baselined.

**CAVEAT THAT MUST TRAVEL WITH THESE NUMBERS WHEREVER THEY ARE CITED:** they are measured against Item
1's *deliberately adversarial* benign set — noise anchored to the SAME accounts as the fraud, capped at
8/account. Measured consequence: on labeled accounts, benign edges contribute **783** degree endpoints
versus **598** from the labeled edges themselves, i.e. the noise manufactures hub structure around
exactly what a hub-detector examines. That is the right choice for a harder test, but these numbers
would likely look better against a naturally-distributed benign population. Do not quote them as
absolute detector performance.

**FORWARD POINTER — GATHER-SCATTER/STACK FLAG-incapability is DATA-DEPENDENT, not architectural.**
GATHER-SCATTER fails on two independent counts (its witness fires on 37/1200 benign rows, and retrieval
cannot separate it from SCATTER-GATHER: the gather-scatter description retrieves the SCATTER-GATHER
document top-1, 0.505 vs 0.536). Every ingested STACK instance is internally disjoint (num_components
4..11, Item 1), so it has no single connected path to witness. If a later ingestion changes graph
density, the asserted counts shift, the soundness test FAILS, and turning a typology's FLAG capability
on becomes a deliberate decision — never a silent flip. That test failing is a capability change to
notice, not a number to update.

### DEVELOPMENT-SET DISCLOSURE (added by Item 7, 2026-07-10) — these numbers were NOT a hold-out
The precision/recall table above is measured on the SAME 1,500-edge extract that design decisions
were made against. It is therefore a **development-set (in-sample)** result and must NOT be quoted
as "a hold-out you never tuned." Item 7 investigated exactly what did and did not touch this extract,
and the honest three-way split is:
- **DERIVED-FROM-DOMAIN (clean, not fit to data):** the witness ALGORITHMS themselves
  (`cycle_witness`, `scatter_gather_witness`, ...) come from the IBM/Altman typology definitions
  (Item 3 corpus). A directed cycle returning to origin over >=3 accounts is the paper's definition,
  not a shape reverse-engineered from the 43 CYCLE edges. Defensible as-is.
- **SELECTED-BY-LOOKING-AT-RESULTS (in-sample decisions):** `FLAG_CAPABLE = {CYCLE, SCATTER-GATHER}`
  was CHOSEN by measuring cross-typology soundness across all 1,500 edges (a discrete 2-of-4 pick on
  the eval data); a GATHER-SCATTER predicate tightening was measured on this extract before being
  rejected; and the shipped SCATTER-GATHER "subject must participate" tightening MOVED its numbers on
  this extract (recall 48->39, benign FP 24->3). The final counts were then read off that same,
  already-inspected set.
- **SET-FROM-OBSERVED-DATA (low-stakes but informed by the extract):** `MAX_CYCLE_HOPS=12` and the
  neighbourhood radius were fixed from observed cycle lengths (6, 7, 10). They only need to exceed
  observed maxima, but they are choices informed by this data.
Degrees of freedom are low (no continuous threshold was gradient-fit to maximize a metric; MARGIN_FLOOR
and distance-tau gates were explicitly REJECTED), so this is not egregious leakage — but "never tuned"
is false for this set. Item 7 earns the never-tuned headline on a genuinely fresh, account-disjoint
slice; see the "Roadmap Item 7" section below. Ground-truth note restated for this table: it is scored
against pattern-typology MEMBERSHIP (ring detection), and inside this extract `is_laundering=1` <=>
pattern-member by construction, so it is a RING-detection number, not general fraud detection.

### Overlap with Item E — stated, not left to be discovered
This validator IS the citation-and-structure half of Item E, built now and meant to be SUBSUMED, not a
parallel design. What deterministic checks catch: a typology retrieval never returned; a transaction id
that does not exist; a cited path that does not form the claimed structure (re-derived from the rows,
never compared against our own search's answer); a FLAG asserted without a witness. What they cannot
catch is unfaithful PROSE — "funds returned within 24 hours through a shell company" is a claim about
timing and corporate form that no cited edge supports. That is Item E's genuinely different half and the
only place an LLM judge earns its place. No second LLM call was added here; instead the response schema
keeps the rationale in evidence-referencing slots to shrink the surface for drift.

### Verdicts are EPHEMERAL — no table, deliberately (the Item-1 call, restated)
`decisions` is the wrong shape, verified against the live schema: `agent_id` is NOT NULL FK->agents (an
AML verdict has no fleet agent), `merchant`/`amount` are NOT NULL card fields, and `verdict` is
`approve|decline|blocked` — a payment-authorization vocabulary that cannot express
FLAG/NO_FLAG/INSUFFICIENT_COVERAGE. Forcing it in would need a synthetic agent and a fake merchant: the
exact forced-fit fabrication the Item-1 spike refused. A dedicated `verdicts` table is the right eventual
answer, but its columns are determined by what Item 5 reads back. Decisively: a verdict is a pure function
of (subject, corpus@T, graph@T), and all three are reproducible at a past instant via AS OF SYSTEM TIME —
a derivable, reproducible result needs no storage to be trustworthy. Persist when a verdict becomes a
consequential act someone is held to (Item 5's call, or a certificate). The `decisions.aml_transaction_id`
grounding FK remains deferred.

### Three defects found by RUNNING the agent, not by reading it (all fixed; banked)
1. The prompt rendered candidates as `[TYP] title`, so the model copied
   `"[SCATTER-GATHER] Scatter-gather laundering typology"` and Gate 0 fired on our own formatting.
   Fixed the rendering (`typology: CYCLE` on its own line), NOT the gate.
2. The far side of a 10-edge cycle is 5 hops from the subject, but the prompt carried a 2-hop
   neighbourhood — the model was asked to cite edges it had never been shown, so every cycle claim was
   rejected as `unfaithful_citation`. `neighbourhood()` now covers the searchable region (hops=6,
   limit=120), ordered by distance from the subject so truncation drops distant distractors, not the path.
   Handing over the searchable region is not handing over the answer: the path arrives mixed with every
   distractor in the same radius.
3. gpt-4o-mini initially cited a SUPERSET — the correct 6 cycle edges plus one unrelated inbound edge to
   an intermediary. `verify_witness_path` rejects supersets ON PURPOSE (accepting them lets a model cite
   the whole neighbourhood and always "contain" a cycle). Fixed by an explicit prompt contract ("a cited
   path with one extra transaction is rejected exactly like a fabricated one"), never by weakening the
   check. The model then cited exactly the 6 edges and the brake FLAGged.

### Mechanics / gotchas
- `MAX_CYCLE_HOPS=12` covers every ingested cycle (observed lengths 6, 7, 10 = the instance sizes) with
  headroom. `MIN_CYCLE_LEN=3` — length 2 would accept a reciprocal ping-pong pair (2 exist).
- Self-loops (447 rows, 446 benign "Reinvestment" transfers, from==to) are excluded from all adjacency:
  an account paying itself is not a transfer. They land in CONCLUSIVE_NO. An early probe that did NOT
  exclude them inflated the GATHER-SCATTER/STACK benign counts (49/60 vs the correct 37/2).
- The shipped SCATTER-GATHER witness requires the SUBJECT to participate in the witness (not merely sit
  near one). That is stricter than the design probe and moves its numbers: recall 48->39, benign FP 24->3.
- `tests/test_aml_brake.py` is CI-SAFE: no OpenAI (claims built directly; retrieval queries with a
  document's OWN stored embedding, the test_corpus trick) and read-only — unlike the replay/consistency
  tests it never calls `run_seed`, so it cannot wipe the moat backfill.
- Live demo: `PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_grounded_agent.py`.
  The brake's outcome depends on what the model CLAIMS, so a subject does not map to a fixed verdict; each
  branch is exercised deterministically in the tests.

### What Item 5 can call once this ships
`app.services.aml_agent.evaluate_transaction(txn_id, *, as_of=None) -> VerdictOutcome`, carrying
`verdict` (FLAG|NO_FLAG|INSUFFICIENT_COVERAGE), `reason`, `claimed_typology`, `corpus_doc` (the cited
definition + its cosine distance), `witness_txn_ids` (real `aml_transactions` ids — click-to-interrogate
resolves each to a real row), `boundary_account` when inconclusive, `retrieval_margin`, and
`validation_errors`. `as_of` time-travels the corpus retrieval through Item 3's proven AOST path.

### Explicitly NOT done (still gated): Item 5 (click-to-interrogate), Item E (explanation-faithfulness
### guard — this is its citation half only), Item 7 (headline eval), the regulatory corpus (still gated on
### the data/raw/ drop), the decisions.aml_transaction_id grounding FK, any change to the five tables,
### aml_* schema, or typology_corpus. Do NOT start Item 5 / E / 7 without approval.

## Roadmap Item 5 — click-to-interrogate a transaction, with conflict-surfacing (2026-07-10)

Item 5 delivered: a DETERMINISTIC interrogation surface over the AML money-flow graph
(`app/services/aml_interrogate.py`), competing-witness provenance on every verdict branch, and
a read-only `GET /aml/transactions/{id}/interrogate`. No migration, no new table, no OpenAI
call, no change to the five-table moat / `aml_*` / `typology_corpus`. 21 new tests (12 service in
`test_aml_interrogate.py` + 9 HTTP in `test_aml_routes.py`); the 14 Item-4 brake tests pass with
`tests/test_aml_brake.py` byte-unchanged. Verified after the fact, not inferred: `alembic current`
= 0005 (head), and `information_schema` reports the same 12 tables with unchanged column counts.

**Service tests are NOT route tests — a gap this session initially shipped.** The service test
proving `interrogate_transaction()` returns `None` / raises `ValueError` says nothing about
whether the ROUTER turns those into 404 / 400. That translation is router code, and an untested
translation is how a `ValueError` becomes an opaque 500 on camera. `tests/test_aml_routes.py`
drives the real app through `httpx.ASGITransport` and asserts the status codes, plus two
route-level properties no service test can see: every `/aml` route is a GET, and a tripwire
asserts no route ever invokes `aml_agent.evaluate_transaction()` (the paid OpenAI path) —
including on the 404 path, so the obvious future mistake ("fall back to the agent on a miss")
fails loudly.

### The wrong-graph trap, caught a second time
The roadmap's Item-5 wording ("real inherited beliefs, ancestor rows... on belief conflict")
predates the Items 1-4 pivot onto the AML evidence layer and, taken literally, would have wired
this against `belief_inheritance` — the same terminology collision Item 4 caught. It does not
mean that graph. There is no ROADMAP.md in this repo (the wording lives outside it), so intent
was resolved against three in-repo anchors the Item-4 session left, none written to be
convenient here: `aml_agent.evaluate_transaction`'s docstring says verbatim "The Item-5 entry
point"; `describe_transaction` is annotated "(for demos / Item 5)"; `aml_models.hop_index`'s
comment warns that "narration (e.g. Item 5) must derive order from ts/edges". Plus NOTES Item 4:
"witness_txn_ids (real aml_transactions ids — click-to-interrogate resolves each to a real row)".
**"A node" is an AML transaction.**

### PRECISION: a transaction is an EDGE. Accounts are the nodes.
`aml_graph.Edge` IS the transaction; accounts are the graph's vertices. So interrogation resolves
BOTH object kinds — the subject and each witness member as transaction rows, and the accounts they
run between, including the `boundary_account` an INCONCLUSIVE search names (previously a bare uuid
with no resolver anywhere). The old `describe_transaction()` had ZERO callers and returned only
id + bank/account (no ts/amount/currency/format); it is superseded.

### FINDING — the witness id list carries NO traversal order. It must be re-derived.
Three measured facts, each of which would have produced a plausible, silently-wrong UI:
- `verify_witness_path` is ORDER-INSENSITIVE by construction (it rebuilds the chain from a
  `{src: edge}` map). Verified: feeding it a cycle citation with its tail reversed still returns
  `None` (valid).
- On a FLAG, `VerdictOutcome.witness_txn_ids` is `claim.evidence_txn_ids` — **the MODEL's list**,
  not the graph's. The graph's canonical ordered path is on a different field entirely,
  `structural_check.witness_txn_ids`. NOTES Item 4 advertised the former to Item 5.
- The model's cited cycle and the graph's found cycle can be two DIFFERENT real cycles (the
  graph's BFS returns the shortest return path; the model may cite a longer valid one). Both
  verify. Both are real.
So order is re-derived from the rows (`ring_order`, `scatter_gather_legs`), never read off a list.

### Only CYCLE has a linear order — and it is a CLOSED RING, so `predecessor_of` is total
Measured over all 1,500 edges: **57/57 CYCLE witnesses are contiguous AND closed**
(`last.dst == first.src`). Consequence: every hop has exactly one predecessor and the SUBJECT's
predecessor is the closing hop. There is therefore **no "first hop, no predecessor" case**, and
`PredecessorStatus` deliberately has no `FIRST_HOP` member;
`test_cycle_witness_is_a_closed_ring_so_predecessor_is_total` is what keeps that true.
SCATTER-GATHER's witness is NOT a path: two scatter legs leaving one source + two gather legs
entering one destination (verified non-contiguous) — two parallel 2-hop routes. `TraversalKind` is
`RING` / `LEGS` / `BUNDLE` (GATHER-SCATTER, STACK) / `NONE`, and `predecessor_of` returns a
`PredecessorStatus` enum (`RESOLVED` / `NO_LINEAR_ORDER` / `NOT_IN_WITNESS`) so a caller never
parses a reason string to tell "not a path" from "not a member".

### Conflict: it HALF-existed. The half that was missing is where the real conflicts live.
`_competing_match` already ran other typologies' witnesses, and the `contradicted` downgrade was
already implemented AND tested. But it was reachable from exactly one of four branches
(`CONCLUSIVE_NO`), considered only `FLAG_CAPABLE` competitors, and short-circuited on first hit.
Measured over all 1,500 edges (all four witnesses vs every edge):

| typology       | MATCH edges | flag-capable |
|----------------|-------------|--------------|
| CYCLE          | 57          | yes          |
| GATHER-SCATTER | 107         | no           |
| SCATTER-GATHER | 42          | yes          |
| STACK          | 35          | no           |

- **CYCLE ∩ SCATTER-GATHER = 0.** The two FLAG-capable typologies NEVER co-witness the same edge.
  So "the detector is torn between two flags" is UNREACHABLE in this extract and must not be
  demoed. Stated plainly rather than discovered later.
- 26 edges carry >=2 competing witnesses (24 with exactly 2, 2 with 3). Pairwise:
  C∩GS 3, C∩STACK 9, GS∩STACK 14, SG∩STACK 4, C∩SG 0, GS∩SG 0.
- **10 edges would FLAG on CYCLE while another typology also witnesses.** Reported today; gated
  by nothing.
- On **all 42 SG-MATCH edges, CYCLE is INCONCLUSIVE** (never CONCLUSIVE_NO) — a branch
  `_competing_match` never ran on, so a live competing witness sat on the subject unmentioned.
  Symmetrically, on the 57 CYCLE-MATCH edges SG is CONCLUSIVE_NO on 40 (today's `contradicted`
  set) and INCONCLUSIVE on 17.
- ORACLE, after the fact only: the 40 contradicted edges = 28 real CYCLE-labeled + 12 benign.

`competing_witnesses()` now runs all four typologies, no short-circuit, and rides EVERY
`VerdictOutcome` as `competing_typologies`.

### SURFACING IS NOT GATING (the rule that keeps this from becoming MARGIN_FLOOR again)
The widened result feeds exactly ONE decision — the pre-existing `CONCLUSIVE_NO` downgrade, which
still requires a FLAG_CAPABLE contradictor, so `test_no_flag_downgrade_...` passes byte-unchanged.
A non-flag-capable witness NEVER withholds a corroborated FLAG. Allowing it would repeat Item 4's
`MARGIN_FLOOR` mistake exactly: evidence that gates nothing must not start gating.

### The demo exhibit that is real: `3cda6d1d-f765-5001-9342-0478b1a92232`
**BENIGN** by the oracle (`is_laundering=false`), yet CYCLE **and** GATHER-SCATTER **and** STACK
all produce real structural witnesses on it, and it would FLAG. This single row is the honest,
citable face of CYCLE's measured 75.4% precision (14 of the 57 edges it fires on are benign) —
far better than a demo of a conflict that does not exist. Other locked subjects:
`045adfd2-...` (clean 10-hop CYCLE, oracle laundering) and `1384b7bc-...` (CYCLE + STACK).

### The CYCLE ∩ SCATTER-GATHER == 0 test is a LIVING INVARIANT, not a fact
Same framing as Item 4's FLAG_CAPABLE soundness test. It is a property of what Item 1 ingested (20
account-disjoint instances), not of money-flow graphs. Its failure message says so: a reachable
flag-capable conflict is a **decision point** — the brake could then FLAG one story while an
equally sound witness supports another, and someone must decide how the brake and the
interrogation surface present that (tie-break rule? both?). Do not simply update the assertion.

### Narration DEFERRED to Item E — the overlap flagged before it was built
Item 4 shipped the citation-and-structure half of Item E and deferred prose entailment ("timing and
shell-company claims are assertions beyond the evidence"). "The agent narrates the traversal" IS
that deferred half: an LLM narration is a prose claim, and shipping it now means shipping an
unfaithfulness surface with no guard for it. Strict-schema + temperature-0 constrain the SHAPE of
output, not its entailment. What ships instead is the ordered, resolved, real-row traversal — a
client can render it as text by formatting column values, which asserts nothing beyond what the
rows literally contain and needs no faithfulness check. The word "narration" is deliberately absent
from the API so Item E's real narration does not arrive to find the name taken.

### Backend-only — and there was no HTTP surface at all
`app/routers/` was agents/beliefs/decisions/demo: the whole evidence layer was unreachable over
HTTP, so a frontend session had nothing to call. FRONTEND.md's endpoint table predates AML entirely
and its ladder ends at Phase 6 (done), so an AML console is a NEW surface needing its own
plan-gated session — the same way Items 0-4 were each backend-only. `GET /aml/transactions/{id}`
and `GET /aml/transactions/{id}/interrogate?as_of=` are read-only; `as_of` pins graph load AND row
resolution to ONE MVCC snapshot (the replay.py discipline), out-of-window/malformed -> 400.
**No POST, and deliberately NO route for `evaluate_transaction()`**: it makes a paid OpenAI call,
and a paid non-deterministic call behind a GET is a separate decision. It stays a callable.

### Still NO new table — and the argument is STRONGER than Item 4's
Item 4 deferred a `verdicts` table because a verdict is a pure function of (subject, corpus@T,
graph@T), all AOST-reproducible. That had one soft spot: the MODEL's claim is not reproducible
across model versions, temperature-0 notwithstanding. `interrogate_transaction()` sidesteps it
entirely by being **claim-free** — no model call at all — so it is a pure function of the graph,
free, and replayable offline. Nothing to persist.
**The precise trigger for a `verdicts` table, named so a later session recognises it: when Item F
must replay an identical MODEL CLAIM.** That is the one artifact this design cannot re-derive. Not
today.

### What a frontend session / Item F can call once this ships
- `GET /aml/transactions/{id}/interrogate` — real subject row, both accounts, all four structural
  verdicts with `flag_capable`, `competing_typologies` + `has_competing_structure` for a conflict
  badge, resolved `transactions`/`accounts` maps (no per-id round-trip), and an ordered CYCLE
  traversal whose shape maps straight onto the existing Trace animation idiom.
- `aml_interrogate.predecessor_of(witness, txn_id)` — the "trace ancestor" primitive.
- `aml_interrogate.interrogate_transaction()` — deterministic, zero API cost, offline-replayable
  click-through; pair it with `aml_agent.evaluate_transaction()` for the ONE LLM verdict in a demo.

### Mechanics / gotchas
- `_snapshot()` runs graph load AND row resolution inside ONE explicit txn after a single
  `SET TRANSACTION AS OF SYSTEM TIME`, so an interrogation can never mix a graph from one instant
  with rows from another. Reuses `time_travel.normalize_as_of` + `_AOST_RANGE_ERRORS`.
- `_build_witness` falls back to `BUNDLE` if `ring_order` returns None, so a hypothetical future
  witness constructor emitting an open path is mis-presented as nothing rather than as a path.
- `tests/test_aml_interrogate.py` is CI-SAFE: no OpenAI (retrieval uses a document's OWN stored
  embedding, the test_corpus trick), read-only, never calls `run_seed` — it cannot wipe the moat
  backfill.
- Response DTOs carry NO label field; `aml_interrogate` selects neither `is_laundering` nor
  pattern membership. Both remain test-only oracles.

### Explicitly NOT done (still gated): Item 6, Item E (explanation-faithfulness / LLM narration),
### Item 7 (headline eval), Item F (hero attack demo), the regulatory corpus (still gated on the
### data/raw/ drop), any frontend wiring, the decisions.aml_transaction_id grounding FK, a
### `verdicts` table, any change to the five tables / aml_* / typology_corpus.
### Do NOT start Item 6 / E / 7 / F without approval.

## Roadmap Item 6 — content-addressed pre-kill state + an earned certifier (2026-07-10)

Item 6 delivered: the certificate's `pre_invalidation_state` now CONTENT-ADDRESSES the pre-kill
world instead of merely counting it, and the certifier Lambda independently RE-DERIVES that
content address on AWS compute and reports whether it agrees. No migration, no new table, no
change to the five-table moat / `aml_*` / `typology_corpus`, no AML read, no LLM call anywhere on
the invalidation path. Certificate schema 1.0 -> 1.1, additive only.

### Two of Item 6's own phrases described already-solved problems. Investigated BEFORE building.
Same posture Item 5 took toward its roadmap wording, and it paid off the same way. The roadmap
text predates Items 1-5 and there is no ROADMAP.md in this repo, so intent was resolved against
the code.

- **"not a hardcoded-secret HMAC" is a STALE concern, not a gap.** `certificate.py`'s docstring
  has opened with `Integrity model (no HMAC, per plan)` since Phase 3. There is no `hmac` import,
  no secret, and no key material in `certificate.py`, `s3_audit.py`, or the Lambda handler.
  Confirmed by reading, not by trusting this file's Phase-3 note.
- **"lineage state exists, grounded evidence attached" means the belief_inheritance closure +
  Item 2's replay (reading a), NOT Items 4/5's AML evidence (reading b).** Reading (b) was
  considered seriously and REJECTED — see below. It is the more impressive-sounding reading and
  it is the one that would have failed inspection.

### Why reading (b) — "attach a real FLAG verdict to the certificate" — was rejected
The proposal: a real `evaluate_transaction()` FLAG (a genuine structural witness against a real
IBM AML transaction) should serve as external, grounded justification for invalidating the belief,
with the witness path and cited typology embedded in the certificate. It would be the first time a
certificate's "why" pointed at an independent real-world dataset rather than the project's own
internal `belief_performance` curve.

**It cannot be done honestly with what Items 1-5 built, because the belief has never touched an
AML transaction and no relation in the schema says it did.** Verified from source, not assumed:

- The one belief is `"merchant category 5411 under $180 is safe if account age > 6 months"`
  (`seed/seed.py:44`) — a CARD-AUTHORIZATION heuristic formed by crimson-0.
- The decisions it drove carry synthetic `txn_ref` values like `txn-w5-p0012`
  (`app/sim/transactions.py:147`), with NOT NULL `merchant` / `amount` card columns.
- The AML evidence layer is BANK-TO-BANK money flow: ACH / Cheque / Bitcoin transfers between
  `(bank, account)` pairs. No merchant, no MCC, no account age.
- **`decisions.aml_transaction_id` does not exist.** Migration 0004's header says so explicitly,
  and Items 1/3/4/5 each defer it. NOTES Item 1 calls it "the ONE real seam."

So no AML transaction was ever evaluated by, approved by, or measured against this belief. The
roadmap's phrase "the belief that approved similar transactions" presupposes an approval relation
that is not in the data. Embedding the (real, verified) FLAG exhibit
`3cda6d1d-f765-5001-9342-0478b1a92232` into a certificate for belief `898ad0e5-...` would produce a
document in which **every field is individually true and the juxtaposition is fabricated** — it
would assert that a six-hop cycle among bank accounts justifies invalidating a rule about merchant
category 5411. That is strictly worse than today's self-referential certificate, because it *looks*
externally grounded. The roadmap's own framing ("weaker projects overclaim here; you gain on
inspection") argues AGAINST it. Same forced-fit fabrication the Item-1 spike, Item 4's verdicts
table, and Item 5's no-table decision each refused.

### THE HONEST PATH TO A REAL (b) — for Items 7/F, so it is recognised and not reinvented
A version of (b) IS real. It is not a wiring job; it is a data-model job, and it walks the deferred
seam forward in this order:
1. Add the nullable `decisions.aml_transaction_id` FK (a FIVE-TABLE MOAT change — the first one
   since Phase 1; needs its own explicit reasoning, not a drive-by migration).
2. Seed a SECOND belief that is actually about laundering typologies. The Item-1 spike already
   drafts the sentence: *"cyclic flows returning to origin within N hops via shared intermediaries
   indicate laundering."* Inherit it down a bloodline the same way.
3. Have an agent apply THAT belief to real `aml_transactions` rows, writing `decisions` whose
   `is_fraud` is sourced from `aml_transactions.is_laundering` (real ground truth, not simulation).
4. Recompute `belief_performance` from those real outcomes. The staleness curve is then measured
   against a real labeled dataset.
5. ONLY THEN does a FLAG contradicting that belief constitute external, grounded justification, and
   only then may a certificate cite it.

Note what step 3 buys that nothing before it does: `decisions.verdict` is
`approve|decline|blocked` and `agent_id` is NOT NULL FK->agents, so a laundering decision needs a
real fleet agent and a verdict vocabulary that fits (Item 4 already measured this mismatch). Steps
1-4 are Items 7/F territory. Do NOT attempt them as part of a certificate change.

### What shipped instead: the pre-kill claim became reproducible, not just recorded
`pre_invalidation_state` carried two integers — `closure_edge_total` / `closure_edge_open` ("8 of 8
open"). Item 2 built `replay.closure_snapshot()`, which reconstructs the ENTIRE closure (belief row
+ every edge's revocation state) at an arbitrary MVCC instant and emits a canonical `content_hash`
proven byte-identical across independent reads. **The certificate did not carry that hash, and the
certifier re-verified counts, never the reconstructed world.** Now:

- The endpoint replays the closure `AS OF snapshot_hlc` post-commit and embeds
  `pre_invalidation_state.closure_content_hash`. Hash-covered like everything else.
- **Computed in the ROUTER, not inside `invalidate_belief`.** `snapshot_hlc` is captured on a
  separate connection strictly BEFORE the write txn opens, so the write txn's read snapshot is at
  or after it. Hashing what that txn sees would content-address a subtly different world than the
  one the Lambda replays at `snapshot_hlc`. The two must be the same world BY MVCC, not because
  nothing happened in between.
- Best-effort: a failed replay leaves the field null, and null is honest (the counts still stand,
  hash-covered). The invalidation is already durable; provenance enrichment must never 500 the one
  governed write after it has committed.
- The Lambda reconstructs the same world at the same instant with sync psycopg, hashes it with the
  SHARED canonicalizer, and stamps a hash-covered `closure_verification` block.

### Hash-coverage proves a document has not CHANGED. It can never prove the document was TRUE.
This is the answer to "does embedding the already-verified value suffice, since the hash covers
it?" — no, and it is the same question the brake answered once. `aml_graph.py` recomputes structure
from the unlabeled edge set and selects no label column, because trusting the label makes the
reasoning decorative. A certifier that embedded an app-computed closure hash and signed over it
would be attesting to the one claim it took on faith, inside a document whose entire reason to
exist is independent verification (its own docstring: "trusting CockroachDB's own history rather
than any in-memory result"). So it re-derives.

**Agreement is a TRI-STATE, never a silent pass:** `agreed` / `disagreed` / `unavailable`. The last
fires when the endpoint's certificate never reached S3 (`cert_status='failed'`) or predates schema
1.1 and carries no closure hash. A missing counterparty reading as success would be a lie by
omission — the same failure mode `INSUFFICIENT_COVERAGE` exists to prevent in the brake. Disagreement
is REPORTED, not raised: a certificate recording a mismatch is worth far more to an auditor than a
Lambda that crashed. `compared_against_source` is also stamped, because the Lambda overwrites
`audit_log.cert_s3_key`, so a SECOND certifier run compares against a previous LAMBDA certificate
rather than the endpoint's — still a real comparison, but the reader is told whose word was checked.

### The canonicalizer had to be SHARED, and that reverses an Item-2 decision on purpose
Item 2 deliberately kept `replay.py`'s digest local ("coupling them buys nothing"). Correct then;
its premise expired the moment something needed to compare two hashes. Two independently-implemented
canonicalizers that happen to agree today make the endpoint-vs-Lambda check a **false guarantee**
waiting to silently diverge — the check would keep passing while proving nothing. `canonical_json` /
`canonical_digest` / `closure_world` moved into `certificate.py` (already import-safe with zero app
deps, which is exactly why the Lambda can reach them). The Item-2 note is annotated in place, not
overwritten. `closure_world` also fixes the DICT SHAPE, so the two halves can differ only in what
they read from CockroachDB — which is precisely what the comparison is supposed to be testing.

### VERIFIED, not assumed: the async and sync halves hash identically
`scratchpad/probe_closure_hash_parity.py` ran BOTH reads against the live cluster — `replay.
closure_snapshot()` (async SQLAlchemy) vs the handler's `_BELIEF_SQL`/`_CLOSURE_SQL` through raw
sync psycopg — at current state AND at a past HLC via AOST. Identical digests, 9/9 closure nodes.
This was the real risk (timestamptz rendering, uuid casing, `depth` typing, row ordering) and it is
now measured rather than hoped. `tests/test_certifier_closure_verification.py` additionally asserts
the two halves' SELECTs project the same column sets, so a column added to one and forgotten in the
other fails loudly instead of producing a spurious `disagreed` on an honest invalidation.

### Q4 answered plainly: WHICH pre_state fields are now cross-checked, and which are NOT
The wrinkle "the Lambda's `pre_state` is never cross-checked against the endpoint's" is closed **for
the closure-state portion only, and only when a counterparty certificate exists.**
- **Now cross-checked:** the belief row (id, rule_text, status, originating_agent_id, formed_at,
  invalidated_at) and every closure edge's (depth, agent, generation, bloodline, status,
  from_agent_id, inherited_at, edge_invalidated_at) — everything `closure_world` covers, at
  `snapshot_hlc`, by two implementations reading independently.
- **Still NOT cross-checked:** `staleness_evidence` (BOTH sides read `belief_performance` at CURRENT
  committed state, not AOST — so neither is a check on the other), `issued_at`, `actor`, `source`,
  and the `affected_closure` agent/living counts (read at current state in the Lambda, in-txn in the
  endpoint). Do not describe Item 6 as "the certificate is fully independently verified."

### DEFERRED FINDING (do not rediscover): there is no single canonical certificate per invalidation
The endpoint and the Lambda each build and PUT their OWN certificate for the same event — different
`certificate_id`, different `issued_at`, `source='issue-time-read'` vs `'aost-replay'`, different S3
keys. Both objects persist; `audit_log.cert_status` / `content_hash` / `cert_s3_key` point at
whichever ran LAST. So "the certificate for invalidation X" is ambiguous, and a second certifier run
silently repoints the audit row at itself. Not fixed this session — content-addressing the closure
state was the scope, and reconciling the two-document model (one canonical cert with an appended
countersignature? the Lambda writing only a verification record?) is a real design decision, not a
cleanup. Flagged so Item 9's honesty ledger states it rather than a later session rediscovering it.

### DEFERRED FINDING: unkeyed sha256 proves INTEGRITY, never AUTHORSHIP
`content_hash` is an unkeyed digest. Anyone can forge a certificate wholesale and compute a perfectly
self-consistent hash for it; `verify()` returns True. What actually anchors authenticity is the
DATABASE — `audit_log.content_hash` holds the expected digest, and within the GC window the AOST
replay reproduces the claimed world from CRDB's own MVCC history. A forgery has neither.
**Residual gap:** an offline third party holding ONLY the JSON, with no cluster access, cannot verify
authorship. Asymmetric signing (the certifier signing the digest; the public half published) would
close it. **HMAC would NOT** — a shared secret lets the verifier forge too, which is precisely why
Phase 3 rejected it and why "not a hardcoded-secret HMAC" was never the real question. DOCUMENTED,
NOT BUILT: it needs a new AWS service (KMS), which CLAUDE.md forbids adding unasked, and it does not
advance this item's actual point. Recorded in `certificate.py`'s docstring too.

### Tripwire hole found and closed (shipped regardless of the (a)/(b) decision)
`tests/test_aml_routes.py`'s "no route invokes `evaluate_transaction()`" guard filtered routes on
`path.startswith("/aml")`. `POST /beliefs/{id}/invalidate` is not an /aml route — so the guard had a
hole exactly where reading (b) would have put a paid, non-deterministic OpenAI call: on the ONE
governed write. Now asserted STATICALLY over the whole `app` package (zero callers of
`evaluate_transaction` in application code, every branch, nothing executed) plus a namespace check on
every registered route's module. **Verified the guard actually trips** by temporarily importing the
function into `routers/beliefs.py` — it fails at the offending file:line. A guard that cannot fail is
theatre.

### If reading (b) is ever revisited: it must be the DETERMINISTIC witness, never the LLM verdict
Recorded because it inverts the obvious approach. `interrogate_transaction()` (Item 5) is
deterministic, free, and offline-replayable. `evaluate_transaction()`'s output embeds the MODEL's
claim — which NOTES Item 5 names as the one artifact this design cannot re-derive, and as the precise
trigger for a `verdicts` table. A certificate is a durable, hash-covered document someone is held to;
embedding a non-reproducible model claim in one would CREATE the persistence requirement this project
has now deliberately deferred twice. And per the section above, the certifier would then have to
re-derive the AML witness itself (porting the whole `aml_graph` witness machinery to sync psycopg in a
4.7 MB zip), because embedding an unverified witness is exactly the "trust the label" move the brake
refuses.

### DEPLOYED + VERIFIED END TO END ON REAL AWS (2026-07-10) — both tri-state branches
Local parity was not accepted as done, matching Phase 3's standard (a real invocation, not
"should work"). `build.py` -> `deploy.py` -> `scripts/demo_certifier.py`, which now runs BOTH
branches against the deployed `lineage-certifier`:
- **Scenario A** (invalidate via the SERVICE, so no certificate is ever written to S3):
  `closure_hash_agreement: "unavailable"`, `issue_time_closure_hash: null`. The Lambda still
  re-derived the world and said so. A missing counterparty does NOT read as a pass.
- **Scenario B** (invalidate via the real `POST /beliefs/{id}/invalidate`, which certifies):
  `closure_hash_agreement: "agreed"`, `aost_verified: true`. The endpoint's issue-time hash and
  the Lambda's AOST-replayed hash are the SAME value —
  `sha256:1e40b7a72fe1796cc91fa49bd119e1f239c889c651fc7dbaa70963eb38c393ff` — computed on
  different machines, in different languages' async/sync stacks, from different reads.
  `compared_against_source: "issue-time-read"`. Certificate re-fetched from S3 and its sha256
  re-verified locally.
- **The two scenarios produce the SAME closure hash at DIFFERENT snapshot HLCs.** Expected, not a
  bug: each scenario reseeds deterministically (uuid5 ids, fixed timestamps), so the reconstructed
  world genuinely is the same world. This is Item 2's claim restated — the hash covers the world,
  not the timestamp.

### GOTCHA — `deploy.py` had a latent CREATE-only-parameter bug, exposed by the FIRST re-deploy
`Architectures=["x86_64"]` is valid on `create_function` but `update_function_configuration`
rejects it outright (`ParamValidationError: Unknown parameter in input: "Architectures"`). Phase 3
only ever ran the CREATE path, so this sat dormant for months. It fires AFTER
`update_function_code` has already succeeded, so the failure leaves NEW CODE deployed against a
FAILED config call — a half-applied deploy that reports as an error. Fixed: `Architectures` moved
to a create-only dict, and a second `function_updated` waiter added after the config update.
Verified by a clean re-run (`state=Active last=Successful`).

### Mechanics / gotchas
- **A `disagreed` result is STRUCTURALLY RECORDED but NOT PROMINENTLY SURFACED. Do not overstate
  this.** It lives in the hash-covered certificate body (`closure_verification.agreement`) and in
  the Lambda's return payload (`closure_hash_agreement`) — an auditor must know to look. It is
  NOT in the endpoint's `InvalidateResponse` (the endpoint never sees the Lambda), and there is NO
  `audit_log` column for it. Worse: **the Lambda stamps `cert_status='written'` regardless**, so a
  certificate recording a mismatch is logged in the database as a clean write. `demo_certifier.py`
  now prints the agreement as a headline banner (`*** DISAGREED ***`) rather than one key among
  eight, which is the only surface where it is hard to miss. Properly fixing this means an
  `audit_log` column and/or reconciling the two-certificate model — see the deferred finding above;
  both were out of Item 6's scope.
- `SCHEMA_VERSION` 1.0 -> 1.1. Additive by construction: `_digest` hashes whatever keys are present
  and `verify()` re-derives over the same set, so 1.0 certificates still verify unchanged. This is
  why `build_certificate`'s `extra` (merged BEFORE hashing) was the right existing seam — the Lambda
  already used it for `aost_verification`; `closure_verification` rides the same mechanism.
- A `derived` pre_state (caller supplied none) sets `closure_content_hash: None` — present-and-null,
  never absent, so a consumer distinguishes "no hash" from "field missing" without knowing which
  caller built the document.
- `tests/test_certifier_closure_verification.py` loads the Lambda handler by path with `certificate`
  shimmed onto `app.services.certificate` (the zip packs it flat) and stubs boto3. ZERO AWS, ZERO
  cluster — CI-safe, and it never calls `run_seed`, so it cannot wipe the moat backfill.
- Redeploy with `python lambda/certifier/build.py && python lambda/certifier/deploy.py`, then
  `PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/demo_certifier.py`.
- `tests/test_atomic_invalidation.py` reseeds `defaultdb`, and `demo_certifier.py` reseeds AND runs
  two real invalidations (it also overwrites `belief_performance` with its own 2-window curve). Both
  wiped the 4000-row backfill this session; restored afterwards with `python -m
  seed.backfill_decisions` (via `.venv`), curve reproduced byte-for-byte.

### Explicitly NOT done (still gated): Item E (explanation-faithfulness / LLM narration), Item 7
### (headline eval), Item F (hero attack demo), the regulatory corpus (gated on the data/raw/ drop),
### any frontend wiring, the decisions.aml_transaction_id grounding FK, a second AML-typology belief,
### a `verdicts` table, asymmetric certificate signing, reconciling the two-certificate model, any
### change to the five tables / aml_* / typology_corpus.
### Do NOT start Item E / 7 / F without approval.
