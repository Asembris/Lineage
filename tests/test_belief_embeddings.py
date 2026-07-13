"""THE SEEDED BELIEF VECTORS ARE REAL, AND A RESEED CANNOT UNDO THAT.

The bug this pins is not hypothetical and it is not old. Until 2026-07-13 `seed.seed()` planted a
deterministic PLACEHOLDER into every belief, and the fix was an INSTRUCTION: "run
`scripts/embed_beliefs.py` afterwards". Every reseed — every CI run, every test that calls
`run_seed()`, every backfill — silently re-planted the placeholder and discarded it.

It failed exactly the way an instruction fails. Measured on the live cluster on 2026-07-13, BOTH
beliefs sat at cosine distance **0.000000000** from `placeholder_embedding(1536)` while the honesty
ledger — the one surface whose entire job is to be trustworthy — asserted in static prose that the
azure belief carried a real vector. Nobody was careless; the procedure was simply one command
longer than a human reliably executes.

So the seed plants the real vector now, from a committed fixture, and these tests make the
regression UNREPRESENTABLE rather than documented. That is this project's house move (ARCHITECTURE
section 7), and it is the eighth time it has been applied.

Hermetic except for the last test, which reseeds the live cluster on purpose — because the claim
under test is precisely "a RESEED cannot undo it", and only a reseed can prove that.
"""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from seed.seed import (
    _FIXTURE,
    AML_RULE_TEXT,
    RULE_TEXT,
    belief_embedding,
    bid,
    placeholder_embedding,
)
from seed.seed import seed as run_seed

SEEDED = {"origin": RULE_TEXT, "aml-cycle": AML_RULE_TEXT}


def _cos_dist(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return 1.0 - float(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b))


def test_the_fixture_covers_every_seeded_belief_and_records_the_text_it_describes():
    """A vector with no record of what it embeds is a number nobody can check."""
    assert _FIXTURE.exists(), f"{_FIXTURE} is missing — python -m scripts.embed_beliefs --refresh-fixture"
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    dim = get_settings().embedding_dim

    assert data["model"] == get_settings().embedding_model
    assert data["dim"] == dim
    for name, rule_text in SEEDED.items():
        entry = data["beliefs"][name]
        assert entry["rule_text"] == rule_text, (
            f"the committed vector for {name!r} was computed from different words than the seed "
            f"plants — it does not describe the rule"
        )
        assert len(entry["embedding"]) == dim


@pytest.mark.parametrize("name", sorted(SEEDED))
def test_the_seed_plants_a_real_vector_not_the_placeholder(name):
    """THE REGRESSION GUARD. Revert seed.seed() to placeholder_embedding() and this fails."""
    dim = get_settings().embedding_dim
    planted = belief_embedding(name, SEEDED[name], dim)
    dead = placeholder_embedding(dim)

    d = _cos_dist(planted, dead)
    assert d > 0.5, (
        f"the seeded vector for {name!r} is cosine distance {d:.9f} from the dead placeholder. "
        f"At ~0.0 it IS the placeholder — the very state measured on the live cluster on "
        f"2026-07-13, while the honesty ledger claimed otherwise."
    )


def test_belief_embedding_refuses_a_vector_computed_from_different_text():
    """Editing a belief's wording without re-embedding must fail LOUDLY at seed time.

    This is the subtlest form of the bug: a REAL vector, for the WRONG sentence. It would pass
    every "is it the placeholder?" check above while describing text nobody is seeding.
    """
    with pytest.raises(RuntimeError, match="DIFFERENT text"):
        belief_embedding("origin", RULE_TEXT + " and also anything else", get_settings().embedding_dim)


def test_a_reseed_leaves_the_real_embedding_in_place():
    """The decisive one: a RESEED — the thing that used to destroy the embedding — now plants it.

    Destructive by necessity (it reseeds `defaultdb`), like every other run_seed() test here.
    """

    async def go():
        await run_seed()
        dim = get_settings().embedding_dim
        dead = placeholder_embedding(dim)
        async with engine.connect() as c:
            for name in sorted(SEEDED):
                row = (await c.execute(
                    text("SELECT embedding::TEXT AS e FROM beliefs WHERE id = :i"),
                    {"i": bid(name)},
                )).mappings().first()
                assert row is not None, f"{name} was not seeded"
                stored = [float(x) for x in row["e"].strip("[]").split(",")]
                d = _cos_dist(stored, dead)
                assert d > 0.5, (
                    f"AFTER A RESEED, belief {name!r} carries a vector {d:.9f} from the "
                    f"placeholder — the reseed re-planted the dead vector, which is exactly the "
                    f"bug this fix exists to make impossible."
                )

    asyncio.run(go())
