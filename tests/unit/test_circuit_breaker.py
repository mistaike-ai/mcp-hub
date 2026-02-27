import time
from unittest.mock import AsyncMock, MagicMock
import pytest
from mcp_hub.circuit_breaker import CircuitBreaker, CircuitState, FAILURE_THRESHOLD


@pytest.fixture
def redis_client():
    mock_redis = AsyncMock()
    # In-memory storage for the mock
    storage = {}

    async def get(key):
        val = storage.get(key)
        if isinstance(val, bytes):
            return val
        if val is not None:
            return str(val).encode()
        return None

    async def set_val(key, value, ex=None):
        if hasattr(value, "value"):
            value = value.value
        storage[key] = value
        return True

    async def incr(key):
        val = int(storage.get(key, 0)) + 1
        storage[key] = str(val)
        return val

    async def delete(*keys):
        count = 0
        for k in keys:
            if k in storage:
                del storage[k]
                count += 1
        return count

    async def expire(key, seconds):
        return True

    mock_redis.get.side_effect = get
    mock_redis.set.side_effect = set_val
    mock_redis.incr.side_effect = incr
    mock_redis.delete.side_effect = delete
    mock_redis.expire.side_effect = expire

    # Pipeline mock
    pipeline = MagicMock()

    pending_actions = []

    def pipe_set(key, value, ex=None):
        if hasattr(value, "value"):
            value = value.value
        pending_actions.append(("set", key, value))
        return pipeline

    def pipe_delete(*keys):
        pending_actions.append(("delete", keys))
        return pipeline

    async def pipe_execute():
        for action in pending_actions:
            if action[0] == "set":
                storage[action[1]] = action[2]
            elif action[0] == "delete":
                for k in action[1]:
                    if k in storage:
                        del storage[k]
        pending_actions.clear()
        return []

    pipeline.set.side_effect = pipe_set
    pipeline.delete.side_effect = pipe_delete
    pipeline.execute.side_effect = pipe_execute

    mock_redis.pipeline = MagicMock(return_value=pipeline)

    return mock_redis


@pytest.fixture
def circuit_breaker(redis_client):
    return CircuitBreaker(redis_client, "test_reg")


@pytest.mark.asyncio
async def test_initial_state_closed(circuit_breaker):
    assert await circuit_breaker.get_state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_record_failure_increments_count(circuit_breaker, redis_client):
    await circuit_breaker.record_failure()
    failures = await redis_client.get("mcp_hub:cb:test_reg:failures")
    assert int(failures) == 1


@pytest.mark.asyncio
async def test_opens_after_threshold_failures(circuit_breaker):
    for _ in range(FAILURE_THRESHOLD):
        await circuit_breaker.record_failure()

    assert await circuit_breaker.get_state() == CircuitState.OPEN


@pytest.mark.asyncio
async def test_is_open_returns_true_when_open(circuit_breaker):
    for _ in range(FAILURE_THRESHOLD):
        await circuit_breaker.record_failure()

    assert await circuit_breaker.is_healthy() is False
    assert await circuit_breaker.get_state() == CircuitState.OPEN


@pytest.mark.asyncio
async def test_is_open_returns_false_when_closed(circuit_breaker):
    assert await circuit_breaker.is_healthy() is True
    assert await circuit_breaker.get_state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_after_timeout(circuit_breaker, redis_client):
    # Trip the circuit
    for _ in range(FAILURE_THRESHOLD):
        await circuit_breaker.record_failure()

    assert await circuit_breaker.get_state() == CircuitState.OPEN

    # Manually expire the open_until key or set it to the past
    await redis_client.set("mcp_hub:cb:test_reg:open_until", str(time.time() - 1))

    # Next get_state should transition to HALF_OPEN
    assert await circuit_breaker.get_state() == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_record_success_resets_to_closed(circuit_breaker, redis_client):
    # Trip it
    for _ in range(FAILURE_THRESHOLD):
        await circuit_breaker.record_failure()

    await circuit_breaker.record_success()
    assert await circuit_breaker.get_state() == CircuitState.CLOSED
    assert await redis_client.get("mcp_hub:cb:test_reg:failures") is None


@pytest.mark.asyncio
async def test_failure_in_half_open_trips_back_to_open(circuit_breaker, redis_client):
    # Trip it
    for _ in range(FAILURE_THRESHOLD):
        await circuit_breaker.record_failure()

    # Go to half-open
    await redis_client.set("mcp_hub:cb:test_reg:open_until", str(time.time() - 1))
    assert await circuit_breaker.get_state() == CircuitState.HALF_OPEN

    # Fail again
    await circuit_breaker.record_failure()
    assert await circuit_breaker.get_state() == CircuitState.OPEN
    # Failure count should be reset to 1
    failures = await redis_client.get("mcp_hub:cb:test_reg:failures")
    assert int(failures) == 1
