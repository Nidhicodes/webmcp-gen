"""Tests for the analyze module (heuristic tool generation)."""

import pytest

from webmcp_gen.analyze import (
    analyze_without_llm,
    build_user_prompt,
    _infer_tool_name,
    _clean_param_name,
    _field_type_to_json_type,
)
from webmcp_gen.extract import InteractiveElement, FormField


class TestHeuristicAnalysis:
    """Test heuristic tool generation."""

    def test_simple_search_form(self, simple_search_extraction):
        result = analyze_without_llm(simple_search_extraction)

        assert "tools" in result
        assert "site" in result
        assert len(result["tools"]) == 1  # just the form, no nav links

        tool = result["tools"][0]
        # Name is inferred from aria_label "Search form""searchForm"
        assert tool["name"] == "searchForm"
        assert "parameters" in tool
        assert "q" in tool["parameters"]["properties"]
        assert "lang" in tool["parameters"]["properties"]
        assert tool["parameters"]["properties"]["q"]["_selector"] == "#search-input"
        assert tool["parameters"]["properties"]["lang"]["_selector"] == "#lang-select"
        assert tool["parameters"]["properties"]["lang"]["enum"] == ["English", "Spanish", "French"]
        assert "q" in tool["parameters"].get("required", [])

    def test_complex_page_filtering(self, complex_extraction):
        result = analyze_without_llm(complex_extraction)
        tools = result["tools"]
        tool_names = [t["name"] for t in tools]

        # Should have: login form, search form, dark mode button, navigate
        assert "loginForm" in tool_names
        assert "darkMode" in tool_names
        assert "navigate" in tool_names

        # Should NOT have promotional/FAQ buttons (filtered by length/question mark)
        for t in tools:
            assert "whyChoose" not in t["name"].lower()
            assert "howDoes" not in t["name"].lower()

    def test_navigate_tool_has_enum(self, complex_extraction):
        result = analyze_without_llm(complex_extraction)
        nav_tool = next(t for t in result["tools"] if t["name"] == "navigate")

        assert "page" in nav_tool["parameters"]["properties"]
        page_param = nav_tool["parameters"]["properties"]["page"]
        assert "enum" in page_param
        assert "Home" in page_param["enum"]
        assert "Docs" in page_param["enum"]
        assert "Pricing" in page_param["enum"]

    def test_navigate_tool_has_link_bindings(self, complex_extraction):
        result = analyze_without_llm(complex_extraction)
        nav_tool = next(t for t in result["tools"] if t["name"] == "navigate")

        assert "_link_bindings" in nav_tool
        assert "Home" in nav_tool["_link_bindings"]
        assert nav_tool["_link_bindings"]["Home"] == "nav a:nth-of-type(1)"

    def test_empty_page(self, empty_extraction):
        result = analyze_without_llm(empty_extraction)
        assert result["tools"] == []
        assert result["site"]["name"] == "Static Page"

    def test_deduplication(self):
        """Duplicate form names should get suffixed."""
        from webmcp_gen.extract import PageExtraction
        extraction = PageExtraction(
            url="https://example.com",
            title="Test",
            elements=[
                InteractiveElement(
                    kind="form", text="Search", selector="form:nth-of-type(1)",
                    element_index=0,
                    fields=[FormField(tag="input", type="text", name="q", selector="#q1")],
                ),
                InteractiveElement(
                    kind="form", text="Search", selector="form:nth-of-type(2)",
                    element_index=1,
                    fields=[FormField(tag="input", type="text", name="q", selector="#q2")],
                ),
            ],
        )
        result = analyze_without_llm(extraction)
        names = [t["name"] for t in result["tools"]]
        # Should not have duplicates
        assert len(names) == len(set(names))

    def test_submit_selector_is_set(self, simple_search_extraction):
        result = analyze_without_llm(simple_search_extraction)
        tool = result["tools"][0]
        assert tool["_submit_selector"] == "form#search-form"

    def test_site_metadata(self, simple_search_extraction):
        result = analyze_without_llm(simple_search_extraction)
        assert result["site"]["name"] == "Example Search"
        assert result["site"]["description"] == "A simple search engine"


class TestHelpers:
    """Test helper functions."""

    def test_infer_tool_name_from_text(self):
        el = InteractiveElement(kind="button", text="Sign Up Now")
        assert _infer_tool_name(el) == "signUpNow"

    def test_infer_tool_name_from_aria(self):
        el = InteractiveElement(kind="form", text="Go", aria_label="Search flights")
        assert _infer_tool_name(el) == "searchFlights"

    def test_infer_tool_name_special_chars(self):
        el = InteractiveElement(kind="button", text="Save & Continue ")
        assert _infer_tool_name(el) == "saveContinue"

    def test_infer_tool_name_empty(self):
        el = InteractiveElement(kind="button", text="")
        # With empty text, falls through to "action" (the default fallback)
        assert _infer_tool_name(el) == "action"

    def test_clean_param_name(self):
        assert _clean_param_name("user_name") == "userName"
        assert _clean_param_name("email-address") == "emailAddress"
        assert _clean_param_name("q") == "q"
        assert _clean_param_name("123abc") == "field123abc"
        assert _clean_param_name("") == "param"
        assert _clean_param_name("first name") == "firstName"

    def test_field_type_mapping(self):
        assert _field_type_to_json_type("number") == "number"
        assert _field_type_to_json_type("checkbox") == "boolean"
        assert _field_type_to_json_type("email") == "string"
        assert _field_type_to_json_type("date") == "string"
        assert _field_type_to_json_type("unknown") == "string"


class TestUserPrompt:
    """Test LLM prompt building."""

    def test_prompt_includes_url(self, simple_search_extraction):
        prompt = build_user_prompt(simple_search_extraction)
        assert "https://example.com" in prompt
        assert "Example Search" in prompt

    def test_prompt_includes_fields(self, simple_search_extraction):
        prompt = build_user_prompt(simple_search_extraction)
        assert "Search" in prompt
        assert "#search-input" in prompt
        assert "REQUIRED" in prompt

    def test_prompt_includes_selectors(self, simple_search_extraction):
        prompt = build_user_prompt(simple_search_extraction)
        assert "selector" in prompt.lower()
