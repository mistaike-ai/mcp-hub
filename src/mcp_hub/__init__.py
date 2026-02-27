"""mcp-hub — Aggregating MCP proxy engine."""

from mcp_hub.proxy import HubProxy
from mcp_hub.interfaces import BackendResolver, EncryptionProvider, Registration

__all__ = ["HubProxy", "BackendResolver", "EncryptionProvider", "Registration"]
__version__ = "0.1.0"
