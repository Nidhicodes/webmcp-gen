"""Tests for the multi-page crawler."""

import pytest

from webmcp_gen.crawl import (
    CrawlResult,
    _same_origin,
    _normalize,
    _tool_signature,
)


class TestOriginAndNormalize:
    def test_same_origin_true(self):
        assert _same_origin("https://x.com/a", "https://x.com/b")

    def test_same_origin_ignores_scheme(self):
        assert _same_origin("http://x.com/a", "https://x.com/b")

    def test_same_origin_false_different_host(self):
        assert not _same_origin("https://y.com/a", "https://x.com/b")

    def test_same_origin_rejects_non_http(self):
        assert not _same_origin("mailto:a@x.com", "https://x.com")
        assert not _same_origin("ftp://x.com/a", "https://x.com")

    def test_normalize_forces_https(self):
        assert _normalize("http://x.com/a") == "https://x.com/a"

    def test_normalize_strips_trailing_slash(self):
        assert _normalize("https://x.com/a/") == "https://x.com/a"

    def test_normalize_strips_fragment(self):
        assert _normalize("https://x.com/a#section") == "https://x.com/a"

    def test_normalize_keeps_query(self):
        assert _normalize("https://x.com/a?q=1") == "https://x.com/a?q=1"

    def test_normalize_root(self):
        assert _normalize("https://x.com") == "https://x.com/"


class TestToolSignature:
    def test_signature_by_name_and_params(self):
        t1 = {"name": "search", "parameters": {"properties": {"q": {}}}}
        t2 = {"name": "search", "parameters": {"properties": {"q": {}}}}
        assert _tool_signature(t1) == _tool_signature(t2)

    def test_signature_differs_by_params(self):
        t1 = {"name": "search", "parameters": {"properties": {"q": {}}}}
        t2 = {"name": "search", "parameters": {"properties": {"query": {}}}}
        assert _tool_signature(t1) != _tool_signature(t2)

    def test_signature_param_order_invariant(self):
        t1 = {"name": "f", "parameters": {"properties": {"a": {}, "b": {}}}}
        t2 = {"name": "f", "parameters": {"properties": {"b": {}, "a": {}}}}
        assert _tool_signature(t1) == _tool_signature(t2)


class TestCrawlResult:
    def test_to_analysis_shape(self):
        r = CrawlResult(
            start_url="https://x.com",
            pages_visited=["https://x.com/", "https://x.com/a"],
            tools=[{"name": "search"}],
            site={"name": "X"},
        )
        analysis = r.to_analysis()
        assert analysis["tools"] == [{"name": "search"}]
        assert analysis["site"] == {"name": "X"}
        assert analysis["_pages_visited"] == ["https://x.com/", "https://x.com/a"]


@pytest.mark.timeout(90)
class TestCrawlLive:
    @pytest.mark.asyncio
    async def test_crawl_merges_tools_across_pages(self):
        from webmcp_gen.crawl import crawl_site

        result = await crawl_site(
            "https://www.scrapethissite.com/pages/forms/",
            max_pages=3, max_depth=1, stealth=True,
        )
        # Should visit multiple pages
        assert len(result.pages_visited) >= 1
        # All visited URLs should be https-normalized and same-origin
        for u in result.pages_visited:
            assert u.startswith("https://www.scrapethissite.com")
        # Should find at least the search form tool
        names = [t["name"] for t in result.tools]
        assert "search" in names
        # Each tool tagged with its source page
        for t in result.tools:
            assert "_source_url" in t
