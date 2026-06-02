"""Tests for the benchmark harness (logic only — no network in unit tests)."""

import pytest

from webmcp_gen.benchmark import (
    BenchCase,
    BenchResult,
    _pick_tool,
    _normalize_args,
    DEFAULT_SUITE,
)


class TestToolPicking:
    def test_pick_named_tool(self):
        case = BenchCase("https://x.com", {}, tool="searchFoo")
        tools = [{"name": "searchFoo", "parameters": {"properties": {"q": {}}}}]
        assert _pick_tool(case, tools) == "searchFoo"

    def test_pick_navigate(self):
        case = BenchCase("https://x.com", {"page": "Home"}, kind="navigate")
        tools = [
            {"name": "search", "parameters": {"properties": {"q": {}}}},
            {"name": "navigate", "parameters": {"properties": {"page": {}}}},
        ]
        assert _pick_tool(case, tools) == "navigate"

    def test_pick_auto_search(self):
        case = BenchCase("https://x.com", {"q": "test"})
        tools = [
            {"name": "loginUser", "parameters": {"properties": {"email": {}}}},
            {"name": "searchWeb", "parameters": {"properties": {"q": {}}}},
        ]
        assert _pick_tool(case, tools) == "searchWeb"

    def test_pick_returns_none_when_empty(self):
        case = BenchCase("https://x.com", {})
        assert _pick_tool(case, []) is None


class TestArgNormalization:
    def test_navigate_args(self):
        case = BenchCase("https://x.com", {"page": "Travel"}, kind="navigate")
        tools = [{"name": "navigate", "parameters": {"properties": {"page": {}}}}]
        assert _normalize_args(case, tools, "navigate") == {"page": "Travel"}

    def test_search_maps_q_to_first_param(self):
        case = BenchCase("https://x.com", {"q": "rust"})
        tools = [{"name": "searchWeb", "parameters": {"properties": {"searchTerm": {}}}}]
        result = _normalize_args(case, tools, "searchWeb")
        assert result == {"searchTerm": "rust"}

    def test_search_keeps_q_when_param_is_q(self):
        case = BenchCase("https://x.com", {"q": "rust"})
        tools = [{"name": "searchWeb", "parameters": {"properties": {"q": {}}}}]
        assert _normalize_args(case, tools, "searchWeb") == {"q": "rust"}


class TestSuite:
    def test_default_suite_is_valid(self):
        assert len(DEFAULT_SUITE) >= 5
        for case in DEFAULT_SUITE:
            assert case.url.startswith("https://")
            assert case.kind in ("search", "navigate")

    def test_bench_result_defaults(self):
        r = BenchResult(url="https://x.com")
        assert r.success is False
        assert r.blocked is False
        assert r.items == 0


@pytest.mark.timeout(120)
class TestBenchmarkLive:
    """Run the heuristic benchmark and assert a reliability floor.

    This guards against regressions like the navigate dispatch bug. We require
    that every non-bot-walled case either succeeds or is honestly blocked — no
    silent failures.
    """

    @pytest.mark.asyncio
    async def test_no_silent_failures_on_friendly_sites(self):
        from webmcp_gen.benchmark import run_case, BenchCase

        # Automation-friendly sandboxes that must always work, including the
        # cascading-select form that previously hung the executor.
        cases = [
            BenchCase("https://books.toscrape.com", {"page": "Travel"}, kind="navigate"),
            BenchCase("https://www.scrapethissite.com/pages/forms/", {"q": "boston"}),
            BenchCase("https://quotes.toscrape.com/search.aspx",
                      {"author": "Albert Einstein"}),
        ]
        for case in cases:
            r = await run_case(case, llm=False, model="", base_url="", stealth=True)
            assert r.success, f"{case.url} failed: {r.error}"
            assert not r.blocked
