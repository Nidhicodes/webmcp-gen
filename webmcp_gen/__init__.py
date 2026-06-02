"""webmcp-gen: Auto-generate WebMCP tools from any website."""

__version__ = "0.6.1"

from .extract import (
    extract_page,
    extract_from_page,
    PageExtraction,
    InteractiveElement,
    FormField,
)
from .analyze import analyze_with_llm, analyze_without_llm
from .execute import WebExecutor, ToolResult, ToolDefinition, ResultItem
from .validate import validate_tools, is_compliant
from .crawl import crawl_site, CrawlResult
from .workflow import Workflow, WorkflowResult, run_workflow
from . import cache
from . import session

__all__ = [
    "extract_page",
    "extract_from_page",
    "PageExtraction",
    "InteractiveElement",
    "FormField",
    "analyze_with_llm",
    "analyze_without_llm",
    "WebExecutor",
    "ToolResult",
    "ToolDefinition",
    "ResultItem",
    "validate_tools",
    "is_compliant",
    "crawl_site",
    "CrawlResult",
    "Workflow",
    "WorkflowResult",
    "run_workflow",
    "cache",
    "session",
]
