"""
cli.py — Command-line interface for webmcp-gen.

Usage:
    webmcp-gen https://example.com                       # heuristic mode
    webmcp-gen https://example.com --groq                # LLM via Groq
    webmcp-gen https://example.com --llm --model gpt-4o  # LLM via OpenAI
    webmcp-gen https://example.com -o tools.json         # save to file
    webmcp-gen https://example.com --raw                 # raw extraction data
    webmcp-gen https://example.com --execute             # extract + analyze + run a tool
"""

import argparse
import asyncio
import json
import logging
import sys

from .extract import extract_page
from .analyze import analyze_with_llm, analyze_without_llm
from . import cache as _cache
from .validate import validate_tools, format_report


def main():
    parser = argparse.ArgumentParser(
        prog="webmcp-gen",
        description="Auto-generate WebMCP tool definitions from any website",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  webmcp-gen https://google.com                    # Quick heuristic analysis
  webmcp-gen https://booking.com --groq            # LLM analysis via Groq
  webmcp-gen https://github.com --llm -o gh.json   # Save LLM output to file
  webmcp-gen https://hn.algolia.com --raw          # See raw extraction data
        """,
    )
    parser.add_argument("url", help="URL of the website to analyze", nargs="?")

    # LLM options
    llm_group = parser.add_argument_group("LLM options")
    llm_group.add_argument("--llm", action="store_true",
                           help="Use LLM for semantic analysis")
    llm_group.add_argument("--groq", action="store_true",
                           help="Shortcut: use Groq API (llama-3.3-70b-versatile)")
    llm_group.add_argument("--model", default="gpt-4o-mini",
                           help="LLM model (default: gpt-4o-mini)")
    llm_group.add_argument("--base-url", default="https://api.openai.com/v1",
                           help="LLM API base URL")

    # Output options
    out_group = parser.add_argument_group("output options")
    out_group.add_argument("--output", "-o", help="Output file (default: stdout)")
    out_group.add_argument("--raw", action="store_true",
                           help="Output raw extraction (before analysis)")
    out_group.add_argument("--compact", action="store_true",
                           help="Compact JSON output (no indentation)")
    out_group.add_argument("--validate", action="store_true",
                           help="Validate generated tools against the WebMCP schema")

    # Behavior options
    parser.add_argument("--crawl", action="store_true",
                       help="Crawl multiple pages and merge tools across the site")
    parser.add_argument("--max-pages", type=int, default=5,
                       help="Max pages to crawl (with --crawl, default: 5)")
    parser.add_argument("--max-depth", type=int, default=2,
                       help="Max crawl depth (with --crawl, default: 2)")
    parser.add_argument("--same-path", action="store_true",
                       help="Only crawl links sharing the start path prefix")
    parser.add_argument("--timeout", type=int, default=30000,
                       help="Page load timeout in ms (default: 30000)")
    parser.add_argument("--no-hydration", action="store_true",
                       help="Skip SPA hydration wait")
    parser.add_argument("--no-stealth", action="store_true",
                       help="Disable anti-detection evasions")
    parser.add_argument("--headful", action="store_true",
                       help="Show the browser window")
    parser.add_argument("--no-cache", action="store_true",
                       help="Bypass the disk cache")
    parser.add_argument("--clear-cache", action="store_true",
                       help="Clear the cache and exit")
    parser.add_argument("--cache-ttl", type=int, default=3600,
                       help="Cache freshness in seconds (default: 3600)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose output")

    args = parser.parse_args()

    if args.clear_cache:
        n = _cache.clear()
        print(f"Cleared {n} cache entries", file=sys.stderr)
        return

    if not args.url:
        parser.error("the following arguments are required: url")

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # Groq shortcut
    if args.groq:
        args.llm = True
        args.model = "llama-3.3-70b-versatile"
        args.base_url = "https://api.groq.com/openai/v1"

    # Run the pipeline
    try:
        result = asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    # Output
    indent = None if args.compact else 2
    output = json.dumps(result, indent=indent)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(output)

    # Summary to stderr
    if "tools" in result:
        tools = result["tools"]
        print(f"\nGenerated {len(tools)} tools:", file=sys.stderr)
        for t in tools:
            params = list(t.get("parameters", {}).get("properties", {}).keys())
            # Filter internal keys from display
            params = [p for p in params if not p.startswith("_")]
            print(f"- {t['name']}({', '.join(params)})", file=sys.stderr)

    # Optional spec-compliance validation
    if args.validate and "tools" in result:
        issues = validate_tools(result)
        print(f"\nWebMCP validation:", file=sys.stderr)
        print(format_report(issues), file=sys.stderr)
        if any(i.severity == "error" for i in issues):
            sys.exit(2)


async def _run(args) -> dict:
    """Run the extraction and analysis pipeline (with caching + optional crawl)."""
    mode = "raw" if args.raw else ("llm" if args.llm else "heuristic")
    if args.crawl:
        mode = f"crawl-{mode}"
    model = args.model if args.llm else ""

    # Try cache
    if not args.no_cache:
        cached = _cache.get(args.url, mode, model, ttl_seconds=args.cache_ttl)
        if cached is not None:
            print(f"Using cached result (--no-cache to bypass)", file=sys.stderr)
            return cached

    # Multi-page crawl path
    if args.crawl:
        from .crawl import crawl_site
        print(f"Crawling {args.url} (max {args.max_pages} pages, depth {args.max_depth})...",
              file=sys.stderr)
        crawl = await crawl_site(
            args.url,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            same_path_prefix=args.same_path,
            llm=args.llm,
            model=args.model,
            base_url=args.base_url,
            stealth=not args.no_stealth,
            timeout=args.timeout,
        )
        print(f"Visited {len(crawl.pages_visited)} pages, "
              f"merged {len(crawl.tools)} tools", file=sys.stderr)
        result = crawl.to_analysis()
        if not args.no_cache:
            _cache.put(args.url, mode, result, model)
        return result

    print(f"Extracting: {args.url}", file=sys.stderr)
    extraction = await extract_page(
        args.url,
        timeout=args.timeout,
        wait_for_hydration=not args.no_hydration,
        stealth=not args.no_stealth,
        headless=not args.headful,
    )
    print(f"Found {len(extraction.elements)} interactive elements", file=sys.stderr)

    if args.raw:
        result = extraction.to_dict()
    elif args.llm:
        print(f"Analyzing with {args.model}...", file=sys.stderr)
        result = await analyze_with_llm(
            extraction, model=args.model, base_url=args.base_url
        )
    else:
        print(f"Analyzing with heuristics (use --llm or --groq for better results)",
              file=sys.stderr)
        result = analyze_without_llm(extraction)

    # Store in cache
    if not args.no_cache:
        _cache.put(args.url, mode, result, model)

    return result


if __name__ == "__main__":
    main()
