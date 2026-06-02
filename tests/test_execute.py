"""Tests for the execute module."""

import pytest

from webmcp_gen.execute import WebExecutor, ToolDefinition, ToolResult


class TestToolDefinition:
    """Test ToolDefinition parsing."""

    def test_from_dict_basic(self):
        d = {
            "name": "searchWeb",
            "description": "Search the web",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "_selector": "#search-input"}
                },
                "required": ["query"],
            },
            "_submit_selector": "form#main",
            "_element_index": 0,
        }
        tool = ToolDefinition.from_dict(d)
        assert tool.name == "searchWeb"
        assert tool.submit_selector == "form#main"
        assert tool.element_index == 0
        assert tool.link_bindings == {}

    def test_from_dict_with_links(self):
        d = {
            "name": "navigate",
            "description": "Navigate",
            "parameters": {"type": "object", "properties": {"page": {"type": "string"}}},
            "_link_bindings": {"Home": "nav a:nth-of-type(1)", "About": "nav a:nth-of-type(2)"},
        }
        tool = ToolDefinition.from_dict(d)
        assert tool.link_bindings["Home"] == "nav a:nth-of-type(1)"

    def test_from_dict_minimal(self):
        d = {"name": "click", "description": "Click it"}
        tool = ToolDefinition.from_dict(d)
        assert tool.name == "click"
        assert tool.submit_selector == ""


class TestWebExecutorToolLookup:
    """Test tool finding logic (without browser)."""

    def test_find_exact_name(self):
        executor = WebExecutor.__new__(WebExecutor)
        executor._tools = [
            ToolDefinition(name="searchWeb", description="", parameters={"properties": {"q": {}}}),
            ToolDefinition(name="navigate", description="", parameters={}),
        ]
        executor._extraction = None

        assert executor._find_tool("searchWeb").name == "searchWeb"
        assert executor._find_tool("navigate").name == "navigate"

    def test_find_case_insensitive(self):
        executor = WebExecutor.__new__(WebExecutor)
        executor._tools = [
            ToolDefinition(name="searchWeb", description="", parameters={"properties": {"q": {}}}),
        ]
        executor._extraction = None

        assert executor._find_tool("SEARCHWEB").name == "searchWeb"
        assert executor._find_tool("SearchWeb").name == "searchWeb"

    def test_find_semantic_fallback(self):
        executor = WebExecutor.__new__(WebExecutor)
        executor._tools = [
            ToolDefinition(name="submitForm", description="", parameters={"properties": {"q": {}}}),
            ToolDefinition(name="clickBtn", description="", parameters={}),
        ]
        executor._extraction = None

        # "findStuff" contains "find" which is a search keyword  matches first form tool
        result = executor._find_tool("findStuff")
        assert result.name == "submitForm"

    def test_find_returns_none_for_unknown(self):
        executor = WebExecutor.__new__(WebExecutor)
        executor._tools = [
            ToolDefinition(name="searchWeb", description="", parameters={}),
        ]
        executor._extraction = None

        assert executor._find_tool("totallyUnknown") is None


@pytest.mark.timeout(45)
class TestWebExecutorLive:
    """Integration tests that run the executor against real sites."""

    @pytest.mark.asyncio
    async def test_execute_duckduckgo_search(self):
        """Full loop: extract  analyze  execute on DuckDuckGo."""
        from webmcp_gen.extract import extract_page
        from webmcp_gen.analyze import analyze_without_llm

        url = "https://duckduckgo.com"
        extraction = await extract_page(url, timeout=20000)
        tools = analyze_without_llm(extraction)

        async with WebExecutor(url, tools=tools["tools"], extraction=extraction) as executor:
            result = await executor.call("search", {"q": "webmcp test"})

            # Either we get results, or we're honestly told we were blocked.
            assert result.success or result.blocked
            if result.success:
                assert result.url
                # Should have structured items or text
                assert result.items or len(result.text) > 50

    @pytest.mark.asyncio
    async def test_result_to_dict_shape(self):
        """ToolResult.to_dict produces a stable, agent-friendly shape."""
        from webmcp_gen.execute import ToolResult, ResultItem
        r = ToolResult(
            success=True,
            items=[ResultItem(title="Hello", url="https://x.com", snippet="hi")],
            url="https://x.com",
            page_title="X",
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["blocked"] is False
        assert d["items"][0]["title"] == "Hello"
        assert d["items"][0]["url"] == "https://x.com"
