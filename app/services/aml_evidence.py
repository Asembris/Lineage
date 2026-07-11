"""Pure evidence-rendering over the AML money-flow graph: the NEUTRAL structural summary the agent
retrieves against, and the bounded set of real edges it is allowed to cite.

Extracted from app/services/aml_agent.py so that PURE consumers can build the agent's exact
evidence representation WITHOUT importing the paid verdict path. The app-wide tripwire in
tests/test_aml_routes.py forbids any application module from importing `aml_agent` at all (it
carries `evaluate_transaction`, the one paid, non-deterministic OpenAI call), so the faithfulness
instrument (Item E) — which must render grounding identically to what the agent saw — reaches
these helpers here instead. aml_agent re-imports them, so its own callers and the scripts/tests
that import them from aml_agent are unaffected.

NO model, NO OpenAI, NO DB. Everything here is a pure function of a Graph and an Edge. See
NOTES.md "Roadmap Item 4" (where these were first written) and "Roadmap Item E" (why they moved).
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from app.services.aml_graph import MAX_CYCLE_HOPS, Edge, Graph

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
