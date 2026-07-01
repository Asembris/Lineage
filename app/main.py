"""FastAPI app entrypoint — Lineage backend (Phase 1)."""

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Windows: psycopg async requires the selector event loop, not the default proactor.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db import engine  # noqa: E402
from app.routers import agents, beliefs  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Lineage — agent-genealogy forensics", lifespan=lifespan)
app.include_router(agents.router)
app.include_router(beliefs.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}
