"""Tests for the MCP server tool registry and transports."""

import json
import pytest

from webmcp_gen.serve import ToolRegistry, SERVER_NAME, SERVER_VERSION
from webmcp_gen.extract import PageExtraction, InteractiveElement, FormField


@pytest.fixture
def registry():
    """A registry with mock data (no browser, read-only)."""
    extraction = PageExtraction(
        url="https://example.com",
        title="Test",
        elements=[
            InteractiveElement(
                kind="form", text="Search", selector="form",
                element_index=0,
                fields=[FormField(tag="input", type="text", name="q", selector="#q")],
            ),
        ],
    )
    tools = {
        "tools": [
            {
                "name": "search",
                "description": "Search the site",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search term", "_selector": "#q"}
                    },
                    "required": ["query"],
                },
                "_submit_selector": "form",
                "_element_index": 0,
            }
        ],
        "site": {"name": "Test", "description": "Test site"},
    }
    return ToolRegistry("https://example.com", extraction, tools, execute=False)


class TestToolRegistry:
    """Test the transport-agnostic tool registry."""

    def test_mcp_tools_shape(self, registry):
        tools = registry.mcp_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool["name"] == "search"
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"
        assert "query" in tool["inputSchema"]["properties"]
        assert tool["inputSchema"]["required"] == ["query"]

    def test_mcp_tools_strips_internal_bindings(self, registry):
        """Internal _selector / _submit_selector must NOT leak to the client."""
        tools = registry.mcp_tools()
        for tool in tools:
            assert "_submit_selector" not in tool
            assert "_element_index" not in tool
            for prop in tool["inputSchema"]["properties"].values():
                assert "_selector" not in prop

    @pytest.mark.asyncio
    async def test_call_in_readonly_mode(self, registry):
        """Read-only registry should refuse execution without a browser."""
        result_json = await registry.call("search", {"query": "test"})
        result = json.loads(result_json)
        assert "error" in result


class TestHTTPTransport:
    """Test the HTTP JSON-RPC handler logic via a live local server."""

    @pytest.mark.asyncio
    async def test_http_initialize_and_list(self, registry):
        import threading
        import urllib.request
        from http.server import HTTPServer

        # Build a tiny server using the same handler logic
        import asyncio
        loop = asyncio.get_event_loop()

        # Reuse run_http by starting it in the background, then querying.
        from webmcp_gen.serve import run_http
        task = asyncio.ensure_future(run_http(registry, port=3199))
        await asyncio.sleep(0.5)  # let the server bind

        try:
            # initialize
            req = urllib.request.Request(
                "http://localhost:3199/",
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
            assert resp["result"]["serverInfo"]["version"] == SERVER_VERSION

            # tools/list
            req = urllib.request.Request(
                "http://localhost:3199/",
                data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
            tools = resp["result"]["tools"]
            assert len(tools) == 1
            assert tools[0]["name"] == "search"

            # unknown method
            req = urllib.request.Request(
                "http://localhost:3199/",
                data=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "bogus", "params": {}}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
            assert resp["error"]["code"] == -32601
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


class TestStdioSDK:
    """Verify the MCP SDK is available and the stdio path imports cleanly."""

    def test_mcp_sdk_importable(self):
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as types
        assert Server is not None
        assert stdio_server is not None
        assert types.Tool is not None
