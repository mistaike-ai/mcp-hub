import pytest
from unittest.mock import AsyncMock, patch

from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent

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
@patch("mcp_hub.upstream.ClientSession")
@patch("mcp_hub.upstream.streamablehttp_client")
async def test_list_tools_streamable_http(
    mock_streamable_client, mock_session_class, registration
):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = (AsyncMock(), AsyncMock(), None)
    mock_streamable_client.return_value = mock_ctx

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    tool = Tool(
        name="tool1",
        description="Desc 1",
        inputSchema={"type": "object", "properties": {}}
    )
    mock_session.list_tools = AsyncMock(
        return_value=ListToolsResult(tools=[tool])
    )
    mock_session_class.return_value.__aenter__.return_value = mock_session

    client = UpstreamClient(registration)
    tools = await client.list_tools()

    assert len(tools) == 1
    assert tools[0]["name"] == "tool1"
    assert client._tool_schemas["tool1"] == {
        "type": "object", "properties": {}
    }

    mock_streamable_client.assert_called_once_with(
        "https://api.test.com/mcp", headers={}
    )
    mock_session.initialize.assert_called_once()
    mock_session.list_tools.assert_called_once()


@pytest.mark.asyncio
@patch("mcp_hub.upstream.ClientSession")
@patch("mcp_hub.upstream.streamablehttp_client")
async def test_list_tools_upstream_error_on_failure(
    mock_streamable_client, mock_session_class, registration
):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.side_effect = Exception("Connection failed")
    mock_streamable_client.return_value = mock_ctx

    client = UpstreamClient(registration)
    with pytest.raises(UpstreamError, match="list_tools error"):
        await client.list_tools()


@pytest.mark.asyncio
@patch("mcp_hub.upstream.ClientSession")
@patch("mcp_hub.upstream.streamablehttp_client")
async def test_call_tool_routes_correctly(
    mock_streamable_client, mock_session_class, registration
):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = (AsyncMock(), AsyncMock(), None)
    mock_streamable_client.return_value = mock_ctx

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=CallToolResult(
        content=[TextContent(type="text", text="hello", audience=None)],
        isError=False
    ))
    mock_session_class.return_value.__aenter__.return_value = mock_session

    client = UpstreamClient(registration)
    client._tool_schemas = {"tool1": {"required": []}}

    result = await client.call_tool("tool1", {"arg1": "val1"})

    assert result["isError"] is False
    assert result["content"][0]["text"] == "hello"

    mock_streamable_client.assert_called_once_with(
        "https://api.test.com/mcp", headers={}
    )
    mock_session.initialize.assert_called_once()
    mock_session.call_tool.assert_called_once_with("tool1", {"arg1": "val1"})


@pytest.mark.asyncio
@patch("mcp_hub.upstream.ClientSession")
@patch("mcp_hub.upstream.streamablehttp_client")
async def test_call_tool_upstream_error_on_failure(
    mock_streamable_client, mock_session_class, registration
):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = (AsyncMock(), AsyncMock(), None)
    mock_streamable_client.return_value = mock_ctx

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value=CallToolResult(
        content=[TextContent(type="text", text="Failed", audience=None)],
        isError=True
    ))
    mock_session_class.return_value.__aenter__.return_value = mock_session

    client = UpstreamClient(registration)
    client._tool_schemas = {"tool1": {"required": []}}

    with pytest.raises(UpstreamError, match="Upstream error for.*Failed"):
        await client.call_tool("tool1", {"arg1": "val1"})
