# mcp-hub

Aggregating MCP proxy engine — self-hostable, MIT licensed.

`mcp-hub` lets you attach a **single MCP endpoint** to your LLM and register additional upstream MCP servers via the mistaike portal. All tool calls are routed through the hub with per-MCP optional logging.

## Design principles

- **Zero retention by default** — metadata-only mode discards request/response content immediately after routing
- **Zero-knowledge at rest** — `encrypted_full` mode encrypts payloads before writing to the log store; the hub operator cannot query them
- **Auditability** — this is open-source: every line of in-flight handling is auditable
- **Self-hostable** — run against your own DB if you need true zero-trust

## Architecture

```
LLM client
    │  (single MCP endpoint)
    ▼
HubProxy
    ├── UpstreamClient (MCP A)  ──► upstream-a.example.com
    ├── UpstreamClient (MCP B)  ──► upstream-b.example.com
    └── ZeroRetentionLogger ──► log store (metadata or encrypted_full)
```

`HubProxy` depends on two injectable interfaces:
- `BackendResolver` — resolves a user's registered upstreams (implemented by mistaike-mcp)
- `EncryptionProvider` — derives per-user AES keys for credential decryption and log encryption (implemented by mistaike-mcp)

Neither interface has a default implementation in this package, keeping the OSS repo free of proprietary business logic.

## Installation

```bash
pip install mcp-hub
```

Requires Python 3.11+.

## License

MIT
