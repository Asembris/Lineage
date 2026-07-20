"""FastAPI app entrypoint — Lineage backend (Phase 1)."""

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import JSONResponse

# Windows: psycopg async requires the selector event loop, not the default proactor.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db import engine, get_engine  # noqa: E402
from app.ratelimit import RateLimiter  # noqa: E402
from app.routers import agents, aml, beliefs, decisions, demo  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Lineage — agent-genealogy forensics", lifespan=lifespan)

# Lean Phase 4 rate limiting: per-IP, per-route, in-process (single-instance demo scope).
# Generous default so legitimate demo traffic is never blocked; it only trips runaway loops.
_limiter = RateLimiter(max_requests=60, window_seconds=60.0)
# Both meta endpoints are exempt: an orchestrator (ECS/ALB) polls them on a fixed cadence
# from a single source IP, which would otherwise exhaust the per-IP limit and 429 the very
# probe that decides whether to route traffic. /health/ready matched by exact path, like /health.
_RATE_LIMIT_EXEMPT = frozenset({"/health", "/health/ready"})

# Readiness probe hard bound: covers a hung connect AND a hung query (asyncio.wait_for wraps
# the whole checkout+SELECT). A warm SELECT 1 is ~0.18s (NOTES.md), so 2s is margin, not a
# tight fit, and sits inside a typical ALB/ECS 5s health-check timeout.
_READINESS_TIMEOUT_S = 2.0


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if path in _RATE_LIMIT_EXEMPT:
        return await call_next(request)
    ip = request.client.host if request.client else "unknown"
    allowed, retry_after = await _limiter.check(ip, path)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded; slow down"},
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


# CORS for the Vite dev server (frontend supervisor console).
# Registered AFTER the rate_limit middleware above: Starlette inserts each added middleware
# at the FRONT of the stack, so the last-added is outermost. Adding CORS here therefore makes
# it wrap the rate limiter — preflight OPTIONS and CORS headers are applied even to requests
# the limiter would 429. Explicit dev origins (not "*"); no credentials (the API uses none).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(agents.router)
app.include_router(aml.router)
app.include_router(beliefs.router)
app.include_router(decisions.router)
app.include_router(demo.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/health/ready", tags=["meta"])
async def readiness(eng: AsyncEngine = Depends(get_engine)) -> JSONResponse:
    """READINESS — can this instance actually serve? Every served route needs the DB
    synchronously (none needs S3 synchronously — the cert-write path degrades on its own), so
    readiness is a real DB round-trip. Reachable -> 200; unreachable/timeout -> 503. It MUST be
    able to return non-200: a readiness check that is always green is the false-green we fix.
    """

    async def _probe() -> None:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_probe(), timeout=_READINESS_TIMEOUT_S)
    except (asyncio.TimeoutError, OSError, SQLAlchemyError):
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "db": "unreachable"},
        )
    return JSONResponse(status_code=200, content={"status": "ready", "db": "ok"})
