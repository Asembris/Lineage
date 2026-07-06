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
- **OPERATIONAL RISK (address before any public/shared deployment):** this endpoint's blast
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
