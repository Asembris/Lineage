"""In-process, per-IP, per-route rate limiting (lean Phase 4).

Scope: a single-process demo. This is a fixed-window counter keyed on
(client_ip, route-template), guarded by an ``asyncio.Lock`` so concurrent requests
cannot race the shared bucket dict. It is deliberately NOT distributed — a
multi-instance limiter would need Redis/CRDB and is explicitly out of lean scope.

The one correctness subtlety worth stating: the read-modify-write of a bucket happens
entirely inside the lock with NO ``await`` in the critical section, so a burst of
concurrent requests can never interleave a stale read with another's write. A naive
``dict[key] += 1`` without the lock would be atomic only by accident of the current
event loop; the lock makes the guarantee explicit and await-safe.

Routes are keyed by TEMPLATE (UUID path segments collapsed to ``:id``) so an attacker
cannot dodge the limit by varying the id in ``/beliefs/<uuid>/invalidate``.
"""

from __future__ import annotations

import asyncio
import re
import time

# Collapse UUID path segments so /beliefs/<uuid>/invalidate and /agents/<uuid>/beliefs
# each map to one stable per-route bucket regardless of the concrete id.
_UUID_SEG = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def route_template(path: str) -> str:
    return _UUID_SEG.sub("/:id", path)


class RateLimiter:
    """Fixed-window per-(ip, route) request limiter."""

    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = asyncio.Lock()
        # (ip, route_template) -> (window_start_monotonic, count)
        self._buckets: dict[tuple[str, str], tuple[float, int]] = {}

    async def check(self, ip: str, path: str) -> tuple[bool, int]:
        """Record a request. Returns (allowed, retry_after_seconds).

        Concurrency-safe: the whole read-modify-write runs under the lock with no
        awaits inside, so it is atomic against other concurrent requests.
        """
        key = (ip, route_template(path))
        now = time.monotonic()
        async with self._lock:
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= self.window_seconds:  # window elapsed -> reset
                start, count = now, 0
            if count >= self.max_requests:
                retry_after = int(self.window_seconds - (now - start)) + 1
                return False, max(1, retry_after)
            self._buckets[key] = (start, count + 1)
            return True, 0
