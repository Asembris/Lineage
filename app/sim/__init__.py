"""Synthetic-world simulation for Phase 2.

The transaction generator lives here (reusable by the deterministic backfill AND, later,
the live OpenAI agent). It models a WORLD that drifts — the ground-truth fraud labels
shift over time as fraudsters adopt a pattern the founding belief blesses. It never writes
any performance number; staleness is always the aggregation of these labels vs. the
agent's verdicts (see seed/backfill_decisions.py and, later, app/services/performance.py).
"""
