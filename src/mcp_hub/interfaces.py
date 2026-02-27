"""Abstract interfaces injected by the host application (e.g. mistaike-mcp).

mcp-hub itself contains no DB access, no API key validation, and no vault
key management. These are provided at runtime by the host.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Registration:
    """A registered upstream MCP server for a given user."""

    id: str
    user_id: str
    name: str          # tool prefix — e.g. "github"
    url: str
    auth_type: str     # 'none' | 'api_key' | 'oauth'
    log_mode: str      # 'metadata' | 'encrypted_full'
    enabled: bool = True
    # Opaque encrypted credential blob; None when auth_type='none'
    credentials_encrypted: Optional[bytes] = None
    credentials_iv: Optional[bytes] = None
    credentials_auth_tag: Optional[bytes] = None
    timeout_seconds: int = 30
    extra: dict = field(default_factory=dict)


class BackendResolver(abc.ABC):
    """Fetches the caller's registered upstreams."""

    @abc.abstractmethod
    async def get_registrations(self, user_id: str) -> list[Registration]:
        """Return all enabled registrations for *user_id*."""

    @abc.abstractmethod
    async def get_user_log_retention_days(self, user_id: str) -> Optional[int]:
        """Return retention days for the user, or None for indefinite."""


class EncryptionProvider(abc.ABC):
    """Derives per-user AES keys and exposes encrypt/decrypt helpers."""

    @abc.abstractmethod
    async def get_user_key(self, user_id: str) -> bytes:
        """Return the raw 32-byte AES key for *user_id* (auto-provisioned)."""

    @abc.abstractmethod
    def decrypt_credential(self, registration: Registration, user_key: bytes) -> str:
        """Decrypt and return the raw credential string for *registration*."""

    @abc.abstractmethod
    def encrypt_payload(self, payload: dict, user_key: bytes) -> dict:
        """Return AES-GCM encrypted representation of *payload*."""

    @abc.abstractmethod
    def decrypt_payload(self, encrypted: dict, user_key: bytes) -> dict:
        """Decrypt and return the original payload dict."""
