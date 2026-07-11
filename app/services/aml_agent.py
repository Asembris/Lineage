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

from sqlalchemy import text

from app.config import get_settings
from app.corpus_models import SOURCE
from app.db import engine
from app.services.aml_graph import Edge, Graph, load_graph
# The neutral structural summary + citable neighbourhood are pure and live in aml_evidence, so
# pure consumers (the Item-E faithfulness instrument) can reach them without importing this paid
# module. Re-imported here so this module and its existing importers/scripts are unchanged.
from app.services.aml_evidence import _frag, neighbourhood, structure_text
from app.services.corpus import retrieve_typology
from app.services.embeddings import embed_text
from app.services.openai_client import get_openai
from app.services.verdict_guard import Claim, VerdictOutcome, evaluate_claim

# k=3 against a 4-document corpus. At k=4 the "retrieved set" IS the corpus and the brake's
# hallucinated-citation gate could never fire. Three of four leaves one genuinely excluded.
RETRIEVAL_K = 3

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
