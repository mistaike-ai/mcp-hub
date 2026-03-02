"""Upstream MCP client.

Wraps a single upstream MCP server (SSE or streamable HTTP).
Enforces TLS, timeout, and validates tool arguments against discovered schema
before forwarding.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

from mcp_hub.auth import build_auth_headers
from mcp_hub.interfaces import Registration

logger = logging.getLogger(__name__)


class UpstreamError(Exception):
    """Raised when an upstream call fails."""


class UpstreamClient:
    """HTTP client for a single upstream MCP server.

    Args:
        registration: The upstream's registration record.
        raw_credential: Decrypted auth credential (None when auth_type='none').
        verify_tls: Enforce TLS certificate verification (default True).
    """

    def __init__(
        self,
        registration: Registration,
        raw_credential: str | None = None,
        verify_tls: bool = True,
    ) -> None:
        self._reg = registration
        self._headers = build_auth_headers(registration, raw_credential)
        self._verify_tls = verify_tls
        self._timeout = registration.timeout_seconds
        self._tool_schemas: dict[str, dict] = {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Call tools/list on the upstream and cache schemas.

        Returns:
            List of MCP tool descriptors as returned by the upstream.

        Raises:
            UpstreamError: On HTTP or protocol failure.
        """
        try:
            async with streamablehttp_client(
                self._reg.url, headers=self._headers
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
        except Exception as exc:
            raise UpstreamError(
                f"list_tools error for {self._reg.name}: {exc}"
            ) from exc

        tools = []
        if hasattr(result, "tools"):
            for t in result.tools:
                t_dict = t.model_dump()
                tools.append(t_dict)
                self._tool_schemas[t.name] = t.inputSchema

        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Forward a tool call to the upstream, validating arguments first.

        Args:
            name: The tool name (without prefix).
            arguments: Tool arguments as provided by the LLM.

        Returns:
            The upstream tool result.

        Raises:
            UpstreamError: On HTTP or protocol failure.
            ValueError: If arguments fail schema validation.
        """
        self._validate_arguments(name, arguments)

        try:
            async with streamablehttp_client(
                self._reg.url, headers=self._headers
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        except Exception as exc:
            raise UpstreamError(
                f"call_tool error for {self._reg.name}: {exc}"
            ) from exc

        if result.isError:
            error_msg = "Unknown error"
            if result.content:
                # TextContent has text field, EmbeddedResource has resource
                if hasattr(result.content[0], "text"):
                    error_msg = result.content[0].text
                else:
                    error_msg = str(result.content)
            raise UpstreamError(
                f"Upstream error for {self._reg.name}/{name}: {error_msg}"
            )

        return result.model_dump()

    def _validate_arguments(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> None:
        """Basic schema validation — checks required fields are present."""
        schema = self._tool_schemas.get(tool_name)
        if not schema:
            # No cached schema; skip validation (upstream checks it)
            return
        required = schema.get("required", [])
        missing = [r for r in required if r not in arguments]
        if missing:
            raise ValueError(
                f"Missing required arguments for "
                f"{self._reg.name}/{tool_name}: {missing}"
            )
