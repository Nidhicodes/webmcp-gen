"""Tests for the disk cache."""

import os
import time
import pytest

from webmcp_gen import cache


@pytest.fixture(autouse=True)
def temp_cache(tmp_path, monkeypatch):
    """Point the cache at a temp dir for every test."""
    monkeypatch.setenv("WEBMCP_CACHE_DIR", str(tmp_path))
    yield tmp_path


class TestCache:
    def test_put_and_get(self):
        data = {"tools": [{"name": "search"}]}
        cache.put("https://x.com", "heuristic", data)
        result = cache.get("https://x.com", "heuristic")
        assert result == data

    def test_miss_returns_none(self):
        assert cache.get("https://never-cached.com", "heuristic") is None

    def test_different_modes_are_separate(self):
        cache.put("https://x.com", "heuristic", {"a": 1})
        cache.put("https://x.com", "llm", {"b": 2}, model="gpt-4o")
        assert cache.get("https://x.com", "heuristic") == {"a": 1}
        assert cache.get("https://x.com", "llm", model="gpt-4o") == {"b": 2}

    def test_different_models_are_separate(self):
        cache.put("https://x.com", "llm", {"a": 1}, model="gpt-4o")
        cache.put("https://x.com", "llm", {"b": 2}, model="llama-3.3")
        assert cache.get("https://x.com", "llm", model="gpt-4o") == {"a": 1}
        assert cache.get("https://x.com", "llm", model="llama-3.3") == {"b": 2}

    def test_ttl_expiry(self):
        cache.put("https://x.com", "heuristic", {"a": 1})
        # Fresh
        assert cache.get("https://x.com", "heuristic", ttl_seconds=3600) == {"a": 1}
        # Expired
        assert cache.get("https://x.com", "heuristic", ttl_seconds=0) is None

    def test_clear(self):
        cache.put("https://a.com", "heuristic", {"a": 1})
        cache.put("https://b.com", "heuristic", {"b": 2})
        n = cache.clear()
        assert n == 2
        assert cache.get("https://a.com", "heuristic") is None

    def test_corrupt_entry_returns_none(self, temp_cache):
        # Write a garbage file with the right name
        from webmcp_gen.cache import _key
        path = temp_cache / f"{_key('https://x.com', 'heuristic', '')}.json"
        path.write_text("not valid json {{{")
        assert cache.get("https://x.com", "heuristic") is None
