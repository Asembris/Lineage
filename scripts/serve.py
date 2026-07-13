"""Run the API on Windows so that DB-backed routes actually work.

USE THIS INSTEAD OF `uvicorn app.main:app` ON WINDOWS. It is not a convenience wrapper; it
works around a real trap that has cost this project time in two separate sessions, and it
produces a failure that looks like something else entirely.

THE TRAP
--------
`app/main.py` sets `WindowsSelectorEventLoopPolicy` at import time, because psycopg's async
driver cannot run on Windows' default Proactor loop. But `uvicorn app.main:app` **creates its
event loop BEFORE importing the app**, and uvicorn's own loop setup re-sets the policy to
Proactor. So main.py's call lands too late, psycopg raises `InterfaceError` on every query, and
**every DB-backed route 500s** while the app appears to start perfectly.

AND THE SYMPTOM POINTS AT THE WRONG BUG: a 500 carries no CORS header, so the browser reports
*"blocked by CORS policy"* and you go hunting a CORS bug that does not exist. The CORS policy is
fine. The event loop is not.

`loop="none"` tells uvicorn to leave the loop alone; we create it here, with the right policy,
before uvicorn ever looks.

(In-process `httpx.ASGITransport(app=app)` never hits this — which is exactly why the whole test
suite is green while only the live console breaks. If you are verifying over real HTTP, use this.)

BEFORE TRUSTING ANY LIVE-HTTP RESULT, CHECK BOTH:
  1. `/openapi.json` carries a field you know is new — an orphaned uvicorn can hold the port and
     serve stale code while answering every request perfectly ("current" is a real failure mode).
  2. A DB-backed route returns 200 — not just `/health`, which touches no database ("working" is
     a different failure mode from "current", and this project hit both in one afternoon).

Usage:
    PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe scripts/serve.py [--port 8000]

The frontend's CORS allowlist is `localhost:5173` only, and its API base defaults to
`http://localhost:8000` (override with VITE_API_BASE). So serve on 8000 unless a zombie socket
holds it — see NOTES.md, where the un-killable stale-socket case is recorded.
"""

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # MUST happen before uvicorn creates its loop. This is the entire point of the file.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    print(f"serving app.main:app on http://{args.host}:{args.port} (selector loop, loop='none')")
    cfg = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        loop="none",  # do NOT let uvicorn install a Proactor loop over the policy set above
        log_level="warning",
    )
    asyncio.run(uvicorn.Server(cfg).serve())


if __name__ == "__main__":
    main()
