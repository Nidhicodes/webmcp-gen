"""Tests for the extract module."""

import pytest

from webmcp_gen.extract import (
    PageExtraction,
    InteractiveElement,
    FormField,
    extract_page,
)


class TestDataclasses:
    """Test the data structures."""

    def test_page_extraction_to_dict(self):
        ext = PageExtraction(url="https://x.com", title="X", elements=[])
        d = ext.to_dict()
        assert d["url"] == "https://x.com"
        assert d["title"] == "X"
        assert d["elements"] == []

    def test_page_extraction_to_json(self):
        ext = PageExtraction(url="https://x.com", title="X", elements=[])
        j = ext.to_json()
        assert '"url": "https://x.com"' in j

    def test_form_field_defaults(self):
        f = FormField(tag="input")
        assert f.type == "text"
        assert f.name == ""
        assert f.required is False
        assert f.options == []

    def test_interactive_element_defaults(self):
        el = InteractiveElement(kind="button")
        assert el.text == ""
        assert el.fields == []
        assert el.element_index == -1

    def test_find_forms(self, simple_search_extraction):
        forms = simple_search_extraction.find_forms()
        assert len(forms) == 1
        assert forms[0].kind == "form"

    def test_find_buttons(self, complex_extraction):
        buttons = complex_extraction.find_buttons()
        assert len(buttons) >= 1

    def test_find_links(self, complex_extraction):
        links = complex_extraction.find_links()
        assert len(links) == 3


@pytest.mark.timeout(45)
class TestLiveExtraction:
    """Integration tests that hit real websites.

    These require network access and Playwright browsers installed.
    Mark with pytest.mark.integration to allow skipping.
    """

    @pytest.mark.asyncio
    async def test_extract_hacker_news(self):
        """HN is simple, stable, and fast — good canary test."""
        result = await extract_page("https://news.ycombinator.com", timeout=20000)

        assert result.url == "https://news.ycombinator.com"
        assert "Hacker News" in result.title
        assert len(result.elements) >= 1

        # Should find the search form
        forms = result.find_forms()
        assert len(forms) >= 1
        # The search form should have a text input
        search_form = forms[0]
        text_fields = [f for f in search_form.fields if f.type in ("text", "search")]
        assert len(text_fields) >= 1

    @pytest.mark.asyncio
    async def test_extract_has_stable_selectors(self):
        """Extracted elements should have non-empty selectors."""
        result = await extract_page("https://news.ycombinator.com", timeout=20000)

        for el in result.elements:
            assert el.selector, f"Element {el.kind} '{el.text}' has no selector"
            if el.kind == "form":
                for field in el.fields:
                    if field.type not in ("hidden",):
                        assert field.selector, (
                            f"Field '{field.name}' in form has no selector"
                        )

    @pytest.mark.asyncio
    async def test_extract_deduplicates(self):
        """Should not return duplicate elements."""
        result = await extract_page("https://news.ycombinator.com", timeout=20000)

        seen = set()
        for el in result.elements:
            key = (el.kind, el.text, el.selector)
            assert key not in seen, f"Duplicate element: {key}"
            seen.add(key)

    @pytest.mark.asyncio
    async def test_extract_timeout_raises(self):
        """Should raise on unreachable URLs."""
        with pytest.raises(Exception):
            await extract_page("https://this-does-not-exist-xyz123.com", timeout=5000)
