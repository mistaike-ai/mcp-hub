"""Upstream auth header injection.

Phase 1: none — URL-only (no auth header)
Phase 2: api_key — Authorization: Bearer or X-API-Key
Phase 3: oauth   — stub; logs a warning and skips injection
"""

from __future__ import annotations

import logging

from mcp_hub.interfaces import Registration

logger = logging.getLogger(__name__)


def build_auth_headers(registration: Registration, raw_credential: str | None) -> dict[str, str]:
    """Return HTTP headers to inject for *registration*'s auth type.

    Args:
        registration: The upstream registration record.
        raw_credential: Decrypted credential string (None when auth_type='none').

    Returns:
        Dict of header name → value (may be empty).
    """
    auth_type = registration.auth_type

    if auth_type == "none":
        return {}

    if auth_type == "api_key":
        if not raw_credential:
            logger.warning(
                "Registration %s has auth_type='api_key' but no credential; skipping auth header",
                registration.id,
            )
            return {}
        return {"Authorization": f"Bearer {raw_credential}"}

    if auth_type == "oauth":
        logger.warning(
            "Registration %s has auth_type='oauth' which is not yet supported; "
            "skipping auth header injection",
            registration.id,
        )
        return {}

    logger.warning("Unknown auth_type %r for registration %s", auth_type, registration.id)
    return {}
