# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
- Reduce `_TOOL_CACHE_TTL` from 5 minutes to 30 seconds (#150)

### Fixed
- fix(logging): `ZeroRetentionLogger.log_call()` crashes with `AttributeError` when `registration=None` — native platform tool calls via `hub_mcp` were silently not logged; use `"native"` as `registration_id` when registration is absent (#174)
### Fixed
- Replace raw JSON-RPC POST with proper MCP Streamable HTTP client in UpstreamClient (#4)

### Added
- HubProxy engine: tool listing with Redis cache, prefixed routing, circuit breaker integration (#1)
- UpstreamClient: JSON-RPC tool list and call with input schema validation (#1)
- CircuitBreaker: Redis-backed open/closed/half-open state machine (#1)
- LogSink ABC: metadata and encrypted_full logging modes (#1)
- Comprehensive test suite (≥90% coverage) (#1)

## [0.1.0] - TBD

### Added
- Initial repo scaffold: src layout, pyproject.toml, CI/CD workflows
