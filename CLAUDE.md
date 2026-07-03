# CLAUDE.md — Lineage Backend

This file is the project constitution. Read it fully at the start of every session. It persists the decisions that must not drift.

## What this project is

**Lineage** is an agent-genealogy and belief-inheritance forensics system for fraud detection, built for the CockroachDB × AWS "Agentic Memory" hackathon (deadline: August 18, 2026).

The core idea: AI fraud-detection agents spawn, work, and die. When an agent dies, it passes its learned **beliefs** (rules like *"merchant category 5411 under $180 is safe if account age >6 months"*) down to the next generation. A living agent therefore acts on beliefs it **inherited from ancestors it never met**. A belief that was correct when a founding ancestor formed it can silently go **stale** across generations — until a living agent approves fraud because of a rule a long-dead agent created under conditions that no longer hold. The supervisor traces the bad decision back through the family tree to the origin belief, sees it was valid-then/rotten-now, and invalidates it atomically across the whole living fleet.

CockroachDB is the memory layer. Its unique capabilities — distributed vector indexing, `AS OF SYSTEM TIME` time-travel, and atomic cross-region transactions — are what make this project impossible to build convincingly on a normal Postgres+Pinecone stack. That is the whole competitive thesis.

## Stack (do not substitute without being asked)

- **Language:** Python 3.12
- **API:** FastAPI (async)
- **DB:** CockroachDB Cloud (Serverless free tier), Postgres-wire compatible
- **DB access:** SQLAlchemy 2.x (async) + psycopg 3
- **Agents (later phases):** OpenAI API. NOT Bedrock — Bedrock is not used in this project.
- **AWS:** S3 (audit certificates / belief snapshots) + Lambda (invalidation propagation / agent tick). These two satisfy the AWS requirement. Do not add other AWS services without being asked.
- **Migrations:** Alembic
- **Tests:** pytest

## The data model (the heart of the project)

Five core tables. This schema IS the competitive moat — treat it with care.

1. **agents** — id, generation (int), bloodline (str), status ('alive'|'dead'), spawned_at, retired_at (nullable), parent_id (FK to agents, nullable). The genealogy graph.
2. **beliefs** — id, rule_text, originating_agent_id (FK agents), formed_at, embedding (VECTOR — CockroachDB distributed vector index), status ('active'|'invalidated'), invalidated_at (nullable), invalidated_by (nullable).
3. **belief_inheritance** — id, belief_id (FK), from_agent_id (FK), to_agent_id (FK), inherited_at. The provenance graph traversed to build the trace path and to propagate invalidation atomically.
4. **decisions** — id, agent_id (FK), txn_ref, merchant, amount, verdict ('approve'|'decline'|'blocked'), driving_belief_id (FK beliefs, nullable), confidence, decided_at, is_fraud (bool). Links a bad call back to the belief that caused it.
5. **belief_performance** — id, belief_id (FK), window_start, window_end, confidence, false_positive_rate, frauds_approved (int). **This table makes staleness REAL.** A belief's measured performance is recorded over time windows so that "valid then, rotten now" is QUERIED FROM DATA.

## Non-negotiable rules

- **Staleness must be real, never hardcoded.** The "valid when formed / stale now" reveal must be computed from `belief_performance` rows, not faked. If you ever find yourself hardcoding a confidence drop, stop — that is a project-losing gimmick a technical judge will see through.
- **Time-travel must use real `AS OF SYSTEM TIME`.** The deposition endpoint queries CockroachDB's actual time-travel, not an application-level history table pretending to be one.
- **Invalidation must be atomic.** Invalidating a belief and its inherited closure happens in a single CockroachDB transaction, all holders at once. This is the CRDB kill-shot; a loop of individual updates defeats the entire point.
- **Every consequential action is audit-logged.** Who invalidated what, when. Certificates written to S3.
- **Paraphrase-free provenance:** every decision links to a real belief; every belief links to a real originating agent; every inheritance is a real row. No dangling references — the trace must always resolve to a real node.

## Phase discipline (critical — do not build ahead)

We are building in phases. **Do not implement future-phase work.** Ask before crossing a boundary.

- **Phase 1 (CURRENT) — the real spine.** Only: cluster connection, schema + migrations, a seeded genealogy (8 generations, 2 bloodlines, ~24 agents, real inheritance links), one belief formed by a founding ancestor and inherited down the full bloodline, and the TWO heart endpoints:
  - `GET /agents/{id}/beliefs?as_of={timestamp}` — real `AS OF SYSTEM TIME` deposition.
  - `GET /beliefs/{id}/lineage` — traverse `belief_inheritance` backward to the origin, return ordered path.
  Phase 1 is DONE when both endpoints return real data from a real cluster and a test proves the time-travel query returns different results before vs after a state change.
- **Phase 2 (LATER) — agents.** OpenAI/LangGraph fraud agent, lifecycle (spawn→inherit→act→retire), `belief_performance` populated from real outcomes. Do not start until Phase 1 is verified.
- **Phase 3 (DONE) — money-shots.** All 8 steps complete. Migration 0003 (closure state on belief_inheritance + audit_log); POST /beliefs/{id}/invalidate = one serializable CRDB txn closing the whole inherited closure at one commit; sha256 + AOST-reproducible S3 certificate carrying real belief_performance staleness evidence; measured atomic-vs-eventual consistency proof (strong 1 commit / 0 split samples vs eventual 9 commits / split window + a real leaked fraud approval); certifier Lambda (arn:...:role/lineage-certifier-role) that re-verifies the closure AS OF SYSTEM TIME on AWS compute and writes the cert to S3. 11 tests pass. See NOTES.md "Phase 3" for mechanics/gotchas. Do NOT start Phase 4 without approval.
- **Phase 4 (LATER) — hardening.** DTO validation, rate limiting, audit completeness, observability.
- **Phases 5–7 (LATER) — frontend, integration, 3-min video.**

## Working practices

- **Plan before building.** For each phase, produce a plan and wait for approval before writing code.
- **Commit after every working piece.** Schema migrates → commit. Seed works → commit. Endpoint returns real data → commit.
- **Write and run tests for the heart endpoints.** A passing test that proves time-travel works is the goal of Phase 1, not just code that runs.
- **Keep a scratch log.** Note what was tried and what failed in a `NOTES.md` so later sessions don't repeat dead ends.
- **Prefer transforms/opacity for any future UI animation** (not this phase, but noted so it persists).

## Environment

- Python 3.12, Node 24 / npm 11 available.
- Secrets (CockroachDB connection string, OpenAI key, AWS creds) live in a `.env` that is gitignored. Never commit secrets. Never print full secrets to logs.