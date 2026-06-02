"""Tests for the WebMCP spec-compliance validator."""

import pytest

from webmcp_gen.validate import (
    validate_tools,
    is_compliant,
    format_report,
    ValidationIssue,
)
from webmcp_gen.analyze import analyze_without_llm


class TestValidator:
    def test_valid_tool_passes(self):
        analysis = {
            "tools": [
                {
                    "name": "searchWeb",
                    "description": "Search the web for a query",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "Term"}},
                        "required": ["query"],
                    },
                }
            ]
        }
        assert validate_tools(analysis) == []
        assert is_compliant(analysis)

    def test_missing_tools_array(self):
        issues = validate_tools({})
        assert any(i.severity == "error" for i in issues)

    def test_invalid_name(self):
        analysis = {"tools": [{"name": "search web!", "description": "x",
                               "parameters": {"type": "object", "properties": {}}}]}
        issues = validate_tools(analysis)
        assert any("valid identifier" in i.message for i in issues)
        assert not is_compliant(analysis)

    def test_duplicate_names(self):
        analysis = {"tools": [
            {"name": "search", "description": "a", "parameters": {"type": "object", "properties": {}}},
            {"name": "search", "description": "b", "parameters": {"type": "object", "properties": {}}},
        ]}
        issues = validate_tools(analysis)
        assert any("duplicate" in i.message for i in issues)

    def test_invalid_property_type(self):
        analysis = {"tools": [{
            "name": "x", "description": "desc here",
            "parameters": {"type": "object", "properties": {"p": {"type": "nonsense"}}},
        }]}
        issues = validate_tools(analysis)
        assert any("invalid type" in i.message for i in issues)
        assert not is_compliant(analysis)

    def test_required_references_undeclared_property(self):
        analysis = {"tools": [{
            "name": "x", "description": "desc here",
            "parameters": {"type": "object", "properties": {"a": {"type": "string"}},
                           "required": ["b"]},
        }]}
        issues = validate_tools(analysis)
        assert any("not a declared property" in i.message for i in issues)

    def test_schema_type_must_be_object(self):
        analysis = {"tools": [{
            "name": "x", "description": "desc here",
            "parameters": {"type": "array", "properties": {}},
        }]}
        issues = validate_tools(analysis)
        assert any("must be 'object'" in i.message for i in issues)

    def test_missing_description_is_warning_not_error(self):
        analysis = {"tools": [{
            "name": "x",
            "parameters": {"type": "object", "properties": {}},
        }]}
        issues = validate_tools(analysis)
        assert all(i.severity == "warning" for i in issues if "description" in i.message)
        assert is_compliant(analysis)  # warnings don't break compliance

    def test_enum_must_be_list(self):
        analysis = {"tools": [{
            "name": "x", "description": "desc here",
            "parameters": {"type": "object",
                           "properties": {"p": {"type": "string", "enum": "notalist"}}},
        }]}
        issues = validate_tools(analysis)
        assert any("enum must be an array" in i.message for i in issues)

    def test_format_report_clean(self):
        assert "compliant" in format_report([]).lower()

    def test_format_report_with_issues(self):
        issues = [ValidationIssue("error", "x", "bad thing")]
        report = format_report(issues)
        assert "error" in report.lower()
        assert "bad thing" in report


class TestHeuristicOutputIsCompliant:
    """The heuristic analyzer's output must always be spec-compliant."""

    def test_simple_search_compliant(self, simple_search_extraction):
        analysis = analyze_without_llm(simple_search_extraction)
        issues = [i for i in validate_tools(analysis) if i.severity == "error"]
        assert issues == [], f"Heuristic output not compliant: {issues}"

    def test_complex_page_compliant(self, complex_extraction):
        analysis = analyze_without_llm(complex_extraction)
        issues = [i for i in validate_tools(analysis) if i.severity == "error"]
        assert issues == [], f"Heuristic output not compliant: {issues}"
