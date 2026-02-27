"""Per-upstream circuit breaker backed by Redis.

States:
  closed   — healthy, tool calls flow through
  open     — unhealthy, tools omitted from merged list; retried after *open_ttl* seconds
  half_open — single probe call permitted; success → closed, failure → open

All state is stored in Redis so multiple hub instances share circuit state.
"""

from __future__ import annotations

import time
from enum import Enum

import redis.asyncio as aioredis


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_PREFIX = "mcp_hub:cb:"
_FAILURE_COUNT_SUFFIX = ":failures"
_STATE_SUFFIX = ":state"
_OPEN_UNTIL_SUFFIX = ":open_until"

FAILURE_THRESHOLD = 3
OPEN_TTL_SECONDS = 60
HALF_OPEN_PROBE_TTL = 10


class CircuitBreaker:
    """Thread-safe, Redis-backed circuit breaker for a single upstream."""

    def __init__(self, redis_client: aioredis.Redis, registration_id: str) -> None:
        self._redis = redis_client
        self._key = f"{_PREFIX}{registration_id}"

    async def get_state(self) -> CircuitState:
        state_raw = await self._redis.get(f"{self._key}{_STATE_SUFFIX}")
        if state_raw is None:
            return CircuitState.CLOSED
        state = state_raw.decode() if isinstance(state_raw, bytes) else state_raw
        if state == CircuitState.OPEN:
            open_until_raw = await self._redis.get(f"{self._key}{_OPEN_UNTIL_SUFFIX}")
            if open_until_raw is not None:
                open_until = float(open_until_raw)
                if time.time() >= open_until:
                    await self._transition(CircuitState.HALF_OPEN)
                    return CircuitState.HALF_OPEN
        return CircuitState(state)

    async def record_success(self) -> None:
        await self._redis.delete(
            f"{self._key}{_FAILURE_COUNT_SUFFIX}",
            f"{self._key}{_STATE_SUFFIX}",
            f"{self._key}{_OPEN_UNTIL_SUFFIX}",
        )

    async def record_failure(self) -> None:
        current_state = await self.get_state()
        if current_state == CircuitState.HALF_OPEN:
            # If in HALF_OPEN and failure occurs, immediately trip back to OPEN
            await self._trip()
            # Set failure count to 1 for the current failure that caused re-tripping
            await self._redis.set(f"{self._key}{_FAILURE_COUNT_SUFFIX}", 1)
            await self._redis.expire(f"{self._key}{_FAILURE_COUNT_SUFFIX}", OPEN_TTL_SECONDS * 2)
            return

        failures = await self._redis.incr(f"{self._key}{_FAILURE_COUNT_SUFFIX}")
        await self._redis.expire(
            f"{self._key}{_FAILURE_COUNT_SUFFIX}", OPEN_TTL_SECONDS * 2
        )
        if failures >= FAILURE_THRESHOLD:
            await self._trip()

    async def _trip(self) -> None:
        open_until = time.time() + OPEN_TTL_SECONDS
        pipe = self._redis.pipeline()
        pipe.set(f"{self._key}{_STATE_SUFFIX}", CircuitState.OPEN, ex=OPEN_TTL_SECONDS * 2)
        pipe.set(f"{self._key}{_OPEN_UNTIL_SUFFIX}", str(open_until), ex=OPEN_TTL_SECONDS * 2)
        pipe.delete(f"{self._key}{_FAILURE_COUNT_SUFFIX}")  # Reset failure count when tripping
        await pipe.execute()

    async def _transition(self, state: CircuitState) -> None:
        await self._redis.set(
            f"{self._key}{_STATE_SUFFIX}", state, ex=HALF_OPEN_PROBE_TTL
        )

    async def is_healthy(self) -> bool:
        return await self.get_state() == CircuitState.CLOSED
