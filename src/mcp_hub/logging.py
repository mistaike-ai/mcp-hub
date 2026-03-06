"""Zero-retention logger.

Two modes:
  metadata      — write only shape/timing; args and response are never stored.
  encrypted_full — AES-GCM encrypt full payload with user-derived key before storing.

The logger is write-only in the hot path — no reads, no analytics.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Any, Optional

from mcp_hub.interfaces import EncryptionProvider, Registration

logger = logging.getLogger(__name__)


@dataclass
class CallMetadata:
    """Shape and timing data captured for every call regardless of log_mode."""

    registration_id: str
    tool_name: str
    latency_ms: int
    status: str            # 'success' | 'error' | 'upstream_down'
    request_size_bytes: int
    response_size_bytes: int


class LogSink(abc.ABC):
    """Protocol for storing log entries.

    Implement this and inject into ZeroRetentionLogger to persist logs.
    """

    @abc.abstractmethod
    async def write_metadata(self, metadata: CallMetadata, expires_at: Optional[str]) -> str:
        """Persist metadata row; return the generated log entry ID."""
        raise NotImplementedError

    @abc.abstractmethod
    async def write_encrypted_payload(
        self, log_id: str, encrypted: dict[str, Any]
    ) -> None:
        """Attach encrypted payload to existing log entry *log_id*."""
        raise NotImplementedError


class ZeroRetentionLogger:
    """Log a proxied tool call with the retention policy dictated by log_mode.

    Args:
        sink: A LogSink implementation (provided by the host app).
        encryption: EncryptionProvider for encrypted_full mode.
    """

    def __init__(self, sink: LogSink, encryption: EncryptionProvider) -> None:
        self._sink = sink
        self._encryption = encryption

    async def log_call(
        self,
        registration: Optional[Registration],
        tool_name: str,
        arguments: dict[str, Any],
        response: Any,
        latency_ms: int,
        status: str,
        user_key: bytes,
        expires_at: Optional[str] = None,
    ) -> None:
        """Record a proxied call.

        In metadata mode the arguments and response are never written; they are
        discarded here before any I/O.

        In encrypted_full mode the payload is encrypted *before* being passed to
        the sink — the sink never receives plaintext.
        """
        import json

        req_bytes = len(json.dumps(arguments).encode())
        resp_bytes = len(json.dumps(response).encode()) if response is not None else 0

        metadata = CallMetadata(
            registration_id=registration.id if registration is not None else "native",
            tool_name=tool_name,
            latency_ms=latency_ms,
            status=status,
            request_size_bytes=req_bytes,
            response_size_bytes=resp_bytes,
        )

        log_id = await self._sink.write_metadata(metadata, expires_at)

        if registration is not None and registration.log_mode == "encrypted_full":
            payload = {"arguments": arguments, "response": response}
            # Encrypt BEFORE handing to sink — sink never sees plaintext
            encrypted = self._encryption.encrypt_payload(payload, user_key)
            await self._sink.write_encrypted_payload(log_id, encrypted)

        # In metadata mode: arguments and response are discarded here (never written)
