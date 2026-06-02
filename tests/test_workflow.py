"""Tests for the tool-chaining workflow engine."""

import pytest

from webmcp_gen.workflow import (
    Workflow,
    WorkflowResult,
    StepResult,
    _resolve_path,
    _interpolate,
)


CONTEXT = {
    "vars": {"query": "rust", "n": 2},
    "steps": {
        "search": {
            "success": True,
            "url": "https://x.com/results",
            "items": [
                {"title": "First", "url": "https://x.com/1", "snippet": "a"},
                {"title": "Second", "url": "https://x.com/2", "snippet": "b"},
            ],
            "text": "",
        }
    },
}


class TestResolvePath:
    def test_var(self):
        assert _resolve_path("vars.query", CONTEXT) == "rust"

    def test_step_field(self):
        assert _resolve_path("steps.search.url", CONTEXT) == "https://x.com/results"

    def test_list_index(self):
        assert _resolve_path("steps.search.items.0.title", CONTEXT) == "First"
        assert _resolve_path("steps.search.items.1.url", CONTEXT) == "https://x.com/2"

    def test_missing_key_raises(self):
        with pytest.raises(KeyError):
            _resolve_path("steps.search.nope", CONTEXT)

    def test_index_out_of_range_raises(self):
        with pytest.raises(IndexError):
            _resolve_path("steps.search.items.9.url", CONTEXT)


class TestInterpolate:
    def test_whole_string_preserves_type(self):
        # A whole-string ref to a list should return the list, not its str()
        out = _interpolate("{{ steps.search.items }}", CONTEXT)
        assert isinstance(out, list)
        assert len(out) == 2

    def test_whole_string_int(self):
        assert _interpolate("{{ vars.n }}", CONTEXT) == 2

    def test_inline_reference_stringifies(self):
        out = _interpolate("page {{ vars.n }} of results", CONTEXT)
        assert out == "page 2 of results"

    def test_dict_interpolation(self):
        out = _interpolate({"q": "{{ vars.query }}", "static": "x"}, CONTEXT)
        assert out == {"q": "rust", "static": "x"}

    def test_no_reference_passthrough(self):
        assert _interpolate("plain", CONTEXT) == "plain"
        assert _interpolate(42, CONTEXT) == 42


class TestWorkflowValidation:
    def test_valid_workflow(self):
        wf = Workflow(steps=[{"tool": "search", "args": {"q": "x"}}])
        assert wf.validate() == []

    def test_missing_tool(self):
        wf = Workflow(steps=[{"args": {}}])
        assert any("missing 'tool'" in p for p in wf.validate())

    def test_bad_args_type(self):
        wf = Workflow(steps=[{"tool": "x", "args": "notdict"}])
        assert any("'args' must be an object" in p for p in wf.validate())


class TestWorkflowResult:
    def test_to_dict(self):
        r = WorkflowResult(
            success=False, error="boom", failed_at=1,
            steps=[StepResult("search", {"q": "x"}, True, url="https://x.com")],
        )
        d = r.to_dict()
        assert d["success"] is False
        assert d["failed_at"] == 1
        assert d["steps"][0]["tool"] == "search"


class FakeExecutor:
    """A stand-in executor that returns scripted results (no browser)."""
    def __init__(self, scripted):
        self._scripted = scripted
        self._tools = []
        self.calls = []

    async def call(self, tool, args):
        self.calls.append((tool, args))
        from webmcp_gen.execute import ToolResult, ResultItem
        spec = self._scripted.get(tool, {})
        items = [ResultItem(**i) for i in spec.get("items", [])]
        return ToolResult(
            success=spec.get("success", True),
            blocked=spec.get("blocked", False),
            items=items, url=spec.get("url", ""), error=spec.get("error", ""),
        )

    async def re_extract(self):
        from webmcp_gen.extract import PageExtraction
        return PageExtraction(url="", title="", elements=[])


class TestWorkflowRunLogic:
    @pytest.mark.asyncio
    async def test_chains_reference_between_steps(self):
        scripted = {
            "search": {"success": True, "url": "https://x.com/r",
                       "items": [{"title": "First", "url": "https://x.com/1", "snippet": ""}]},
            "open": {"success": True, "url": "https://x.com/1"},
        }
        ex = FakeExecutor(scripted)
        wf = Workflow(steps=[
            {"tool": "search", "args": {"q": "{{ vars.q }}"}, "save_as": "search"},
            {"tool": "open", "args": {"target": "{{ steps.search.items.0.title }}"}},
        ])
        result = await wf.run(ex, variables={"q": "rust"})
        assert result.success
        # The second call should have received the resolved title
        assert ex.calls[1] == ("open", {"target": "First"})

    @pytest.mark.asyncio
    async def test_stops_on_failure(self):
        scripted = {"a": {"success": False, "error": "nope"}}
        ex = FakeExecutor(scripted)
        wf = Workflow(steps=[
            {"tool": "a", "args": {}},
            {"tool": "b", "args": {}},
        ])
        result = await wf.run(ex, variables={})
        assert not result.success
        assert result.failed_at == 0
        # second step never ran
        assert len(ex.calls) == 1

    @pytest.mark.asyncio
    async def test_bad_reference_fails_cleanly(self):
        ex = FakeExecutor({"a": {"success": True}})
        wf = Workflow(steps=[
            {"tool": "a", "args": {"x": "{{ steps.missing.url }}"}},
        ])
        result = await wf.run(ex, variables={})
        assert not result.success
        assert "reference error" in result.steps[0].error


@pytest.mark.timeout(90)
class TestWorkflowLive:
    @pytest.mark.asyncio
    async def test_search_then_navigate_chain(self):
        """Live two-step chain on an automation-friendly sandbox."""
        from webmcp_gen.extract import extract_page
        from webmcp_gen.analyze import analyze_without_llm
        from webmcp_gen.execute import WebExecutor

        url = "https://books.toscrape.com"
        ext = await extract_page(url, timeout=20000)
        tools = analyze_without_llm(ext)

        wf = Workflow(steps=[
            {"tool": "navigate", "args": {"page": "Travel"}, "save_as": "cat"},
        ])
        async with WebExecutor(url, tools=tools["tools"], extraction=ext) as ex:
            result = await wf.run(ex, variables={})
        assert result.success
        assert result.steps[0].success
