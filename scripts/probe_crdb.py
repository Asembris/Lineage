"""Throwaway probe: verify CockroachDB-specific mechanics before we commit to them.

Run: .venv/Scripts/python.exe scripts/probe_crdb.py
Verifies: version, VECTOR type, vector index availability, gc.ttlseconds,
AOST read-timestamp builtin, and that SET TRANSACTION AS OF SYSTEM TIME engages
on a single async connection in one explicit transaction.
"""

import asyncio
import sys

from sqlalchemy import text

# Windows: psycopg async requires the selector event loop, not the default proactor.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db import engine


async def scalar(conn, sql):
    return (await conn.execute(text(sql))).scalar()


async def main():
    async with engine.connect() as conn:
        ver = await scalar(conn, "SELECT version()")
        print("VERSION:", ver)

    # --- VECTOR type support ---
    async with engine.begin() as conn:
        try:
            await conn.execute(text("DROP TABLE IF EXISTS _vec_probe"))
            await conn.execute(
                text("CREATE TABLE _vec_probe (id INT PRIMARY KEY, v VECTOR(3))")
            )
            await conn.execute(text("INSERT INTO _vec_probe VALUES (1, '[1,2,3]')"))
            got = await scalar(conn, "SELECT v FROM _vec_probe WHERE id = 1")
            print("VECTOR type: OK, roundtrip =", got)
        except Exception as e:
            print("VECTOR type: UNSUPPORTED ->", type(e).__name__, str(e)[:200])

    # --- Vector index availability (Phase 2 concern, just probing) ---
    async with engine.begin() as conn:
        try:
            await conn.execute(
                text("CREATE VECTOR INDEX _vec_probe_idx ON _vec_probe (v)")
            )
            print("VECTOR INDEX: AVAILABLE on this tier")
            await conn.execute(text("DROP INDEX _vec_probe_idx"))
        except Exception as e:
            print("VECTOR INDEX: not available ->", type(e).__name__, str(e)[:200])
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS _vec_probe"))

    # --- gc.ttlseconds (time-travel depth) ---
    async with engine.connect() as conn:
        try:
            raw = await scalar(
                conn, "SHOW ZONE CONFIGURATION FROM RANGE default"
            )
            print("ZONE default (raw):", raw)
        except Exception as e:
            print("gc.ttlseconds probe failed ->", type(e).__name__, str(e)[:200])

    # --- AOST read-timestamp builtin + SET TRANSACTION engagement ---
    async with engine.connect() as conn:
        t0 = await scalar(conn, "SELECT cluster_logical_timestamp()")
        print("\nt0 (HLC decimal):", t0)

    await asyncio.sleep(1.0)  # let wall clock advance so 'now' differs from t0

    # Same physical connection, one explicit transaction, SET first.
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text(f"SET TRANSACTION AS OF SYSTEM TIME {t0}"))
            clt = await scalar(conn, "SELECT cluster_logical_timestamp()")
            txn_ts = await scalar(conn, "SELECT transaction_timestamp()")
            stmt_ts = await scalar(conn, "SELECT statement_timestamp()")
            now_ts = await scalar(conn, "SELECT now()")
    print("Inside AOST txn set to t0:")
    print("  cluster_logical_timestamp() =", clt, " (== t0 means AOST engaged)")
    print("  transaction_timestamp()     =", txn_ts)
    print("  statement_timestamp()       =", stmt_ts)
    print("  now()                       =", now_ts)

    # Control: a normal read now, for comparison
    async with engine.connect() as conn:
        clt_now = await scalar(conn, "SELECT cluster_logical_timestamp()")
    print("Control (no AOST) cluster_logical_timestamp() =", clt_now)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
