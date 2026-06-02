"""Curated benchmark suite, grouped by difficulty tier.

Tiers: sandbox (automation-friendly), open (no aggressive detection), guarded
(may throttle), walled (hard-blocks headless). The benchmark reports per-tier
rates so the aggregate isn't misleading.
"""

from __future__ import annotations

from .benchmark import BenchCase


# --- Tier: sandbox (automation-friendly, should always work) ---
SANDBOX = [
    BenchCase("https://books.toscrape.com", {"page": "Travel"}, kind="navigate",
              note="[sandbox] book catalog nav"),
    BenchCase("https://books.toscrape.com", {"page": "Mystery"}, kind="navigate",
              note="[sandbox] book catalog nav 2"),
    BenchCase("https://quotes.toscrape.com", {"page": "Login"}, kind="navigate",
              note="[sandbox] quotes nav"),
    BenchCase("https://quotes.toscrape.com/search.aspx", {"author": "Albert Einstein"},
              note="[sandbox] cascading select"),
    BenchCase("https://www.scrapethissite.com/pages/forms/", {"q": "boston"},
              note="[sandbox] hockey search form"),
    BenchCase("https://www.scrapethissite.com/pages/", {"page": "Hockey"},
              kind="navigate", note="[sandbox] lessons nav"),
    BenchCase("https://webscraper.io/test-sites/e-commerce/allinone",
              {"page": "Computers"}, kind="navigate", note="[sandbox] e-commerce nav"),
    BenchCase("https://webscraper.io/test-sites/e-commerce/static",
              {"page": "Phones"}, kind="navigate", note="[sandbox] static e-commerce"),
    BenchCase("https://the-internet.herokuapp.com/login", {"q": "tomsmith"},
              note="[sandbox] login form"),
    BenchCase("https://the-internet.herokuapp.com/", {"page": "Form Authentication"},
              kind="navigate", note="[sandbox] the-internet nav"),
    BenchCase("https://demoqa.com/text-box", {"q": "test"},
              note="[sandbox] demoqa text box"),
    BenchCase("https://httpbin.org/forms/post", {"q": "pizza"},
              note="[sandbox] httpbin form"),
]

# --- Tier: open (public, usually no bot-wall) ---
OPEN = [
    BenchCase("https://en.wikipedia.org", {"q": "alan turing"},
              note="[open] wikipedia search"),
    BenchCase("https://en.wikisource.org", {"q": "shakespeare"},
              note="[open] wikisource search"),
    BenchCase("https://en.wiktionary.org", {"q": "ontology"},
              note="[open] wiktionary search"),
    BenchCase("https://news.ycombinator.com", {"q": "rust"},
              note="[open] HN algolia search"),
    BenchCase("https://www.startpage.com", {"q": "webmcp"},
              note="[open] startpage search"),
    BenchCase("https://lite.duckduckgo.com/lite/", {"q": "playwright"},
              note="[open] ddg lite (form variant)"),
    BenchCase("https://search.marginalia.nu", {"q": "small web"},
              note="[open] marginalia search"),
    BenchCase("https://pypi.org", {"q": "flask"},
              note="[open/guarded] pypi search"),
    BenchCase("https://stackoverflow.com", {"q": "asyncio"},
              note="[guarded] stackoverflow search"),
    BenchCase("https://www.gutenberg.org", {"q": "moby dick"},
              note="[open] project gutenberg"),
    BenchCase("https://archive.org", {"q": "apollo 11"},
              note="[open] internet archive"),
    BenchCase("https://developer.mozilla.org/en-US/", {"q": "fetch"},
              note="[open] MDN search"),
    BenchCase("https://www.python.org", {"q": "asyncio"},
              note="[open] python.org search"),
    BenchCase("https://hn.algolia.com", {"q": "webmcp"},
              note="[open] HN search frontend"),
    BenchCase("https://readthedocs.org", {"q": "sphinx"},
              note="[open] readthedocs search"),
    BenchCase("https://crates.io", {"q": "tokio"},
              note="[guarded] crates.io search"),
    BenchCase("https://www.npmjs.com", {"q": "react"},
              note="[guarded] npm search"),
]

# --- Tier: guarded (real sites; may throttle/challenge) ---
GUARDED = [
    BenchCase("https://github.com", {"q": "playwright"},
              note="[guarded] github search"),
    BenchCase("https://gitlab.com", {"q": "ci"},
              note="[guarded] gitlab search"),
    BenchCase("https://www.reddit.com", {"q": "mcp"},
              note="[guarded] reddit search"),
    BenchCase("https://www.imdb.com", {"q": "inception"},
              note="[guarded] imdb search"),
    BenchCase("https://www.bing.com", {"q": "webmcp"},
              note="[guarded] bing search"),
    BenchCase("https://search.brave.com", {"q": "webmcp"},
              note="[guarded] brave search"),
    BenchCase("https://www.ecosia.org", {"q": "webmcp"},
              note="[guarded] ecosia search"),
    BenchCase("https://duckduckgo.com", {"q": "webmcp"},
              note="[walled] ddg main behavioral wall"),
    BenchCase("https://www.google.com", {"q": "webmcp"},
              note="[walled] google sorry-page"),
    BenchCase("https://www.amazon.com", {"q": "usb cable"},
              note="[walled] amazon bot-wall"),
]

# Full suite
FULL_SUITE = SANDBOX + OPEN + GUARDED


def get_suite(name: str = "full") -> list[BenchCase]:
    return {
        "full": FULL_SUITE,
        "sandbox": SANDBOX,
        "open": OPEN,
        "guarded": GUARDED,
    }.get(name, FULL_SUITE)
