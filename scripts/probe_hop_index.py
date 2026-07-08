"""scripts/probe_hop_index.py — is aml_pattern_members.hop_index a real order? (Roadmap Item 1)

READ-ONLY diagnostic. For one instance per typology (incl. a multi-component STACK), lines up the
stored hop_index, the stored transaction ts, and an INDEPENDENTLY re-derived Patterns.txt
within-block file position, then reports whether hop_index == file position and whether ts is
non-decreasing along hop_index (i.e. whether file order also happens to be chronological).

Finding (2026-07-08): hop_index == generator/file EMISSION order for every instance (exact match),
so it is real, not arbitrary insertion order. It is NOT reliably chronological: ts ascends along
hop only for connected CYCLEs, not SCATTER-GATHER or multi-component STACK. See NOTES.md.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/probe_hop_index.py
"""
import asyncio
import importlib.util
import pathlib
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "probe_aml", pathlib.Path(__file__).with_name("probe_aml.py"))
probe_aml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe_aml)  # type: ignore[union-attr]
PATTERNS = probe_aml.PATTERNS


def independent_file_positions():
    """Re-parse Patterns.txt fresh -> {instance_index: {raw_key: within_block_position}}."""
    out, cur_idx, pos, idx = {}, None, 0, -1
    with open(PATTERNS, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("BEGIN LAUNDERING ATTEMPT"):
                idx += 1
                cur_idx, pos = idx, 0
                out[cur_idx] = {}
            elif line.startswith("END LAUNDERING ATTEMPT"):
                cur_idx = None
            elif line.strip() and cur_idx is not None:
                p = probe_aml.parse(line)
                out[cur_idx]["\x1f".join(p["key"])] = pos
                pos += 1
    return out


async def main():
    filepos = independent_file_positions()
    async with engine.connect() as c:
        picks = (await c.execute(text(
            "SELECT DISTINCT ON (typology) id, typology, instance_index, num_rows, num_components "
            "FROM aml_pattern_instances "
            "WHERE typology IN ('CYCLE','SCATTER-GATHER','STACK') "
            "ORDER BY typology, instance_index"))).all()
        for iid, typ, iidx, nrows, ncomp in picks:
            rows = (await c.execute(text(
                "SELECT m.hop_index, t.ts, t.raw_key "
                "FROM aml_pattern_members m JOIN aml_transactions t ON t.id = m.transaction_id "
                "WHERE m.pattern_instance_id = :iid ORDER BY m.hop_index"), {"iid": iid})).all()
            print(f"\n=== {typ}  instance_index={iidx}  rows={nrows}  components={ncomp} ===")
            print(f"  {'hop':>3} {'ts':<16} {'file_pos':>8}  from->to (bank/acct)")
            hop_eq_pos, ts_prev, ts_monotonic = True, None, True
            for hop, ts, rk in rows:
                fp = filepos.get(iidx, {}).get(rk)
                if fp != hop:
                    hop_eq_pos = False
                if ts_prev is not None and ts < ts_prev:
                    ts_monotonic = False
                ts_prev = ts
                cols = rk.split("\x1f")
                flow = f"{cols[1]}/{cols[2]} -> {cols[3]}/{cols[4]}"
                print(f"  {hop:>3} {ts.strftime('%Y/%m/%d %H:%M'):<16} {str(fp):>8}  {flow}")
            print(f"  -> hop_index == file position: {hop_eq_pos};  "
                  f"ts non-decreasing along hop (chronological): {ts_monotonic}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
