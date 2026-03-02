import pytest
from unittest.mock import AsyncMock, patch
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

@pytest.fixture
def mock_mcp_client():
    with patch("mcp_hub.upstream.streamablehttp_client") as mock_streamable:
        mock_ctx = AsyncMock()
        mock_streamable.return_value = mock_ctx
        mock_ctx.__aenter__.return_value = (AsyncMock(), AsyncMock(), AsyncMock())
        
        with patch("mcp_hub.upstream.ClientSession") as mock_session_cls:
            mock_session_ctx = AsyncMock()
            mock_session_cls.return_value = mock_session_ctx
            
            mock_session = AsyncMock()
            mock_session_ctx.__aenter__.return_value = mock_session
            
            yield mock_session

@pytest.mark.asyncio
async def test_list_tools_streamable_http(registration, mock_mcp_client):
    # Mock the return value of list_tools
    class MockTool:
        def model_dump(self):
            return {"name": "tool1", "description": "Desc 1", "inputSchema": {"type": "object", "properties": {}}}
            
    class MockListToolsResult:
        tools = [MockTool()]
        
    mock_mcp_client.list_tools.return_value = MockListToolsResult()

    client = UpstreamClient(registration)
    tools = await client.list_tools()

    assert len(tools) == 1
    assert tools[0]["name"] == "tool1"
    assert client._tool_schemas["tool1"] == {"type": "object", "properties": {}}
    
    # Verify initialize was called
    mock_mcp_client.initialize.assert_awaited_once()
    mock_mcp_client.list_tools.assert_awaited_once()

@pytest.mark.asyncio
async def test_list_tools_upstream_error_on_failure(registration, mock_mcp_client):
    mock_mcp_client.list_tools.side_effect = Exception("Connection refused")

    client = UpstreamClient(registration)
    with pytest.raises(UpstreamError, match="Connection refused"):
        await client.list_tools()

@pytest.mark.asyncio
async def test_call_tool_routes_correctly(registration, mock_mcp_client):
    class MockCallToolResult:
        def model_dump(self):
            return {"content": [{"type": "text", "text": "hello world"}], "isError": False}
            
    mock_mcp_client.call_tool.return_value = MockCallToolResult()

    client = UpstreamClient(registration)
    client._tool_schemas = {"tool1": {"required": []}}

    result = await client.call_tool("tool1", {"arg1": "val1"})
    
    # Depending on how we implement call_tool in UpstreamClient:
    # We probably should return the dict representation of the MCP CallToolResult.
    assert result == {"content": [{"type": "text", "text": "hello world"}], "isError": False}
    
    mock_mcp_client.initialize.assert_awaited_once()
    mock_mcp_client.call_tool.assert_awaited_once_with("tool1", {"arg1": "val1"})

@pytest.mark.asyncio
async def test_call_tool_missing_required_arg_raises(registration, mock_mcp_client):
    client = UpstreamClient(registration)
    client._tool_schemas = {"tool1": {"required": ["important_arg"]}}

    with pytest.raises(ValueError, match="Missing required arguments"):
        await client.call_tool("tool1", {"other_arg": "val"})

@pytest.mark.asyncio
async def test_call_tool_upstream_error_raises(registration, mock_mcp_client):
    mock_mcp_client.call_tool.side_effect = Exception("Internal error")

    client = UpstreamClient(registration)
    client._tool_schemas = {"tool1": {"required": []}}
    
    with pytest.raises(UpstreamError, match="Internal error"):
        await client.call_tool("tool1", {})
