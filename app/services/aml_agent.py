"""The grounded AML agent (Roadmap Item 4): retrieval-cited verdicts with a strict brake.

This is a new reasoning path over new data (aml_transactions + typology_corpus), NOT a second
LLM-calling pattern. It reuses the Phase-2 convention proven in app/services/agent_brain.py:
gpt-4o-mini, `strict` json_schema response_format, temperature 0, ground truth never in the
prompt, and the model's cited ids validated against real rows before anything is trusted.
agent_brain validates one belief id; here the same discipline generalizes to a typology plus a
whole evidence path (app/services/verdict_guard.py).

The pipeline: summarize the subject transaction's neighborhood as NEUTRAL graph facts ->
embed that summary -> retrieve typology definitions by CockroachDB cosine search (Item 3) ->
one strict-schema call that must cite a typology and the real transaction ids supporting it ->
the brake decides.

Two measured facts shaped this and are worth not rediscovering:

1. You cannot retrieve a typology by embedding a raw transaction row. A bare row ("Transfer of
   14552.29 USD by ACH from...") retrieves CYCLE at cosine distance 0.680, and an off-topic
   chargeback complaint retrieves CYCLE at 0.743 — both are just "nearest of four distant
   things". The query must describe the STRUCTURE around the transaction.
2. The structural summary names no typology and uses no laundering vocabulary. It reports
   degrees, path lengths and shared destinations. Mapping those facts onto a definition is the
   reasoning we are actually testing; handing the model the word "cycle" would test nothing.

Nothing here is persisted. A verdict is a pure function of the subject, the corpus at time T,
and the graph at time T, and this project can already reproduce all three at a past instant via
AS OF SYSTEM TIME — a derivable, reproducible result does not need storing to be trustworthy.
Persistence becomes necessary when a verdict is a consequential act someone is held to, which
is Item 5's call, not this one. See NOTES.md "Roadmap Item 4".
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict

from sqlalchemy import text

from app.config import get_settings
from app.corpus_models import SOURCE
from app.db import engine
from app.services.aml_graph import MAX_CYCLE_HOPS, Edge, Graph, load_graph
from app.services.corpus import retrieve_typology
from app.services.embeddings import embed_text
from app.services.openai_client import get_openai
from app.services.verdict_guard import Claim, VerdictOutcome, evaluate_claim

# k=3 against a 4-document corpus. At k=4 the "retrieved set" IS the corpus and the brake's
# hallucinated-citation gate could never fire. Three of four leaves one genuinely excluded.
RETRIEVAL_K = 3

# DERIVED, not tuned. The model can only cite edges we show it, so the neighbourhood must cover
# every edge the witness search itself can reach. A cycle of length L has its far side at
# ceil(L/2) undirected steps from the subject, and the search is bounded by MAX_CYCLE_HOPS, so
# that radius is exactly what is required. (SCATTER-GATHER's witness lives within 2 steps.)
# tests/test_aml_brake.py::test_neighbourhood_contains_every_flag_capable_witness holds this to
# account: it asserts every real witness in the extract is fully citable from the prompt.
NEIGHBOURHOOD_HOPS = -(-MAX_CYCLE_HOPS // 2)  # ceil(12 / 2) = 6

# A prompt-size cap, and the ONE genuinely unprincipled constant here. Today the largest
# neighbourhood hits it exactly (120) and no witness is lost, because edges are ordered by
# distance from the subject so truncation drops the most distant distractors first. In a denser
# graph it could drop a witness edge and the brake would reject a true cycle as an unfaithful
# citation. The containment test above is what would catch that; it is not prevented by design.
NEIGHBOURHOOD_LIMIT = 120

_CLAIM_SCHEMA = {
    "name": "aml_typology_claim",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            # Free-form string, NOT an enum of the retrieved typologies: constraining it here
            # would make the brake's hallucinated-citation gate unfalsifiable. We want to be
            # able to catch the model citing something retrieval never returned.
            "typology": {
                "type": "string",
                "description": "exact typology string copied from one candidate definition",
            },
            "evidence_txn_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ids of the transactions that together form the claimed structure",
            },
            "rationale": {
                "type": "string",
                "description": "why the cited transactions form that structure",
            },
        },
        "required": ["typology", "evidence_txn_ids", "rationale"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are an anti-money-laundering analyst. You are given neutral structural facts about "
    "one transaction's neighbourhood in a money-flow graph, the transactions in that "
    "neighbourhood, and candidate laundering typology definitions retrieved from a reference "
    "corpus. Decide which candidate typology, if any, the structure matches.\n\n"
    "Rules you must follow:\n"
    "- Set `typology` to the bare value of one candidate's `typology:` field, e.g. CYCLE. "
    "Do not include the title, the definition text, or any brackets.\n"
    "- Cite in evidence_txn_ids the exact transaction ids that together form that structure, "
    "including the subject transaction. Cite only ids listed in the neighbourhood. Cite NOTHING "
    "else: a transaction that merely touches one of the accounts, without being a step of the "
    "structure itself, must be left out. A cited path with one extra transaction in it is "
    "rejected exactly like a fabricated one.\n"
    "- evidence_txn_ids must contain ONE id per transaction in the structure. If the facts say "
    "the funds return to the source after N transfers, a CYCLE claim needs exactly N ids. Do "
    "not summarise the path in the rationale and then cite only a couple of ids.\n"
    "- If you claim CYCLE: list the transactions in order, starting with the subject. Each "
    "transaction must begin at the account where the previous one ended, and the last must end "
    "at the account the subject started from. No account may appear twice as a sender. Work out "
    "the hop sequence account by account, then write down the id of the transaction for each "
    "hop.\n"
    "- If you claim SCATTER-GATHER: cite the transactions from the one source account to each "
    "intermediary, and the transactions from those same intermediaries into the one common "
    "destination account.\n"
    "- If the facts do not support any candidate, say so in the rationale and cite no "
    "evidence. Never assert a structure you cannot point at.\n"
    "- Your rationale must refer only to the cited transactions and the stated facts."
)

_SUBJECT_SQL = text(
    """
    SELECT t.id, fa.bank AS from_bank, fa.account AS from_account,
           ta.bank AS to_bank, ta.account AS to_account
    FROM aml_transactions t
    JOIN aml_accounts fa ON fa.id = t.from_account_id
    JOIN aml_accounts ta ON ta.id = t.to_account_id
    WHERE t.id = :tid
    """
)


def _frag(u: uuid.UUID) -> str:
    return str(u)[:6]


def _return_path_hops(g: Graph, e: Edge) -> int | None:
    """Shortest directed path from the destination back to the source, if any within budget.
    Reported as a bare hop count — the summary never calls this a cycle."""
    if e.is_self_loop:
        return None
    frontier, seen = {e.dst}, {e.dst}
    for hop in range(1, MAX_CYCLE_HOPS + 1):
        nxt = set()
        for a in frontier:
            for b in g.succ(a):
                if b == e.src:
                    return hop + 1
                if b not in seen:
                    seen.add(b)
                    nxt.add(b)
        frontier = nxt
        if not frontier:
            break
    return None


def structure_text(g: Graph, e: Edge) -> str:
    """A NEUTRAL rendering of the subject's neighbourhood: degrees, path lengths, shared
    destinations. No typology names, no laundering vocabulary — the model must do the mapping.
    This string is both the retrieval query and part of the prompt."""
    src_out = {x.dst for x in g.out_edges.get(e.src, [])}
    src_in = {x.src for x in g.in_edges.get(e.src, [])}
    dst_out = {x.dst for x in g.out_edges.get(e.dst, [])}
    dst_in = {x.src for x in g.in_edges.get(e.dst, [])}

    shared: dict[uuid.UUID, int] = defaultdict(int)
    for m in src_out:
        for d in g.succ(m):
            if d != e.src:
                shared[d] += 1
    best_shared = max(shared.values(), default=0)

    hops = _return_path_hops(g, e)
    lines = [
        f"A transfer of {e.amount} by {e.payment_format} moves funds from a source account to a "
        f"destination account.",
        f"The source account sends funds to {len(src_out)} distinct account(s) and receives from "
        f"{len(src_in)} distinct account(s).",
        f"The destination account sends funds to {len(dst_out)} distinct account(s) and receives "
        f"from {len(dst_in)} distinct account(s).",
        (
            f"Following transfers onward from the destination account, the funds can reach the "
            f"original source account again after {hops} transfers."
            if hops is not None
            else "Following transfers onward from the destination account, the funds do not reach "
            "the original source account within the search budget."
        ),
        (
            f"Of the accounts the source sends to, at most {best_shared} of them send funds onward "
            f"to one and the same further account."
            if best_shared
            else "The accounts the source sends to have no further account in common."
        ),
    ]
    if e.is_self_loop:
        lines.append("The source and destination account are the same account.")
    if not dst_out:
        lines.append("No onward transfers from the destination account are recorded in this dataset.")
    return " ".join(lines)


def neighbourhood(
    g: Graph, e: Edge, hops: int = NEIGHBOURHOOD_HOPS, limit: int = NEIGHBOURHOOD_LIMIT
) -> list[Edge]:
    """The bounded set of real edges the model is allowed to cite.

    `hops` must cover the SEARCHABLE region, not just the immediate surroundings: the far side
    of a 10-edge cycle sits 5 steps from the subject, so a 2-hop neighbourhood physically cannot
    contain the path we then ask the model to cite. An earlier version did exactly that and the
    validator dutifully rejected every cycle claim as unfaithful — the model was being asked to
    cite edges it had never been shown. The radius is now derived from MAX_CYCLE_HOPS rather
    than tuned to whatever made one demo pass.

    Handing over the searchable region is not handing over the answer: the cycle's edges arrive
    mixed in with every distractor within the same radius, and the model must find the path.
    Edges are ordered by distance from the subject, then by id, so truncation at `limit` drops
    the most distant distractors first rather than silently dropping the path.
    """
    depth = {e.src: 0, e.dst: 0}
    frontier = {e.src, e.dst}
    for d in range(1, hops + 1):
        nxt = set()
        for a in frontier:
            for x in g.out_edges.get(a, []):
                nxt.add(x.dst)
            for x in g.in_edges.get(a, []):
                nxt.add(x.src)
        nxt -= depth.keys()
        for a in nxt:
            depth[a] = d
        frontier = nxt
        if not frontier:
            break

    edges = {
        x.id: x
        for a in depth
        for x in (*g.out_edges.get(a, []), *g.in_edges.get(a, []))
        if x.src in depth and x.dst in depth
    }
    ordered = sorted(
        edges.values(),
        key=lambda x: (0 if x.id == e.id else max(depth[x.src], depth[x.dst]), str(x.id)),
    )
    return ordered[:limit]


async def _subject_edge(txn_id: uuid.UUID, g: Graph) -> Edge:
    edge = g.by_id.get(txn_id)
    if edge is None:
        raise LookupError(f"transaction {txn_id} is not in the AML evidence layer")
    return edge


async def claim_for_transaction(txn_id: uuid.UUID, *, as_of: str | None = None) -> tuple:
    """Run retrieval + the model. Returns (subject, graph, retrieved, claim, summary)."""
    g = await load_graph()
    subject = await _subject_edge(txn_id, g)
    summary = structure_text(g, subject)

    query_vec = await embed_text(summary)
    retrieved = await retrieve_typology(query_vec, k=RETRIEVAL_K, as_of=as_of, source=SOURCE)

    local = neighbourhood(g, subject)
    edge_lines = "\n".join(
        f"  - id={x.id}: {_frag(x.src)} -> {_frag(x.dst)} ({x.amount} {x.payment_format})"
        for x in local
    )
    defs = "\n\n".join(
        f"  typology: {r['typology']}\n  title: {r['title']}\n  definition: {r['body']}"
        for r in retrieved
    )
    user_msg = (
        f"Subject transaction: id={subject.id} ({_frag(subject.src)} -> {_frag(subject.dst)})\n\n"
        f"Structural facts about its neighbourhood:\n{summary}\n\n"
        f"Transactions in the neighbourhood (cite only these ids):\n{edge_lines}\n\n"
        f"Candidate typology definitions:\n{defs}\n\n"
        f"Return your claim."
    )

    resp = await get_openai().chat.completions.create(
        model=get_settings().chat_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_schema", "json_schema": _CLAIM_SCHEMA},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content)

    ids: list[uuid.UUID] = []
    for raw in data["evidence_txn_ids"]:
        try:
            ids.append(uuid.UUID(raw))
        except (ValueError, AttributeError):
            # A malformed id is a fabricated citation; keep it out of the UUID list but let the
            # validator see the path is short/wrong rather than crashing on the model's output.
            continue
    claim = Claim(typology=data["typology"], evidence_txn_ids=ids, rationale=data.get("rationale", ""))
    return subject, g, retrieved, claim, summary


async def evaluate_transaction(txn_id: uuid.UUID, *, as_of: str | None = None) -> VerdictOutcome:
    """The Item-5 entry point. `as_of` time-travels the corpus retrieval with real AOST."""
    subject, g, retrieved, claim, _ = await claim_for_transaction(txn_id, as_of=as_of)
    return evaluate_claim(subject=subject, graph=g, retrieved=retrieved, claim=claim)


async def describe_transaction(txn_id: uuid.UUID) -> dict:
    """Human-readable identity of a subject transaction (for demos / Item 5), no labels."""
    async with engine.connect() as c:
        row = (await c.execute(_SUBJECT_SQL, {"tid": txn_id})).mappings().first()
    return dict(row) if row else {}
