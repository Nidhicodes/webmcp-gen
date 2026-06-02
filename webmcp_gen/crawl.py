"""Crawl a site and merge tools across pages.

Bounded BFS within one origin: extract tools from each page and merge by
(name, parameter-signature), tagging each with its `_source_url`. Reuses a
single browser context so cookies/session carry across pages.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from .extract import extract_from_page, PageExtraction, InteractiveElement, FormField
from .analyze import analyze_without_llm, analyze_with_llm

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    """Merged tools from a multi-page crawl."""
    start_url: str
    pages_visited: list[str] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    site: dict = field(default_factory=dict)

    def to_analysis(self) -> dict:
        """Return in the same shape as analyze_* so it drops into the rest of the pipeline."""
        return {"tools": self.tools, "site": self.site,
                "_pages_visited": self.pages_visited}


def _same_origin(url: str, origin: str) -> bool:
    """Same registrable origin, ignoring http/https scheme differences
    (sites often redirect between them; we don't want to treat that as off-site)."""
    try:
        a, b = urlparse(url), urlparse(origin)
        return (a.scheme in ("http", "https")
                and a.netloc == b.netloc)
    except Exception:
        return False


def _normalize(url: str) -> str:
    """Canonicalize for dedup: force https, strip fragments and trailing slashes."""
    p = urlparse(url)
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    path = p.path.rstrip("/") or "/"
    return f"{scheme}://{p.netloc}{path}" + (f"?{p.query}" if p.query else "")


def _tool_signature(tool: dict) -> tuple:
    """A signature for deduping tools across pages."""
    props = tuple(sorted(tool.get("parameters", {}).get("properties", {}).keys()))
    return (tool.get("name", ""), props)


async def _discover_links(page, origin: str, path_prefix: str | None) -> list[str]:
    """Collect in-origin navigable links from the current page."""
    hrefs = await page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(h => h && !h.startsWith('javascript:') && !h.startsWith('mailto:'))
    """)
    out = []
    seen = set()
    for h in hrefs:
        if not _same_origin(h, origin):
            continue
        norm = _normalize(h)
        if norm in seen:
            continue
        if path_prefix and urlparse(norm).path and not urlparse(norm).path.startswith(path_prefix):
            continue
        seen.add(norm)
        out.append(norm)
    return out


async def crawl_site(
    start_url: str,
    max_pages: int = 5,
    max_depth: int = 2,
    same_path_prefix: bool = False,
    llm: bool = False,
    model: str = "gpt-4o-mini",
    base_url: str = "https://api.openai.com/v1",
    stealth: bool = True,
    timeout: int = 30000,
) -> CrawlResult:
    """Crawl within an origin and merge tools from every visited page.

    Args:
        start_url: Where to begin.
        max_pages: Hard cap on pages visited.
        max_depth: BFS depth limit from the start page.
        same_path_prefix: If True, only follow links sharing the start path prefix.
        llm: Use LLM analysis per page (slower, costs tokens).
        stealth: Apply anti-detection.

    Returns:
        A CrawlResult with merged, deduplicated tools.
    """
    from playwright.async_api import async_playwright
    from .stealth import (
        stealth_context_options, apply_stealth, STEALTH_LAUNCH_ARGS, STEALTH_USER_AGENT,
    )

    origin = start_url
    path_prefix = urlparse(_normalize(start_url)).path if same_path_prefix else None

    result = CrawlResult(start_url=start_url)
    merged: dict[tuple, dict] = {}
    seen_pages: set[str] = set()
    # BFS queue of (url, depth)
    queue: list[tuple[str, int]] = [(_normalize(start_url), 0)]

    async with async_playwright() as p:
        launch_args = STEALTH_LAUNCH_ARGS if stealth else []
        browser = await p.chromium.launch(headless=True, args=launch_args)
        if stealth:
            context = await browser.new_context(**stealth_context_options())
            await apply_stealth(context)
        else:
            context = await browser.new_context(user_agent=STEALTH_USER_AGENT)
        page = await context.new_page()

        try:
            while queue and len(result.pages_visited) < max_pages:
                url, depth = queue.pop(0)
                if url in seen_pages:
                    continue
                seen_pages.add(url)

                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                    if resp and resp.status >= 400:
                        logger.info(f"skip {url} (HTTP {resp.status})")
                        continue
                    await page.wait_for_timeout(1200)
                except Exception as e:
                    logger.info(f"skip {url}: {e}")
                    continue

                result.pages_visited.append(url)

                # Extract + analyze this page
                extraction = await extract_from_page(page)
                if llm:
                    analysis = await analyze_with_llm(extraction, model=model, base_url=base_url)
                else:
                    analysis = analyze_without_llm(extraction)

                if not result.site:
                    result.site = analysis.get("site", {})

                # Merge tools (skip pure "navigate" duplicates)
                for tool in analysis.get("tools", []):
                    sig = _tool_signature(tool)
                    if sig in merged:
                        continue
                    tool = dict(tool)
                    tool["_source_url"] = url
                    merged[sig] = tool

                # Enqueue child links
                if depth < max_depth:
                    links = await _discover_links(page, origin, path_prefix)
                    for link in links:
                        if link not in seen_pages:
                            queue.append((link, depth + 1))
        finally:
            await context.close()
            await browser.close()

    result.tools = list(merged.values())
    if not result.site:
        result.site = {"name": start_url, "description": ""}
    return result
