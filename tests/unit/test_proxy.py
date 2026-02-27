import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_hub.circuit_breaker import CircuitState
from mcp_hub.interfaces import BackendResolver, EncryptionProvider, Registration
from mcp_hub.proxy import HubProxy, _TOOL_CACHE_PREFIX, _TOOL_CACHE_TTL
from mcp_hub.upstream import UpstreamError


@pytest.fixture
def mock_backend_resolver():
    return AsyncMock(spec=BackendResolver)


@pytest.fixture
def mock_encryption_provider():
    return AsyncMock(spec=EncryptionProvider)


@pytest.fixture
def mock_log_sink():
    return AsyncMock()


@pytest.fixture
def mock_redis_client():
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.setex.return_value = None
    mock_redis.delete.return_value = None
    mock_redis.incr.return_value = 1
    mock_redis.expire.return_value = None
    mock_redis.pipeline.return_value = AsyncMock()  # Mock pipeline
    mock_redis.pipeline.return_value.set.return_value = None
    mock_redis.pipeline.return_value.delete.return_value = None
    mock_redis.pipeline.return_value.execute.return_value = None
    return mock_redis


@pytest.fixture
def hub_proxy(mock_backend_resolver, mock_encryption_provider, mock_redis_client, mock_log_sink=None):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("mcp_hub.proxy.aioredis", MagicMock())
        mp.setattr("mcp_hub.proxy.aioredis.from_url", MagicMock(return_value=mock_redis_client))

        # Mock CircuitBreaker for all HubProxy tests
        mock_circuit_breaker_instance = AsyncMock()
        mock_circuit_breaker_instance.get_state.return_value = CircuitState.CLOSED
        mock_circuit_breaker_instance.record_success.return_value = None
        mock_circuit_breaker_instance.record_failure.return_value = None
        mock_circuit_breaker_constructor = MagicMock(return_value=mock_circuit_breaker_instance)
        mp.setattr("mcp_hub.proxy.CircuitBreaker", mock_circuit_breaker_constructor)

        return HubProxy(
            backend=mock_backend_resolver,
            encryption=mock_encryption_provider,
            redis_url="redis://localhost:6379",  # Valid URL, but aioredis.from_url is mocked
            log_sink=mock_log_sink,
        )


class TestHubProxy:
    USER_ID = "test_user"
    REGISTRATION_ID_WEATHER = "weather_reg_id"
    REGISTRATION_ID_CALENDAR = "calendar_reg_id"

    @pytest.mark.asyncio
    async def test_get_tools_empty_registrations(self, hub_proxy, mock_backend_resolver):
        mock_backend_resolver.get_registrations.return_value = []
        tools = await hub_proxy.get_tools(self.USER_ID)
        assert tools == []
        mock_backend_resolver.get_registrations.assert_called_once_with(self.USER_ID)

    @pytest.mark.asyncio
    async def test_get_tools_prefixes_tool_names(
        self, hub_proxy, mock_backend_resolver, mock_encryption_provider, mock_redis_client
    ):
        weather_reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_backend_resolver.get_registrations.return_value = [weather_reg]

        mock_upstream_client = AsyncMock()
        mock_upstream_client.list_tools.return_value = [
            {"name": "forecast", "description": "Get weather forecast"},
            {"name": "current_conditions", "description": "Get current conditions"},
        ]
        # Patch UpstreamClient for this test
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("mcp_hub.proxy.UpstreamClient", MagicMock(return_value=mock_upstream_client))
            tools = await hub_proxy.get_tools(self.USER_ID)

        expected_tools = [
            {"name": "weather__forecast", "description": "Get weather forecast"},
            {"name": "weather__current_conditions", "description": "Get current conditions"},
        ]
        assert tools == expected_tools
        mock_upstream_client.list_tools.assert_called_once()
        mock_redis_client.setex.assert_called_once_with(
            f"{_TOOL_CACHE_PREFIX}{self.USER_ID}", _TOOL_CACHE_TTL, json.dumps(expected_tools)
        )

    @pytest.mark.asyncio
    async def test_get_tools_redis_cache_hit(
        self, hub_proxy, mock_backend_resolver, mock_redis_client
    ):
        cached_tools = [
            {"name": "cached__tool", "description": "From cache"}
        ]
        mock_redis_client.get.return_value = json.dumps(cached_tools).encode('utf-8')

        tools = await hub_proxy.get_tools(self.USER_ID)
        assert tools == cached_tools
        mock_backend_resolver.get_registrations.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_tools_redis_cache_miss(
        self, hub_proxy, mock_backend_resolver, mock_encryption_provider, mock_redis_client
    ):
        weather_reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_backend_resolver.get_registrations.return_value = [weather_reg]

        mock_upstream_client = AsyncMock()
        mock_upstream_client.list_tools.return_value = [
            {"name": "forecast", "description": "Get weather forecast"}
        ]
        # Patch UpstreamClient for this test
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("mcp_hub.proxy.UpstreamClient", MagicMock(return_value=mock_upstream_client))
            # Simulate cache miss (get returns None)
            mock_redis_client.get.side_effect = [
                None,  # First call for cache miss
                b"closed",  # Subsequent calls from CircuitBreaker.get_state
            ]

            tools = await hub_proxy.get_tools(self.USER_ID)

        expected_tools = [
            {"name": "weather__forecast", "description": "Get weather forecast"}
        ]
        assert tools == expected_tools
        mock_backend_resolver.get_registrations.assert_called_once_with(self.USER_ID)
        mock_upstream_client.list_tools.assert_called_once()
        assert mock_redis_client.get.call_args_list[0].args[0] == f"{_TOOL_CACHE_PREFIX}{self.USER_ID}"
        assert mock_redis_client.get.call_count == 2  # One for cache, one for circuit breaker state

    @pytest.mark.asyncio
    async def test_call_tool_routes_to_correct_upstream(
        self, hub_proxy, mock_backend_resolver, mock_encryption_provider
    ):
        weather_reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="api_key",
            log_mode="encrypted_full",
            credentials_encrypted=b"encrypted_creds",
            credentials_iv=b"iv",
            credentials_auth_tag=b"auth_tag",
        )
        mock_backend_resolver.get_registrations.return_value = [weather_reg]
        mock_encryption_provider.get_user_key.return_value = b"sixteen_byte_user_key"
        mock_encryption_provider.decrypt_credential.return_value = "secret_api_key"

        mock_upstream_client = AsyncMock()
        mock_upstream_client.call_tool.return_value = {"status": "sunny"}
        mock_upstream_client.list_tools.return_value = [  # Required for schema validation
            {"name": "forecast", "inputSchema": {"required": []}}
        ]

        with pytest.MonkeyPatch.context() as mp:
            mock_upstream_client_constructor = MagicMock(return_value=mock_upstream_client)
            mp.setattr("mcp_hub.proxy.UpstreamClient", mock_upstream_client_constructor)
            mock_circuit_breaker_instance = AsyncMock()
            mock_circuit_breaker_instance.get_state.return_value = CircuitState.CLOSED
            mock_circuit_breaker_constructor = MagicMock(return_value=mock_circuit_breaker_instance)
            mp.setattr("mcp_hub.proxy.CircuitBreaker", mock_circuit_breaker_constructor)

            result = await hub_proxy.call_tool(self.USER_ID, "weather__forecast", {"location": "London"})

            assert result == {"status": "sunny"}
            mock_backend_resolver.get_registrations.assert_called_once_with(self.USER_ID)
            mock_encryption_provider.get_user_key.assert_called_once_with(self.USER_ID)
            mock_encryption_provider.decrypt_credential.assert_called_once_with(weather_reg, b"sixteen_byte_user_key")
            mock_upstream_client_constructor.assert_called_once_with(
                registration=weather_reg,
                raw_credential="secret_api_key",
                verify_tls=True,
            )
            mock_upstream_client.list_tools.assert_called_once()
            mock_upstream_client.call_tool.assert_called_once_with("forecast", {"location": "London"})

    @pytest.mark.asyncio
    async def test_call_tool_unknown_prefix_raises(self, hub_proxy, mock_backend_resolver):
        mock_backend_resolver.get_registrations.return_value = []
        with pytest.raises(ValueError, match="No enabled registration found for prefix 'unknown'"):
            await hub_proxy.call_tool(self.USER_ID, "unknown__tool", {})

    @pytest.mark.asyncio
    async def test_split_prefixed_name_malformed_raises(self, hub_proxy):
        with pytest.raises(ValueError, match="Tool name 'malformed' is not prefixed"):
            await hub_proxy.call_tool(self.USER_ID, "malformed", {})

    @pytest.mark.asyncio
    async def test_call_tool_circuit_open_raises(
        self, hub_proxy, mock_backend_resolver, mock_encryption_provider
    ):
        weather_reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_backend_resolver.get_registrations.return_value = [weather_reg]

        mock_circuit_breaker = AsyncMock()
        mock_circuit_breaker.get_state.return_value = CircuitState.OPEN
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("mcp_hub.proxy.CircuitBreaker", MagicMock(return_value=mock_circuit_breaker))
            with pytest.raises(UpstreamError, match="Upstream 'weather' is circuit-open; tool call rejected"):
                await hub_proxy.call_tool(self.USER_ID, "weather__forecast", {})
            mock_circuit_breaker.get_state.assert_called_once()
            mock_encryption_provider.decrypt_credential.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidate_cache(self, hub_proxy, mock_redis_client):
        # Simulate cache existing before invalidation
        mock_redis_client.get.return_value = json.dumps([{"name": "some__tool"}]).encode('utf-8')
        mock_redis_client.delete.return_value = 1  # Simulate one key deleted

        await hub_proxy.invalidate_cache(self.USER_ID)
        mock_redis_client.delete.assert_called_once_with(f"{_TOOL_CACHE_PREFIX}{self.USER_ID}")

    @pytest.mark.asyncio
    async def test_get_tools_filters_disabled_registrations(
        self, hub_proxy, mock_backend_resolver, mock_encryption_provider, mock_redis_client
    ):
        enabled_reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="none",
            log_mode="metadata",
            enabled=True,
        )
        disabled_reg = Registration(
            id=self.REGISTRATION_ID_CALENDAR,
            user_id=self.USER_ID,
            name="calendar",
            url="http://calendar.com",
            auth_type="none",
            log_mode="metadata",
            enabled=False,
        )
        mock_backend_resolver.get_registrations.return_value = [enabled_reg, disabled_reg]

        mock_upstream_client_enabled = AsyncMock()
        mock_upstream_client_enabled.list_tools.return_value = [
            {"name": "forecast", "description": "Get weather forecast"},
        ]
        mock_upstream_client_disabled = AsyncMock()  # Should not be called

        with pytest.MonkeyPatch.context() as mp:
            # Patch UpstreamClient constructor to return different mocks based on registration
            def mock_upstream_constructor(registration, **kwargs):
                if registration.id == enabled_reg.id:
                    return mock_upstream_client_enabled
                return mock_upstream_client_disabled  # This instance should not be used
            mp.setattr("mcp_hub.proxy.UpstreamClient", MagicMock(side_effect=mock_upstream_constructor))

            tools = await hub_proxy.get_tools(self.USER_ID)

        expected_tools = [
            {"name": "weather__forecast", "description": "Get weather forecast"},
        ]
        assert tools == expected_tools
        mock_backend_resolver.get_registrations.assert_called_once_with(self.USER_ID)
        mock_upstream_client_enabled.list_tools.assert_called_once()
        mock_upstream_client_disabled.list_tools.assert_not_called()  # Ensure disabled one is not called

    @pytest.mark.asyncio
    async def test_call_tool_logs_when_logger_is_present(
        self, mock_backend_resolver, mock_encryption_provider, mock_redis_client, mock_log_sink
    ):
        # Mock ZeroRetentionLogger directly to assert on its log_call method
        mock_zero_retention_logger = AsyncMock()
        mock_zero_retention_logger.log_call.return_value = None  # No specific return needed

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("mcp_hub.proxy.aioredis", MagicMock())
            mp.setattr("mcp_hub.proxy.aioredis.from_url", MagicMock(return_value=mock_redis_client))
            mock_circuit_breaker_instance = AsyncMock()
            mock_circuit_breaker_instance.get_state.return_value = CircuitState.CLOSED
            mock_circuit_breaker_constructor = MagicMock(return_value=mock_circuit_breaker_instance)
            mp.setattr("mcp_hub.proxy.CircuitBreaker", mock_circuit_breaker_constructor)

            # Patch ZeroRetentionLogger directly
            mp.setattr("mcp_hub.proxy.ZeroRetentionLogger", MagicMock(return_value=mock_zero_retention_logger))

            hub_proxy_instance = HubProxy(
                backend=mock_backend_resolver,
                encryption=mock_encryption_provider,
                redis_url="redis://localhost:6379",
                log_sink=MagicMock(),  # Pass a simple mock for log_sink since ZeroRetentionLogger is mocked
            )

            weather_reg = Registration(
                id=self.REGISTRATION_ID_WEATHER,
                user_id=self.USER_ID,
                name="weather",
                url="http://weather.com",
                auth_type="none",
                log_mode="metadata",
            )
            mock_backend_resolver.get_registrations.return_value = [weather_reg]
            mock_encryption_provider.get_user_key.return_value = b"sixteen_byte_user_key"
            mock_backend_resolver.get_user_log_retention_days.return_value = 7  # For log_call

            mock_upstream_client = AsyncMock()
            mock_upstream_client.call_tool.return_value = {"status": "sunny"}
            mock_upstream_client.list_tools.return_value = [
                {"name": "forecast", "inputSchema": {"required": []}}
            ]

            mock_upstream_client_constructor = MagicMock(return_value=mock_upstream_client)
            mp.setattr("mcp_hub.proxy.UpstreamClient", mock_upstream_client_constructor)

            await hub_proxy_instance.call_tool(self.USER_ID, "weather__forecast", {"location": "London"})

            mock_backend_resolver.get_user_log_retention_days.assert_called_once_with(self.USER_ID)
            mock_zero_retention_logger.log_call.assert_called_once()
            _, call_kwargs = mock_zero_retention_logger.log_call.call_args
            assert call_kwargs["registration"].id == weather_reg.id
            assert call_kwargs["tool_name"] == "weather__forecast"
            assert call_kwargs["status"] == "success"

    @pytest.mark.asyncio
    async def test_call_tool_upstream_error_records_failure(
        self, hub_proxy, mock_backend_resolver, mock_encryption_provider
    ):
        weather_reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_backend_resolver.get_registrations.return_value = [weather_reg]
        mock_encryption_provider.get_user_key.return_value = b"key"

        mock_upstream_client = AsyncMock()
        mock_upstream_client.call_tool.side_effect = UpstreamError("Down")
        mock_upstream_client.list_tools.return_value = [{"name": "forecast"}]

        mock_circuit_breaker = AsyncMock()
        mock_circuit_breaker.get_state.return_value = CircuitState.CLOSED

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("mcp_hub.proxy.UpstreamClient", MagicMock(return_value=mock_upstream_client))
            mp.setattr("mcp_hub.proxy.CircuitBreaker", MagicMock(return_value=mock_circuit_breaker))

            with pytest.raises(UpstreamError):
                await hub_proxy.call_tool(self.USER_ID, "weather__forecast", {})

            mock_circuit_breaker.record_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tools_skips_circuit_open(
        self, hub_proxy, mock_backend_resolver, mock_redis_client
    ):
        reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_backend_resolver.get_registrations.return_value = [reg]
        mock_redis_client.get.return_value = None

        mock_circuit_breaker = AsyncMock()
        mock_circuit_breaker.get_state.return_value = CircuitState.OPEN

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("mcp_hub.proxy.CircuitBreaker", MagicMock(return_value=mock_circuit_breaker))
            tools = await hub_proxy.get_tools(self.USER_ID)
            assert tools == []

    @pytest.mark.asyncio
    async def test_get_tools_upstream_error_returns_empty(
        self, hub_proxy, mock_backend_resolver, mock_encryption_provider, mock_redis_client
    ):
        reg = Registration(
            id=self.REGISTRATION_ID_WEATHER,
            user_id=self.USER_ID,
            name="weather",
            url="http://weather.com",
            auth_type="none",
            log_mode="metadata",
        )
        mock_backend_resolver.get_registrations.return_value = [reg]
        mock_redis_client.get.return_value = None
        mock_encryption_provider.get_user_key.return_value = b"key"

        mock_upstream_client = AsyncMock()
        mock_upstream_client.list_tools.side_effect = UpstreamError("Failed")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("mcp_hub.proxy.UpstreamClient", MagicMock(return_value=mock_upstream_client))
            tools = await hub_proxy.get_tools(self.USER_ID)
            assert tools == []

    def test_compute_expires_at_none(self, hub_proxy):
        assert hub_proxy._compute_expires_at(None) is None
