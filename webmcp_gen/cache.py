"""Disk cache for extractions and analyses, keyed by (url, mode, model).

Avoids re-extracting unchanged pages and re-paying for LLM analysis. Entries
expire after a TTL. Location: ~/.cache/webmcp-gen/ (override WEBMCP_CACHE_DIR).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional


def _cache_dir() -> Path:
    override = os.environ.get("WEBMCP_CACHE_DIR")
    if override:
        d = Path(override)
    else:
        d = Path.home() / ".cache" / "webmcp-gen"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(url: str, mode: str, model: str = "") -> str:
    raw = f"{url}|{mode}|{model}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def get(url: str, mode: str, model: str = "", ttl_seconds: int = 3600) -> Optional[dict]:
    """Return a cached result if present and fresh, else None."""
    path = _cache_dir() / f"{_key(url, mode, model)}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            entry = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if time.time() - entry.get("_cached_at", 0) > ttl_seconds:
        return None
    return entry.get("data")


def put(url: str, mode: str, data: dict, model: str = "") -> None:
    """Store a result in the cache."""
    path = _cache_dir() / f"{_key(url, mode, model)}.json"
    entry = {
        "_cached_at": time.time(),
        "_url": url,
        "_mode": mode,
        "_model": model,
        "data": data,
    }
    try:
        with open(path, "w") as f:
            json.dump(entry, f)
    except OSError:
        pass  # cache failures are non-fatal


def clear() -> int:
    """Clear all cache entries. Returns the number of files removed."""
    count = 0
    for f in _cache_dir().glob("*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count
