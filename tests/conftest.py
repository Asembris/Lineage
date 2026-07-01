"""Test config.

Ensures the project root is importable and sets the Windows selector event loop
(psycopg async can't run on the default proactor loop). Tests themselves drive their
own event loop via asyncio.run(), so there is no pytest-asyncio loop-scope juggling.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
