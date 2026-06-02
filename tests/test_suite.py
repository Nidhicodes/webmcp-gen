"""Tests for the curated benchmark suite and dashboard generation."""

import pytest

from webmcp_gen.suite import get_suite, FULL_SUITE, SANDBOX, OPEN, GUARDED
from webmcp_gen.benchmark import _tier_of, write_dashboard, BenchResult, BenchCase


class TestSuite:
    def test_full_suite_size(self):
        assert len(FULL_SUITE) >= 30

    def test_full_is_union_of_tiers(self):
        assert len(FULL_SUITE) == len(SANDBOX) + len(OPEN) + len(GUARDED)

    def test_get_suite_names(self):
        assert get_suite("sandbox") is SANDBOX
        assert get_suite("open") is OPEN
        assert get_suite("guarded") is GUARDED
        assert get_suite("full") is FULL_SUITE
        assert get_suite("unknown") is FULL_SUITE  # defaults to full

    def test_all_cases_valid(self):
        for c in FULL_SUITE:
            assert c.url.startswith("https://")
            assert c.kind in ("search", "navigate")
            assert isinstance(c.args, dict)

    def test_every_case_has_tier_tag(self):
        for c in FULL_SUITE:
            tier = _tier_of(c.note)
            assert tier in ("sandbox", "open", "guarded", "walled")


class TestTierParsing:
    def test_tier_extraction(self):
        assert _tier_of("[sandbox] foo") == "sandbox"
        assert _tier_of("[guarded] bar") == "guarded"
        assert _tier_of("[open/guarded] baz") == "open"# takes first
        assert _tier_of("no tag") == "open"# default


class TestDashboard:
    def test_write_dashboard(self, tmp_path):
        results = [
            BenchResult(url="https://a.com", success=True, tools=2, items=5, elapsed=3.0),
            BenchResult(url="https://b.com", success=False, blocked=True, elapsed=2.0),
            BenchResult(url="https://c.com", success=False, error="boom", elapsed=1.0),
        ]
        cases = [
            BenchCase("https://a.com", {}, note="[sandbox] a"),
            BenchCase("https://b.com", {}, note="[walled] b"),
            BenchCase("https://c.com", {}, note="[open] c"),
        ]
        out = tmp_path / "dash.md"
        write_dashboard(results, cases, str(out), "heuristic")
        content = out.read_text()

        assert "# webmcp-gen reliability dashboard" in content
        assert "Success rate excluding bot-walls" in content
        # a succeeded, c failed  1/2 = 50%
        assert "50%" in content
        assert "Tier: sandbox" in content
        assert "Tier: walled" in content
        assert "https://a.com" in content
