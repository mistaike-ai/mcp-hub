"""HubProxy — core aggregating proxy engine.

Fetches tool lists from all enabled upstreams, prefixes them, merges, and
routes tool calls to the correct upstream. Circuit-open registrations are
silently omitted from the tool list (no error surfaced to the LLM).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import redis.asyncio as aioredis

from mcp_hub.circuit_breaker import CircuitBreaker, CircuitState
from mcp_hub.interfaces import BackendResolver, EncryptionProvider, Registration
from mcp_hub.logging import LogSink, ZeroRetentionLogger
from mcp_hub.upstream import UpstreamClient, UpstreamError

logger = logging.getLogger(__name__)

_TOOL_CACHE_PREFIX = "mcp_hub:tools:"
_TOOL_CACHE_TTL = 30  # 30 seconds

# Maximum concurrent upstream list_tools calls
_MAX_CONCURRENT_LIST = 10


class HubProxy:
    """Aggregating MCP proxy engine.

    Args:
        backend: Resolves user registrations (no default — injected by host).
        encryption: Provides per-user AES keys (no default — injected by host).
        redis_url: Redis connection URL for tool cache and circuit breaker state.
        log_sink: Optional LogSink for persisting call metadata / encrypted payloads.
        verify_tls: Enforce TLS on upstream connections (default True).
    """

    def __init__(
        self,
        backend: BackendResolver,
        encryption: EncryptionProvider,
        redis_url: str,
        log_sink: Optional[LogSink] = None,
        verify_tls: bool = True,
    ) -> None:
        self._backend = backend
        self._encryption = encryption
        self._redis: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=False)
        self._log_sink = log_sink
        self._verify_tls = verify_tls
        self._logger: Optional[ZeroRetentionLogger] = (
            ZeroRetentionLogger(log_sink, encryption) if log_sink else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_tools(self, user_id: str) -> list[dict[str, Any]]:
        """Return merged, prefixed tool list for *user_id*.

        Circuit-open upstreams are omitted without surfacing errors to the LLM.
        Results are cached in Redis for *_TOOL_CACHE_TTL* seconds.
        """
        cache_key = f"{_TOOL_CACHE_PREFIX}{user_id}"
        cached = await self._redis.get(cache_key)
        if cached:
            import json
            return json.loads(cached)

        registrations = await self._backend.get_registrations(user_id)
        tools = await self._fetch_all_tools(user_id, registrations)

        import json
        await self._redis.setex(cache_key, _TOOL_CACHE_TTL, json.dumps(tools))
        return tools

    async def call_tool(
        self,
        user_id: str,
        prefixed_tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Route a prefixed tool call to the correct upstream.

        Args:
            user_id: Authenticated caller.
            prefixed_tool_name: Tool name as exposed to the LLM, e.g. "github__create_issue".
            arguments: Tool arguments from the LLM.

        Returns:
            Upstream tool result.

        Raises:
            ValueError: If the prefix maps to no registered upstream.
            UpstreamError: On upstream failure (after circuit breaker update).
        """
        prefix, tool_name = self._split_prefixed_name(prefixed_tool_name)
        registrations = await self._backend.get_registrations(user_id)
        reg = self._find_registration(registrations, prefix)

        cb = CircuitBreaker(self._redis, reg.id)
        state = await cb.get_state()
        if state == CircuitState.OPEN:
            raise UpstreamError(
                f"Upstream '{prefix}' is circuit-open; tool call rejected"
            )

        user_key = await self._encryption.get_user_key(user_id)
        raw_credential = (
            self._encryption.decrypt_credential(reg, user_key)
            if reg.auth_type != "none"
            else None
        )

        client = UpstreamClient(
            registration=reg,
            raw_credential=raw_credential,
            verify_tls=self._verify_tls,
        )

        # Ensure schemas are loaded for argument validation
        await client.list_tools()

        start = time.monotonic()
        status = "success"
        result: Any = None
        try:
            result = await client.call_tool(tool_name, arguments)
            await cb.record_success()
        except UpstreamError:
            status = "error"
            await cb.record_failure()
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            if self._logger:
                retention_days = await self._backend.get_user_log_retention_days(user_id)
                expires_at = self._compute_expires_at(retention_days)
                await self._logger.log_call(
                    registration=reg,
                    tool_name=prefixed_tool_name,
                    arguments=arguments,
                    response=result,
                    latency_ms=latency_ms,
                    status=status,
                    user_key=user_key,
                    expires_at=expires_at,
                )

        return result

    async def invalidate_cache(self, user_id: str) -> None:
        """Hard-delete the tool cache for *user_id* (call on registration change)."""
        await self._redis.delete(f"{_TOOL_CACHE_PREFIX}{user_id}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_all_tools(
        self, user_id: str, registrations: list[Registration]
    ) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(_MAX_CONCURRENT_LIST)
        tasks = [
            self._fetch_registration_tools(user_id, reg, sem)
            for reg in registrations
            if reg.enabled
        ]
        results = await asyncio.gather(*tasks)
        merged: list[dict] = []
        for tool_list in results:
            merged.extend(tool_list)
        return merged

    async def _fetch_registration_tools(
        self,
        user_id: str,
        reg: Registration,
        sem: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        cb = CircuitBreaker(self._redis, reg.id)
        state = await cb.get_state()
        if state == CircuitState.OPEN:
            logger.debug("Skipping circuit-open upstream '%s'", reg.name)
            return []

        user_key = await self._encryption.get_user_key(user_id)
        raw_credential = (
            self._encryption.decrypt_credential(reg, user_key)
            if reg.auth_type != "none"
            else None
        )
        client = UpstreamClient(
            registration=reg,
            raw_credential=raw_credential,
            verify_tls=self._verify_tls,
        )

        async with sem:
            try:
                tools = await client.list_tools()
                await cb.record_success()
            except UpstreamError as exc:
                logger.warning("Failed to list tools for '%s': %s", reg.name, exc)
                await cb.record_failure()
                return []

        # Prefix all tool names with "<name>__"
        prefixed = []
        for tool in tools:
            prefixed_tool = dict(tool)
            prefixed_tool["name"] = f"{reg.name}__{tool['name']}"
            prefixed.append(prefixed_tool)
        return prefixed

    @staticmethod
    def _split_prefixed_name(prefixed_name: str) -> tuple[str, str]:
        parts = prefixed_name.split("__", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Tool name '{prefixed_name}' is not prefixed (expected '<prefix>__<tool>')"
            )
        return parts[0], parts[1]

    @staticmethod
    def _find_registration(
        registrations: list[Registration], prefix: str
    ) -> Registration:
        for reg in registrations:
            if reg.name == prefix and reg.enabled:
                return reg
        raise ValueError(f"No enabled registration found for prefix '{prefix}'")

    @staticmethod
    def _compute_expires_at(retention_days: Optional[int]) -> Optional[str]:
        if retention_days is None:
            return None
        from datetime import datetime, timedelta, timezone
        expires = datetime.now(timezone.utc) + timedelta(days=retention_days)
        return expires.isoformat()
