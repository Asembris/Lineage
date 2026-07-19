# MCP Session Evidence — CockroachDB Cloud Managed MCP Server

**Captured:** 2026-07-19T20:48:48Z (UTC)
**Server:** `cockroachdb-cloud` — `https://cockroachlabs.cloud/mcp` (HTTP transport, OAuth in-session)
**Cluster:** `<YOUR_CLUSTER_ID>` (sanitized; supplied via the `mcp-cluster-id` header — see [.mcp.json.example](.mcp.json.example))
**Database:** `defaultdb`
**Mode:** READ-ONLY by design. No write tool was invoked in this session.

This file records a real, verbatim exercise of the CockroachDB Cloud **Managed MCP Server** against
the live Lineage memory cluster. Everything below is the actual tool-call shape and the actual
response payload. Nothing is paraphrased or reconstructed.

---

## 1. Tool surface

The connected server exposes **12 tools**:

| Tool | Kind |
|---|---|
| `select_query` | read (SELECT only) |
| `explain_query` | read (plan only, does not execute) |
| `get_table_schema` | read |
| `list_databases` | read |
| `list_tables` | read |
| `list_clusters` | read |
| `get_cluster` | read |
| `show_running_queries` | read |
| `show_statement` | read |
| `create_database` | **write — not invoked** |
| `create_table` | **write — not invoked** |
| `insert_rows` | **write — not invoked** |

`select_query` is the read-only SQL tool and is the one used for the substantive reads below. Its own
declared contract: *"Execute a read-only SELECT query on the CockroachDB cluster. Automatically adds
LIMIT 25 if not specified. Maximum LIMIT is 10000."* The three mutating tools were deliberately never
called — this integration reads the memory system, it does not modify it.

---

## 2. READ A — belief `898ad0e5…`'s inheritance closure + living-holder flags

**Tool:** `select_query`
**Argument shape:**

```json
{
  "database": "defaultdb",
  "query": "<SQL below>"
}
```

**SQL (sanitized — no credentials present):**

```sql
WITH b AS (SELECT id, status, originating_agent_id FROM defaultdb.public.beliefs
           WHERE id = '898ad0e5-b4f8-5863-abe3-4145c9b5af68')
SELECT a.id AS agent_id, a.bloodline, a.generation, a.status AS agent_status,
       (a.id = b.originating_agent_id) AS is_origin, (a.status = 'alive') AS is_living_holder
FROM defaultdb.public.agents a, b
WHERE a.id = b.originating_agent_id
   OR a.id IN (SELECT to_agent_id FROM defaultdb.public.belief_inheritance WHERE belief_id = b.id)
ORDER BY a.generation, a.id
```

**Verbatim response:**

```json
{"rows":[{"agent_id":"108cf7f4-cb0e-59dd-9654-a85a5bcd3127","bloodline":"crimson","generation":0,"agent_status":"dead","is_origin":true,"is_living_holder":false},{"agent_id":"43c136ca-734c-5468-a797-498ce101b523","bloodline":"crimson","generation":1,"agent_status":"dead","is_origin":false,"is_living_holder":false},{"agent_id":"82c31ac4-b4a8-54f5-a3f1-12c910ecb2bf","bloodline":"crimson","generation":2,"agent_status":"dead","is_origin":false,"is_living_holder":false},{"agent_id":"d4a8fbc2-e560-584c-bca6-6ed8d60aca69","bloodline":"crimson","generation":3,"agent_status":"dead","is_origin":false,"is_living_holder":false},{"agent_id":"0b319b84-5f37-576a-bd3c-9a6688d5081f","bloodline":"crimson","generation":4,"agent_status":"dead","is_origin":false,"is_living_holder":false},{"agent_id":"cd75b330-a6cd-5bd4-b20e-c2a5b105f1f8","bloodline":"crimson","generation":5,"agent_status":"alive","is_origin":false,"is_living_holder":true},{"agent_id":"d3e2c4d5-da1a-512d-9b8f-960da0d25804","bloodline":"crimson","generation":5,"agent_status":"dead","is_origin":false,"is_living_holder":false},{"agent_id":"f8a740d5-8995-5d01-8815-8c140642226e","bloodline":"crimson","generation":6,"agent_status":"dead","is_origin":false,"is_living_holder":false},{"agent_id":"3fb55cf8-a1d4-597f-9de8-c8c54d4b3b14","bloodline":"crimson","generation":7,"agent_status":"alive","is_origin":false,"is_living_holder":true}]}
```

**Reading:** 9 agents hold this belief — 1 origin (`108cf7f4…`, generation 0, dead) plus 8 inheritors
spanning generations 1–7 of the `crimson` bloodline. The bloodline forks at generation 5
(`cd75b330…` and `d3e2c4d5…` share parent `0b319b84…`). Exactly **2 are alive**: `cd75b330…` (gen 5)
and `3fb55cf8…` (gen 7). Those two are the living blast radius of an invalidation.

---

## 3. READ B — one-line closure proof

**Tool:** `select_query`
**Argument shape:** `{"database": "defaultdb", "query": "<SQL below>"}`

**SQL:**

```sql
SELECT
 (SELECT status FROM defaultdb.public.beliefs WHERE id='898ad0e5-b4f8-5863-abe3-4145c9b5af68') AS belief_status,
 (SELECT count(*) FROM defaultdb.public.belief_inheritance WHERE belief_id='898ad0e5-b4f8-5863-abe3-4145c9b5af68') AS closure_edges,
 (SELECT count(*) FROM defaultdb.public.belief_inheritance WHERE belief_id='898ad0e5-b4f8-5863-abe3-4145c9b5af68' AND invalidated_at IS NULL) AS open_edges,
 (SELECT count(*) FROM defaultdb.public.agents a WHERE a.status='alive'
    AND a.id IN (SELECT to_agent_id FROM defaultdb.public.belief_inheritance WHERE belief_id='898ad0e5-b4f8-5863-abe3-4145c9b5af68')) AS living_holders
```

**Verbatim response:**

```json
{"rows":[{"belief_status":"active","closure_edges":8,"open_edges":8,"living_holders":2}]}
```

**Reading:** the belief is `active`; its inheritance closure is **8 edges**, all **8 still open**
(`invalidated_at IS NULL`); **2 living holders**. 8 edges + 1 origin node = the 9 agents READ A
returned — the two reads are internally consistent.

---

## 4. Schema verification (`get_table_schema`)

Column names were verified against the live cluster *before* the reads were run, rather than guessed.

**Tool:** `get_table_schema`, argument shape `{"database": "defaultdb", "table": "<name>"}`.

**Verbatim response — `belief_inheritance`:**

```json
{"rows":[{"table_name":"defaultdb.public.belief_inheritance","create_statement":"CREATE TABLE public.belief_inheritance (\n\tid UUID NOT NULL DEFAULT gen_random_uuid(),\n\tbelief_id UUID NOT NULL,\n\tfrom_agent_id UUID NOT NULL,\n\tto_agent_id UUID NOT NULL,\n\tinherited_at TIMESTAMPTZ NOT NULL,\n\tinvalidated_at TIMESTAMPTZ NULL,\n\tinvalidated_by UUID NULL,\n\tCONSTRAINT belief_inheritance_pkey PRIMARY KEY (id ASC),\n\tCONSTRAINT belief_inheritance_belief_id_fkey FOREIGN KEY (belief_id) REFERENCES public.beliefs(id),\n\tCONSTRAINT belief_inheritance_from_agent_id_fkey FOREIGN KEY (from_agent_id) REFERENCES public.agents(id),\n\tCONSTRAINT belief_inheritance_to_agent_id_fkey FOREIGN KEY (to_agent_id) REFERENCES public.agents(id),\n\tINDEX ix_bi_to_agent (to_agent_id ASC),\n\tINDEX ix_bi_belief (belief_id ASC)\n);"}]}
```

**Verbatim response — `beliefs`:**

```json
{"rows":[{"table_name":"defaultdb.public.beliefs","create_statement":"CREATE TABLE public.beliefs (\n\tid UUID NOT NULL DEFAULT gen_random_uuid(),\n\trule_text STRING NOT NULL,\n\toriginating_agent_id UUID NOT NULL,\n\tformed_at TIMESTAMPTZ NOT NULL,\n\tembedding VECTOR(1536) NULL,\n\tstatus STRING NOT NULL DEFAULT 'active':::STRING,\n\tinvalidated_at TIMESTAMPTZ NULL,\n\tinvalidated_by UUID NULL,\n\tCONSTRAINT beliefs_pkey PRIMARY KEY (id ASC),\n\tCONSTRAINT beliefs_originating_agent_id_fkey FOREIGN KEY (originating_agent_id) REFERENCES public.agents(id),\n\tINDEX ix_beliefs_originating_agent (originating_agent_id ASC)\n);"}]}
```

**Verbatim response — `agents`:**

```json
{"rows":[{"table_name":"defaultdb.public.agents","create_statement":"CREATE TABLE public.agents (\n\tid UUID NOT NULL DEFAULT gen_random_uuid(),\n\tgeneration INT8 NOT NULL,\n\tbloodline STRING NOT NULL,\n\tstatus STRING NOT NULL,\n\tspawned_at TIMESTAMPTZ NOT NULL,\n\tretired_at TIMESTAMPTZ NULL,\n\tparent_id UUID NULL,\n\tCONSTRAINT agents_pkey PRIMARY KEY (id ASC),\n\tCONSTRAINT agents_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.agents(id),\n\tINDEX ix_agents_bloodline_gen (bloodline ASC, generation ASC)\n);"}]}
```

Note the MCP server reports `embedding VECTOR(1536)` on `beliefs` — the distributed vector column — and
the closure-state columns (`invalidated_at` / `invalidated_by`) that migration 0003 added to
`belief_inheritance`.

---

## 5. Query plan (`explain_query`)

**Tool:** `explain_query`, argument shape `{"database": "defaultdb", "query": "<READ A SQL>"}`.
Returns the plan without executing.

**Verbatim response:**

```json
{"rows":[{"info":"distribution: local"},{"info":""},{"info":"• sort"},{"info":"│ estimated row count: 8"},{"info":"│ order: +generation,+id"},{"info":"│"},{"info":"└── • render"},{"info":"    │"},{"info":"    └── • filter"},{"info":"        │ estimated row count: 8"},{"info":"        │ filter: (id = any_not_null) OR CASE WHEN bool_or AND (id IS NOT NULL) THEN true WHEN bool_or IS NULL THEN false ELSE CAST(NULL AS BOOL) END"},{"info":"        │"},{"info":"        └── • group (hash)"},{"info":"            │ estimated row count: 24"},{"info":"            │ group by: id"},{"info":"            │"},{"info":"            └── • render"},{"info":"                │"},{"info":"                └── • hash join (left outer)"},{"info":"                    │ estimated row count: 24"},{"info":"                    │ equality: (id, id) = (belief_id, to_agent_id)"},{"info":"                    │ left cols are key"},{"info":"                    │"},{"info":"                    ├── • cross join"},{"info":"                    │   │ estimated row count: 24"},{"info":"                    │   │"},{"info":"                    │   ├── • scan"},{"info":"                    │   │     estimated row count: 24 (100% of the table; stats collected 8 hours ago; using stats forecast for 11 hours in the future)"},{"info":"                    │   │     table: agents@agents_pkey"},{"info":"                    │   │     spans: FULL SCAN"},{"info":"                    │   │"},{"info":"                    │   └── • scan"},{"info":"                    │         estimated row count: 1 (50% of the table; stats collected 8 hours ago; using stats forecast for 18 hours in the future)"},{"info":"                    │         table: beliefs@beliefs_pkey"},{"info":"                    │         spans: [/'898ad0e5-b4f8-5863-abe3-4145c9b5af68' - /'898ad0e5-b4f8-5863-abe3-4145c9b5af68']"},{"info":"                    │"},{"info":"                    └── • render"},{"info":"                        │"},{"info":"                        └── • scan"},{"info":"                              estimated row count: 15 (100% of the table; stats collected 8 hours ago; using stats forecast for 11 hours in the future)"},{"info":"                              table: belief_inheritance@belief_inheritance_pkey"},{"info":"                              spans: FULL SCAN"}]}
```

Two things worth noting honestly from the real plan:

- The point-lookup on `beliefs` uses `beliefs_pkey` with a tight single-key span, as expected.
- The `belief_inheritance` access is a **FULL SCAN**, not a seek on `ix_bi_belief`. At 15 rows the
  optimizer correctly judges the scan cheaper than an index lookup, so this is not a defect at
  current scale — but it is a real observation from the plan, not a claim that the query is optimal.
  The `estimated row count: 8` on the sort matches the 8-edge closure READ B measured.

---

## 6. Audit / identity metadata returned

**None.** The `select_query`, `get_table_schema`, and `explain_query` responses were bare
`{"rows":[...]}` payloads with no identity, session, request-id, or audit envelope in the tool result.

Stating that plainly rather than implying otherwise: the CockroachDB Cloud MCP server is
OAuth-authenticated (the session authenticated before any tool could run) and Cockroach Labs
operates server-side audit logging for the Managed MCP Server, but **no audit trail was echoed back
to the client in these responses**, so this file cannot present one as captured evidence. The
client-side evidence of identity is the successful OAuth-gated connection itself plus the fact that
the reads returned this cluster's real rows.

---

## 7. Cross-check vs. the system's own REST API

The same belief was independently read through Lineage's own endpoint,
`GET /beliefs/898ad0e5-b4f8-5863-abe3-4145c9b5af68/lineage`, on the running FastAPI app.

**Verdict: AGREE — exactly, on every field compared.**

| Quantity | MCP `select_query` | REST `/lineage` | Match |
|---|---|---|---|
| Agents in closure | 9 | 9 (`path` length) | ✅ |
| Origin agent | `108cf7f4-cb0e-59dd-9654-a85a5bcd3127` | `108cf7f4-cb0e-59dd-9654-a85a5bcd3127` | ✅ |
| Living holders | 2 | 2 (`cd75b330…`, `3fb55cf8…`) | ✅ |
| Belief status | `active` | `active` | ✅ |
| Closure edges | 8 | 8 (9 path nodes − origin) | ✅ |
| Generation span | 0 → 7 | 0 → 7 | ✅ |
| Gen-5 fork | `cd75b330…` + `d3e2c4d5…` | `cd75b330…` + `d3e2c4d5…` (both `depth: 5`) | ✅ |

The agent id list and per-agent `alive`/`dead` status match one-for-one across all 9 nodes. The REST
response additionally carries the belief's `rule_text`
(*"merchant category 5411 under $180 is safe if account age > 6 months"*), `formed_at`
(`2024-05-12T00:00:00Z`), and per-edge `inherited_at` timestamps, which the MCP reads did not select.

Two independent paths — the Managed MCP Server going straight at the cluster, and the application's
own SQLAlchemy-backed traversal — return the same memory state. No drift.

---

## 8. Sanitization

- No credentials, connection strings, or tokens appear in this file.
- The cluster id is replaced with `<YOUR_CLUSTER_ID>`, matching the convention in
  [.mcp.json.example](.mcp.json.example). The MCP endpoint authenticates via in-session OAuth; there
  is no stored secret to redact.
- Agent and belief UUIDs are seeded synthetic fixture data, not production identifiers.
- No write tool (`insert_rows`, `create_table`, `create_database`) was invoked. The cluster state
  after this session is byte-identical to the state before it.
