"""Measure reliability across a suite of sites.

Runs extract -> analyze -> execute against a fixed list and reports
success/blocked/failed rates, with a per-case wall-clock deadline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from .extract import extract_page
from .analyze import analyze_without_llm, analyze_with_llm
from .execute import WebExecutor


@dataclass
class BenchCase:
    url: str
    args: dict
    tool: str = "auto"# "auto" picks first search tool
    kind: str = "search"# "search" | "navigate"
    note: str = ""


@dataclass
class BenchResult:
    url: str
    extracted: int = 0
    tools: int = 0
    tool_called: str = ""
    success: bool = False
    blocked: bool = False
    items: int = 0
    error: str = ""
    elapsed: float = 0.0


# Sites chosen to span the difficulty spectrum, from automation-friendly
# scraping sandboxes to hardened production sites. Each is tagged with what we
# expect so the benchmark output is interpretable.
DEFAULT_SUITE = [
    # --- Automation-friendly sandboxes (should succeed) ---
    BenchCase("https://books.toscrape.com", {"page": "Travel"},
              kind="navigate", note="scraping sandbox, nav"),
    BenchCase("https://quotes.toscrape.com", {"page": "Login"},
              kind="navigate", note="scraping sandbox, nav"),
    BenchCase("https://quotes.toscrape.com/search.aspx", {"author": "Albert Einstein"},
              note="quotes cascading-select form"),
    BenchCase("https://www.scrapethissite.com/pages/forms/",
              {"q": "boston"}, note="hockey team search form"),
    BenchCase("https://www.scrapethissite.com/pages/", {"page": "Hockey"},
              kind="navigate", note="lessons index nav"),
    BenchCase("https://webscraper.io/test-sites/e-commerce/allinone",
              {"page": "Computers"}, kind="navigate", note="e-commerce test site"),
    BenchCase("https://the-internet.herokuapp.com/login",
              {"q": "tomsmith"}, note="login form test page"),

    # --- Real search engines / wikis (often work, sometimes throttle) ---
    BenchCase("https://news.ycombinator.com", {"q": "rust"},
              note="HN Algolia search form"),
    BenchCase("https://en.wikipedia.org", {"q": "alan turing"},
              note="wikipedia search"),
    BenchCase("https://www.startpage.com", {"q": "webmcp"},
              note="privacy search engine"),

    # --- Known hard bot-walls (expected blocked — proves honest reporting) ---
    BenchCase("https://duckduckgo.com", {"q": "webmcp"},
              note="known behavioral bot-wall"),
]


def _pick_tool(case: BenchCase, tools: list[dict]) -> Optional[str]:
    if case.tool != "auto":
        return case.tool
    if case.kind == "navigate":
        for t in tools:
            if t["name"] == "navigate":
                return "navigate"
    # search: first tool with a text-ish parameter
    for t in tools:
        if "search" in t["name"].lower():
            return t["name"]
    for t in tools:
        if t.get("parameters", {}).get("properties"):
            return t["name"]
    return tools[0]["name"] if tools else None


def _normalize_args(case: BenchCase, tools: list[dict], tool_name: str) -> dict:
    """Map generic args (q/page) onto the actual tool's parameter names."""
    tool = next((t for t in tools if t["name"] == tool_name), None)
    if not tool:
        return case.args
    props = list(tool.get("parameters", {}).get("properties", {}).keys())

    # If the args already match real parameter names, pass them through.
    if all(k in props for k in case.args):
        return case.args

    if case.kind == "navigate":
        if "page" in props:
            return {"page": case.args.get("page", "")}
        return case.args
    # search: map "q" onto the first parameter
    if "q" in case.args and props:
        if "q" in props:
            return {"q": case.args["q"]}
        return {props[0]: case.args["q"]}
    return case.args


async def run_case(case: BenchCase, llm: bool, model: str, base_url: str,
                   stealth: bool, deadline: float = 45.0) -> BenchResult:
    """Run a single benchmark case with a hard wall-clock deadline."""
    try:
        return await asyncio.wait_for(
            _run_case_inner(case, llm, model, base_url, stealth),
            timeout=deadline,
        )
    except asyncio.TimeoutError:
        return BenchResult(url=case.url, error=f"exceeded {deadline}s deadline",
                           elapsed=deadline)


async def _run_case_inner(case: BenchCase, llm: bool, model: str, base_url: str,
                          stealth: bool) -> BenchResult:
    t0 = time.time()
    res = BenchResult(url=case.url)
    try:
        ext = await extract_page(case.url, timeout=30000, stealth=stealth)
        res.extracted = len(ext.elements)
        if llm:
            analysis = await analyze_with_llm(ext, model=model, base_url=base_url)
        else:
            analysis = analyze_without_llm(ext)
        tools = analysis.get("tools", [])
        res.tools = len(tools)

        tool_name = _pick_tool(case, tools)
        if not tool_name:
            res.error = "no callable tool generated"
            res.elapsed = time.time() - t0
            return res
        res.tool_called = tool_name

        args = _normalize_args(case, tools, tool_name)
        async with WebExecutor(case.url, tools=tools, extraction=ext,
                               stealth=stealth) as ex:
            r = await ex.call(tool_name, args)
            res.success = r.success
            res.blocked = r.blocked
            res.items = len(r.items)
            res.error = r.error
    except Exception as e:
        res.error = str(e)
    res.elapsed = time.time() - t0
    return res


async def run_suite(suite: list[BenchCase], llm: bool, model: str,
                    base_url: str, stealth: bool) -> list[BenchResult]:
    print(f"Benchmark: {len(suite)} sites | "
          f"{'LLM:' + model if llm else 'heuristic'} | "
          f"stealth={'on' if stealth else 'off'}\n", file=sys.stderr)

    results = []
    for case in suite:
        print(f"{case.url} ({case.note})...", file=sys.stderr)
        r = await run_case(case, llm, model, base_url, stealth)
        status = " success" if r.success else ("⊘ blocked" if r.blocked else " failed")
        print(f"{status}  tools={r.tools} items={r.items} "
              f"{r.elapsed:.1f}s {r.error[:50]}", file=sys.stderr)
        results.append(r)

    # Summary
    n = len(results)
    succ = sum(1 for r in results if r.success)
    blocked = sum(1 for r in results if r.blocked)
    failed = sum(1 for r in results if not r.success and not r.blocked)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"RESULTS: {succ}/{n} success | {blocked} blocked | {failed} failed",
          file=sys.stderr)
    print(f"Success rate (excl. bot-walls): "
          f"{succ}/{succ + failed} = "
          f"{100*succ/(succ+failed) if (succ+failed) else 0:.0f}%", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    return results


def _tier_of(note: str) -> str:
    """Extract the tier tag from a case note like '[sandbox] ...'."""
    if note.startswith("[") and "]" in note:
        return note[1:note.index("]")].split("/")[0]
    return "open"


def write_dashboard(results: list[BenchResult], suite_cases: list,
                    path: str, mode: str) -> None:
    """Write a markdown success dashboard, grouped by tier."""
    import datetime

    # Pair results with their cases for tier info
    note_by_url = {}
    for c in suite_cases:
        note_by_url.setdefault(c.url, c.note)

    tiers: dict[str, list[BenchResult]] = {}
    for r in results:
        tier = _tier_of(note_by_url.get(r.url, ""))
        tiers.setdefault(tier, []).append(r)

    lines = [
        "# webmcp-gen reliability dashboard",
        "",
        f"_Generated {datetime.date.today().isoformat()} · mode: {mode} · "
        f"{len(results)} sites_",
        "",
    ]

    # Overall (excluding walled tier from the headline rate)
    total = len(results)
    succ = sum(1 for r in results if r.success)
    blocked = sum(1 for r in results if r.blocked)
    failed = sum(1 for r in results if not r.success and not r.blocked)
    non_walled = succ + failed
    rate = (100 * succ / non_walled) if non_walled else 0
    lines += [
        "## Summary",
        "",
        f"- **{succ}/{total}** succeeded",
        f"- **{blocked}** blocked (bot-walls, honestly reported)",
        f"- **{failed}** failed",
        f"- **Success rate excluding bot-walls: {rate:.0f}%** ({succ}/{non_walled})",
        "",
    ]

    tier_order = ["sandbox", "open", "guarded", "walled"]
    for tier in tier_order:
        rs = tiers.get(tier)
        if not rs:
            continue
        t_succ = sum(1 for r in rs if r.success)
        lines += [
            f"## Tier: {tier} ({t_succ}/{len(rs)} success)",
            "",
            "| Site | Result | Tools | Items | Time |",
            "|------|--------|-------|-------|------|",
        ]
        for r in rs:
            status = " success" if r.success else ("⊘ blocked" if r.blocked else " failed")
            lines.append(
                f"| {r.url} | {status} | {r.tools} | {r.items} | {r.elapsed:.1f}s |"
            )
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Benchmark webmcp-gen reliability")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--groq", action="store_true")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--no-stealth", action="store_true")
    parser.add_argument("--suite", default="default",
                        help="Which suite: default, full, sandbox, open, guarded")
    parser.add_argument("--dashboard", metavar="FILE",
                        help="Write a markdown success dashboard to FILE")
    parser.add_argument("--output", "-o")
    args = parser.parse_args()

    if args.groq:
        args.llm = True
        args.model = "llama-3.3-70b-versatile"
        args.base_url = "https://api.groq.com/openai/v1"

    if args.suite == "default":
        suite = DEFAULT_SUITE
    else:
        from .suite import get_suite
        suite = get_suite(args.suite)

    results = asyncio.run(run_suite(
        suite, llm=args.llm, model=args.model,
        base_url=args.base_url, stealth=not args.no_stealth,
    ))

    if args.output:
        with open(args.output, "w") as f:
            json.dump([r.__dict__ for r in results], f, indent=2)
        print(f"Saved to {args.output}", file=sys.stderr)

    if args.dashboard:
        mode = f"LLM ({args.model})" if args.llm else "heuristic"
        write_dashboard(results, suite, args.dashboard, mode)
        print(f"Dashboard written to {args.dashboard}", file=sys.stderr)


if __name__ == "__main__":
    main()
