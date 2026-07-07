"""Test config.

Ensures the project root is importable and sets the Windows selector event loop
(psycopg async can't run on the default proactor loop). Tests themselves drive their
own event loop via asyncio.run(), so there is no pytest-asyncio loop-scope juggling.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _reset_sse_appstatus():
    """Each test drives its own asyncio.run() loop, but sse-starlette caches a module-global exit
    Event bound to the FIRST loop that used an EventSourceResponse (sse.py line 240-241 recreates
    it only when None). Without this, the SECOND test in a run that streams SSE fails with
    'bound to a different event loop'. Null it before every test so it re-binds to the current
    loop. Harness hygiene only — unrelated to anything under test."""
    try:
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit_event = None
    except Exception:  # noqa: BLE001
        pass
    yield
