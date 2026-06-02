"""Compare heuristic vs LLM analysis on multiple sites, side by side.

Usage:
    python -m webmcp_gen.compare                 # default site set
    python -m webmcp_gen.compare --url URL       # single site
    python -m webmcp_gen.compare --groq          # via Groq

Requires OPENAI_API_KEY or LLM_API_KEY for the LLM half.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Optional

from .extract import extract_page, PageExtraction
from .analyze import analyze_with_llm, analyze_without_llm


# 5 diverse sites that demonstrate the range of webmcp-gen capabilities
DEFAULT_SITES = [
    "https://www.google.com",          # Simple search form
    "https://en.wikipedia.org",        # Search + navigation
    "https://news.ycombinator.com",    # Links + login form
    "https://duckduckgo.com",          # Search with options
    "https://github.com",              # Complex navigation + search
]


def format_tool_summary(tools: dict, label: str) -> str:
    """Format a tool list for human-readable comparison."""
    lines = [f"[{label}]"]
    tool_list = tools.get("tools", [])
    if not tool_list:
        lines.append("(no tools generated)")
        return "\n".join(lines)

    for t in tool_list:
        params = t.get("parameters", {}).get("properties", {})
        param_strs = []
        for pname, pdef in params.items():
            ptype = pdef.get("type", "?")
            param_strs.append(f"{pname}: {ptype}")
        params_display = ", ".join(param_strs) if param_strs else ""
        desc = t.get("description", "")
        lines.append(f"- {t['name']}({params_display})")
        if desc:
            lines.append(f"{desc[:80]}")
    return "\n".join(lines)


async def compare_single_site(url: str, model: str = "gpt-4o-mini",
                               base_url: str = "https://api.openai.com/v1",
                               verbose: bool = False) -> dict:
    """Extract and analyze a single site with both methods. Returns comparison data."""
    print(f"\n{'='*70}")
    print(f"{url}")
    print(f"{'='*70}")

    # Extract
    print(f"Extracting...", end=" ", flush=True)
    t0 = time.time()
    try:
        extraction = await extract_page(url, timeout=30000)
    except Exception as e:
        print(f"Extraction failed: {e}")
        return {"url": url, "error": str(e)}
    t_extract = time.time() - t0
    print(f"{len(extraction.elements)} elements ({t_extract:.1f}s)")

    if verbose:
        for el in extraction.elements:
            print(f"[{el.kind}] {el.text[:40]} ({len(el.fields)} fields)")

    # Heuristic analysis
    print(f"Heuristic analysis...", end=" ", flush=True)
    t0 = time.time()
    heuristic_result = analyze_without_llm(extraction)
    t_heuristic = time.time() - t0
    n_heuristic = len(heuristic_result.get("tools", []))
    print(f"{n_heuristic} tools ({t_heuristic:.2f}s)")

    # LLM analysis
    print(f"LLM analysis ({model})...", end=" ", flush=True)
    t0 = time.time()
    try:
        llm_result = await analyze_with_llm(extraction, model=model, base_url=base_url)
        t_llm = time.time() - t0
        n_llm = len(llm_result.get("tools", []))
        print(f"{n_llm} tools ({t_llm:.1f}s)")
    except Exception as e:
        t_llm = time.time() - t0
        llm_result = {"tools": [], "error": str(e)}
        print(f"{e}")

    # Display comparison
    print()
    print(format_tool_summary(heuristic_result, "HEURISTIC"))
    print()
    print(format_tool_summary(llm_result, "LLM"))

    return {
        "url": url,
        "elements": len(extraction.elements),
        "heuristic": heuristic_result,
        "llm": llm_result,
        "timing": {
            "extract": t_extract,
            "heuristic": t_heuristic,
            "llm": t_llm,
        }
    }


async def compare_all(sites: list[str] = None, model: str = "gpt-4o-mini",
                      base_url: str = "https://api.openai.com/v1",
                      output: Optional[str] = None,
                      verbose: bool = False) -> list[dict]:
    """Run comparison on multiple sites."""
    sites = sites or DEFAULT_SITES

    # Check for API key
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    if not api_key and "openai.com" in base_url:
        print("No OPENAI_API_KEY or LLM_API_KEY set.", file=sys.stderr)
        print("LLM mode will fail. Set one of these env vars.", file=sys.stderr)
        print("For Ollama: --base-url http://localhost:11434/v1", file=sys.stderr)
        print()

    print(f"webmcp-gen: Heuristic vs LLM comparison")
    print(f"Model: {model}")
    print(f"Sites: {len(sites)}")
    print(f"API:   {base_url}")

    results = []
    for url in sites:
        result = await compare_single_site(url, model=model, base_url=base_url,
                                           verbose=verbose)
        results.append(result)

    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"{'Site':<30} {'Elements':>8} {'Heuristic':>10} {'LLM':>10}")
    print(f"{'-'*30} {'-'*8} {'-'*10} {'-'*10}")
    for r in results:
        if "error" in r and isinstance(r.get("error"), str):
            print(f"{r['url']:<30} {'ERROR':>8} {'-':>10} {'-':>10}")
            continue
        n_h = len(r.get("heuristic", {}).get("tools", []))
        n_l = len(r.get("llm", {}).get("tools", []))
        print(f"{r['url'][:30]:<30} {r.get('elements', 0):>8} {n_h:>10} {n_l:>10}")

    # Save full results if requested
    if output:
        with open(output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nFull results saved to {output}")

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="webmcp-gen compare",
        description="Compare heuristic vs LLM tool generation on websites"
    )
    parser.add_argument("--url", help="Single URL to test (default: 5 diverse sites)")
    parser.add_argument("--model", default="gpt-4o-mini",
                       help="LLM model (default: gpt-4o-mini)")
    parser.add_argument("--base-url", default="https://api.openai.com/v1",
                       help="LLM API base URL")
    parser.add_argument("--groq", action="store_true",
                       help="Shortcut: use Groq API with llama-3.3-70b-versatile")
    parser.add_argument("--output", "-o", help="Save full JSON results to file")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Show extracted elements")
    args = parser.parse_args()

    # Groq shortcut
    if args.groq:
        args.model = "llama-3.3-70b-versatile"
        args.base_url = "https://api.groq.com/openai/v1"

    sites = [args.url] if args.url else None
    asyncio.run(compare_all(sites=sites, model=args.model,
                           base_url=args.base_url, output=args.output,
                           verbose=args.verbose))


if __name__ == "__main__":
    main()
