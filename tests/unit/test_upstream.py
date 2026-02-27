import pytest
import respx
from httpx import Response
from mcp_hub.upstream import UpstreamError, UpstreamClient
from mcp_hub.interfaces import Registration


@pytest.fixture
def registration():
    return Registration(
        id="reg_123",
        user_id="user_1",
        name="test_service",
        url="https://api.test.com/mcp",
        auth_type="none",
        log_mode="metadata",
        timeout_seconds=5
    )


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_returns_tools(registration):
    respx.post("https://api.test.com/mcp").mock(return_value=Response(200, json={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "tool1", "description": "Desc 1", "inputSchema": {"type": "object", "properties": {}}}
            ]
        }
    }))

    client = UpstreamClient(registration)
    tools = await client.list_tools()

    assert len(tools) == 1
    assert tools[0]["name"] == "tool1"
    assert client._tool_schemas["tool1"] == {"type": "object", "properties": {}}


@pytest.mark.asyncio
@respx.mock
async def test_list_tools_http_error_raises_upstream_error(registration):
    respx.post("https://api.test.com/mcp").mock(return_value=Response(500))

    client = UpstreamClient(registration)
    with pytest.raises(UpstreamError, match="list_tools HTTP error"):
        await client.list_tools()


@pytest.mark.asyncio
@respx.mock
async def test_call_tool_success(registration):
    respx.post("https://api.test.com/mcp").mock(return_value=Response(200, json={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"output": "hello world"}
    }))

    client = UpstreamClient(registration)
    # Mock schemas to skip validation or provide a simple one
    client._tool_schemas = {"tool1": {"required": []}}

    result = await client.call_tool("tool1", {"arg1": "val1"})
    assert result == {"output": "hello world"}


@pytest.mark.asyncio
@respx.mock
async def test_call_tool_missing_required_arg_raises(registration):
    client = UpstreamClient(registration)
    client._tool_schemas = {"tool1": {"required": ["important_arg"]}}

    with pytest.raises(ValueError, match="Missing required arguments"):
        await client.call_tool("tool1", {"other_arg": "val"})


@pytest.mark.asyncio
@respx.mock
async def test_call_tool_upstream_http_error_raises(registration):
    respx.post("https://api.test.com/mcp").mock(return_value=Response(404))

    client = UpstreamClient(registration)
    with pytest.raises(UpstreamError, match="call_tool HTTP error"):
        await client.call_tool("tool1", {})


@pytest.mark.asyncio
@respx.mock
async def test_call_tool_rpc_error_raises(registration):
    respx.post("https://api.test.com/mcp").mock(return_value=Response(200, json={
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "Method not found"}
    }))

    client = UpstreamClient(registration)
    with pytest.raises(UpstreamError, match="Upstream error for test_service/tool1"):
        await client.call_tool("tool1", {})
