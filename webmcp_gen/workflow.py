"""Declarative multi-step tool chains.

A workflow is an ordered list of steps run against a live executor. Step args
support references to prior results and inputs:
    {{ steps.search.items.0.url }}    a value from an earlier step
    {{ vars.destination }}            a workflow input variable
The page is re-extracted between steps, so tools that only appear on a later
page become callable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .execute import WebExecutor, ToolResult

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


@dataclass
class StepResult:
    """Outcome of one workflow step."""
    tool: str
    args: dict
    success: bool
    blocked: bool = False
    error: str = ""
    url: str = ""
    items: list = field(default_factory=list)
    text: str = ""

    @classmethod
    def from_tool_result(cls, tool: str, args: dict, r: ToolResult) -> "StepResult":
        return cls(
            tool=tool, args=args, success=r.success, blocked=r.blocked,
            error=r.error, url=r.url,
            items=[{"title": i.title, "url": i.url, "snippet": i.snippet} for i in r.items],
            text=r.text,
        )

    def as_context(self) -> dict:
        return {
            "success": self.success, "blocked": self.blocked, "url": self.url,
            "items": self.items, "text": self.text,
        }


@dataclass
class WorkflowResult:
    """Outcome of a whole workflow run."""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    error: str = ""
    failed_at: Optional[int] = None  # index of the step that failed

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "error": self.error,
            "failed_at": self.failed_at,
            "steps": [
                {
                    "tool": s.tool, "args": s.args, "success": s.success,
                    "blocked": s.blocked, "url": s.url, "error": s.error,
                    "items": s.items[:10], "text": s.text[:500] if not s.items else "",
                }
                for s in self.steps
            ],
        }


def _resolve_path(path: str, context: dict) -> Any:
    """Resolve a dotted path like 'steps.search.items.0.url' against context."""
    cur: Any = context
    for part in path.split("."):
        part = part.strip()
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(f"'{part}' not found while resolving '{path}'")
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                raise KeyError(f"'{part}' is not a list index in '{path}'")
            if idx >= len(cur):
                raise IndexError(f"index {idx} out of range in '{path}' (len {len(cur)})")
            cur = cur[idx]
        else:
            raise KeyError(f"cannot descend into '{part}' of '{path}'")
    return cur


def _interpolate(value: Any, context: dict) -> Any:
    """Replace {{ ... }} references in a value (recursively for dicts/lists)."""
    if isinstance(value, str):
        # Whole-string reference  preserve the resolved type
        m = _REF_RE.fullmatch(value.strip())
        if m:
            return _resolve_path(m.group(1), context)
        # Inline references inside a larger string  stringify
        def repl(match):
            return str(_resolve_path(match.group(1), context))
        return _REF_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, context) for v in value]
    return value


@dataclass
class Workflow:
    """An ordered list of tool-call steps with result threading."""
    steps: list[dict]
    stop_on_error: bool = True

    def validate(self) -> list[str]:
        """Static validation of the workflow shape. Returns a list of problems."""
        problems = []
        for i, step in enumerate(self.steps):
            if "tool" not in step:
                problems.append(f"step[{i}] missing 'tool'")
            if "args" in step and not isinstance(step["args"], dict):
                problems.append(f"step[{i}] 'args' must be an object")
        return problems

    async def run(self, executor: WebExecutor,
                  variables: Optional[dict] = None) -> WorkflowResult:
        """Execute the workflow against a live executor."""
        problems = self.validate()
        if problems:
            return WorkflowResult(success=False, error="; ".join(problems))

        context: dict = {"vars": variables or {}, "steps": {}}
        result = WorkflowResult(success=True)

        for i, step in enumerate(self.steps):
            tool = step["tool"]
            raw_args = step.get("args", {})

            # Resolve references from prior steps / vars
            try:
                args = _interpolate(raw_args, context)
            except (KeyError, IndexError) as e:
                sr = StepResult(tool=tool, args=raw_args, success=False,
                                error=f"reference error: {e}")
                result.steps.append(sr)
                result.success = False
                result.failed_at = i
                result.error = f"step[{i}] ({tool}): {e}"
                return result

            # Run the tool
            logger.info(f"workflow step[{i}]: {tool}({args})")
            tr = await executor.call(tool, args)
            sr = StepResult.from_tool_result(tool, args, tr)
            result.steps.append(sr)

            # Save into context (by save_as name, and always by step index)
            save_as = step.get("save_as")
            if save_as:
                context["steps"][save_as] = sr.as_context()
            context["steps"][str(i)] = sr.as_context()

            if not tr.success and self.stop_on_error:
                result.success = False
                result.failed_at = i
                result.error = (f"step[{i}] ({tool}) "
                                f"{'blocked' if tr.blocked else 'failed'}: {tr.error}")
                # Re-extract so the next executor reuse sees current state
                return result

            # Re-extract the page so newly-revealed tools become callable
            try:
                new_extraction = await executor.re_extract()
                # Refresh the executor's tool set from the new page
                from .analyze import analyze_without_llm
                new_tools = analyze_without_llm(new_extraction)
                _merge_tools(executor, new_tools.get("tools", []))
            except Exception as e:
                logger.debug(f"re-extract after step {i} failed: {e}")

        return result


def _merge_tools(executor: WebExecutor, new_tool_dicts: list[dict]) -> None:
    """Add newly-discovered tools to the executor without dropping existing ones."""
    from .execute import ToolDefinition
    existing = {t.name for t in executor._tools}
    for td in new_tool_dicts:
        if td["name"] not in existing:
            executor._tools.append(ToolDefinition.from_dict(td))
            existing.add(td["name"])


async def run_workflow(url: str, steps: list[dict], tools: list[dict],
                       variables: Optional[dict] = None,
                       extraction=None, stealth: bool = True,
                       storage_state: Optional[dict] = None) -> WorkflowResult:
    """Convenience: open an executor, run the workflow, return the result."""
    wf = Workflow(steps=steps)
    async with WebExecutor(url, tools=tools, extraction=extraction,
                           stealth=stealth, storage_state=storage_state) as ex:
        return await wf.run(ex, variables=variables)


def main():
    """CLI: run a declarative workflow from a JSON file against a site.

    Workflow file format:
        {
          "url": "https://example.com",
          "variables": { "query": "rust" },
          "steps": [
            { "tool": "search", "args": { "q": "{{ vars.query }}" }, "save_as": "search" },
            { "tool": "navigate", "args": { "page": "{{ steps.search.items.0.title }}" } }
          ]
        }

    Example:
        webmcp-workflow flow.json --groq
    """
    import argparse
    import asyncio
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="webmcp-workflow",
        description="Run a declarative multi-step tool chain against a website",
    )
    parser.add_argument("workflow", help="Path to the workflow JSON file")
    parser.add_argument("--url", help="Override the URL in the workflow file")
    parser.add_argument("--llm", action="store_true", help="Use LLM analysis")
    parser.add_argument("--groq", action="store_true")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--session", metavar="NAME", help="Use a saved session")
    parser.add_argument("--no-stealth", action="store_true")
    parser.add_argument("--output", "-o", help="Write result JSON to file")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.groq:
        args.llm = True
        args.model = "llama-3.3-70b-versatile"
        args.base_url = "https://api.groq.com/openai/v1"

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    with open(args.workflow) as f:
        spec = json.load(f)

    url = args.url or spec.get("url")
    if not url:
        parser.error("no URL (set 'url' in the file or pass --url)")
    steps = spec.get("steps", [])
    variables = spec.get("variables", {})

    result = asyncio.run(_run_cli(
        url, steps, variables, args,
    ))

    out = json.dumps(result.to_dict(), indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out + "\n")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(out)

    # Summary
    status = " success" if result.success else f" failed at step {result.failed_at}"
    print(f"\n{status}: {len(result.steps)} steps run", file=sys.stderr)
    if not result.success:
        sys.exit(1)


async def _run_cli(url, steps, variables, args):
    import sys
    from .extract import extract_page
    from .analyze import analyze_without_llm, analyze_with_llm
    from .session import load_session

    print(f"Extracting: {url}", file=sys.stderr)
    extraction = await extract_page(url, stealth=not args.no_stealth)
    if args.llm:
        print(f"Analyzing with {args.model}...", file=sys.stderr)
        analysis = await analyze_with_llm(extraction, model=args.model, base_url=args.base_url)
    else:
        analysis = analyze_without_llm(extraction)

    storage_state = load_session(args.session) if args.session else None

    print(f"Running {len(steps)} workflow steps...", file=sys.stderr)
    return await run_workflow(
        url, steps, analysis.get("tools", []), variables=variables,
        extraction=extraction, stealth=not args.no_stealth,
        storage_state=storage_state,
    )


if __name__ == "__main__":
    main()
