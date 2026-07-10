"""FastAPI app entrypoint — Lineage backend (Phase 1)."""

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

# Windows: psycopg async requires the selector event loop, not the default proactor.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db import engine  # noqa: E402
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
_RATE_LIMIT_EXEMPT = frozenset({"/health"})


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
