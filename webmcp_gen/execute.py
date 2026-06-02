"""Execute generated tools against a live page via Playwright.

Uses the analyzer's `_selector` bindings to fill fields directly. Reports
blocked/failed honestly (never success on a CAPTCHA wall) and returns structured
items rather than a raw text dump.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Any

from .extract import PageExtraction, InteractiveElement, FormField, extract_from_page

logger = logging.getLogger(__name__)


# Signals that an action hit a bot-wall or error rather than a real result.
_BLOCK_SIGNALS = (
    "unusual traffic",
    "are you a robot",
    "verify you are human",
    "captcha",
    "access denied",
    "request blocked",
    "enable javascript and cookies",
    "checking your browser",
    "to continue, please",
    "automated queries",
)

# URL fragments that indicate a block / error / challenge page.
_BLOCK_URL_FRAGMENTS = (
    "/sorry/",          # Google
    "418.html",         # DuckDuckGo teapot bot-wall
    "/errors/",
    "/challenge",
    "captcha",
    "/blocked",
    "access-denied",
)


# JS to extract structured results from a page. Defined once at module level
# so it's not re-parsed on every call.
_RESULT_EXTRACT_JS = r"""
() => {
    function clean(s) { return (s || '').trim().replace(/\s+/g, ' '); }

    const containers = [
        'main', '[role="main"]', '#content', '#main-content',
        '.results', '.search-results', '#search', 'article'
    ];
    let root = null;
    for (const sel of containers) {
        const el = document.querySelector(sel);
        if (el && el.innerText.trim().length > 50) { root = el; break; }
    }
    if (!root) root = document.body;

    const items = [];
    const seen = new Set();
    root.querySelectorAll('a[href]').forEach(a => {
        const title = clean(a.innerText);
        const href = a.href;
        if (!title || title.length < 8 || title.length > 200) return;
        if (!href || href.startsWith('javascript:')) return;
        if (seen.has(href)) return;
        seen.add(href);
        let snippet = '';
        const container = a.closest('li, article, div, tr');
        if (container) {
            const p = container.querySelector('p, .snippet, .description, td');
            if (p) snippet = clean(p.innerText).substring(0, 300);
        }
        items.push({ title, url: href, snippet });
    });

    const clone = root.cloneNode(true);
    clone.querySelectorAll('nav, header, footer, script, style, [role="navigation"]')
        .forEach(el => el.remove());
    const text = clone.innerText.substring(0, 8000);

    return { items: items.slice(0, 25), text };
}
"""


@dataclass
class ResultItem:
    """A single structured item extracted from a result page."""
    title: str = ""
    url: str = ""
    snippet: str = ""


@dataclass
class ToolResult:
    """The result of executing a tool on a website."""
    success: bool
    items: list[ResultItem] = field(default_factory=list)  # structured results
    text: str = ""                  # cleaned text content (fallback)
    error: str = ""
    url: str = ""                   # URL after execution
    page_title: str = ""
    blocked: bool = False           # True if a CAPTCHA / bot-wall was detected

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "blocked": self.blocked,
            "url": self.url,
            "page_title": self.page_title,
            "items": [
                {"title": i.title, "url": i.url, "snippet": i.snippet}
                for i in self.items
            ],
            "text": self.text if not self.items else "",
            "error": self.error,
        }


@dataclass
class ToolDefinition:
    """A tool definition with execution bindings."""
    name: str
    description: str
    parameters: dict
    submit_selector: str = ""
    element_index: int = -1
    link_bindings: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ToolDefinition":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            parameters=d.get("parameters", {}),
            submit_selector=d.get("_submit_selector", ""),
            element_index=d.get("_element_index", -1),
            link_bindings=d.get("_link_bindings", {}),
        )


class WebExecutor:
    """Maintains a browser session and executes tool calls on a website.

    Usage:
        async with WebExecutor("https://google.com", tools=tools["tools"]) as ex:
            result = await ex.call("searchGoogle", {"query": "hello"})
            print(result.to_dict())
    """

    def __init__(self, url: str, tools: list[dict] = None,
                 extraction: PageExtraction = None,
                 headless: bool = True,
                 storage_state: Optional[dict] = None,
                 stealth: bool = True):
        self.start_url = url
        self.headless = headless
        self.storage_state = storage_state
        self.stealth = stealth
        self._tools = [ToolDefinition.from_dict(t) for t in (tools or [])]
        self._extraction = extraction
        self._pw_context = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def tools(self) -> list[ToolDefinition]:
        return self._tools

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        from .stealth import (
            stealth_context_options, apply_stealth,
            STEALTH_LAUNCH_ARGS, STEALTH_USER_AGENT,
        )
        self._pw_context = async_playwright()
        self._playwright = await self._pw_context.__aenter__()
        launch_args = STEALTH_LAUNCH_ARGS if self.stealth else []
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless, args=launch_args,
        )

        if self.stealth:
            ctx_kwargs = stealth_context_options()
        else:
            ctx_kwargs = {
                "viewport": {"width": 1280, "height": 720},
                "user_agent": STEALTH_USER_AGENT,
            }
        if self.storage_state:
            ctx_kwargs["storage_state"] = self.storage_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        if self.stealth:
            await apply_stealth(self._context)

        self._page = await self._context.new_page()
        await self._page.goto(self.start_url, wait_until="domcontentloaded")
        await self._wait_stable()
        return self

    async def __aexit__(self, *args):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw_context:
            await self._pw_context.__aexit__(*args)

    async def save_session(self) -> dict:
        """Export cookies/localStorage so a future executor can resume auth."""
        if self._context:
            return await self._context.storage_state()
        return {}

    async def call(self, tool_name: str, arguments: dict) -> ToolResult:
        """Execute a tool by name with the given arguments."""
        page = self._page
        if not page:
            return ToolResult(success=False, error="No browser session")

        tool = self._find_tool(tool_name)
        if not tool:
            return ToolResult(
                success=False,
                error=(f"Unknown tool '{tool_name}'. "
                       f"Available: {[t.name for t in self._tools]}")
            )

        url_before = page.url
        try:
            if self._is_navigate_tool(tool):
                result = await self._execute_navigate(tool, arguments)
            elif tool.parameters.get("properties"):
                result = await self._execute_form(tool, arguments)
            else:
                result = await self._execute_click(tool)
        except Exception as e:
            logger.error(f"Tool execution failed: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e), url=page.url)

        result = await self._validate_result(result, url_before)
        return result

    @staticmethod
    def _is_navigate_tool(tool: ToolDefinition) -> bool:
        """Navigation if it has link bindings, or is named 'navigate' with a
        'page' parameter (the shape the LLM produces)."""
        if tool.link_bindings:
            return True
        props = tool.parameters.get("properties", {})
        if tool.name == "navigate" and "page" in props:
            return True
        return False

    async def call_tool(self, tool_name: str, arguments: dict,
                        extraction: PageExtraction = None) -> ToolResult:
        if not self._tools and extraction:
            self._extraction = extraction
            from .analyze import analyze_without_llm
            result = analyze_without_llm(extraction)
            self._tools = [ToolDefinition.from_dict(t) for t in result.get("tools", [])]
        return await self.call(tool_name, arguments)

    async def re_extract(self) -> PageExtraction:
        """Re-extract the current page state (for multi-step flows)."""
        if self._page:
            self._extraction = await extract_from_page(self._page)
            return self._extraction
        return PageExtraction(url="", title="", elements=[])

    # --- Private execution methods ---

    async def _execute_form(self, tool: ToolDefinition, arguments: dict) -> ToolResult:
        """Fill form fields using selector bindings and submit."""
        page = self._page
        properties = tool.parameters.get("properties", {})

        filled_count = 0
        errors = []
        for param_name, param_def in properties.items():
            if param_name not in arguments:
                continue

            value = arguments[param_name]
            selector = param_def.get("_selector", "") or self._fallback_selector(param_name, tool)

            if not selector:
                errors.append(f"no selector for '{param_name}'")
                continue

            try:
                await self._fill_field(selector, value, param_def.get("type", "string"))
                filled_count += 1
            except Exception as e:
                errors.append(f"'{param_name}': {e}")
                logger.warning(f"Failed to fill '{param_name}' ({selector}): {e}")

        # Last resort: one arg, one obvious text input
        if filled_count == 0 and len(arguments) == 1:
            try:
                value = next(iter(arguments.values()))
                scope = tool.submit_selector or "body"
                input_el = await page.query_selector(
                    f"{scope} input[type='text'], {scope} input[type='search'], "
                    f"{scope} input:not([type]), {scope} input[type='email']"
                )
                if input_el:
                    await input_el.scroll_into_view_if_needed()
                    await input_el.fill(str(value))
                    filled_count = 1
            except Exception as e:
                errors.append(f"fallback fill: {e}")

        if filled_count == 0:
            return ToolResult(
                success=False,
                error=f"Could not fill any fields. {'; '.join(errors)}",
                url=page.url,
            )

        await self._submit(tool.submit_selector)
        await self._wait_stable()
        return await self._extract_result()

    async def _execute_click(self, tool: ToolDefinition) -> ToolResult:
        """Click a button element."""
        page = self._page
        selector = tool.submit_selector
        if not selector:
            return ToolResult(success=False, error="No selector for button")

        clicked = await self._resilient_click(selector)
        if not clicked:
            # Fallback: click by accessible name parsed from description
            name = tool.description.replace("Click the '", "").rstrip("' button").strip()
            try:
                await page.get_by_role("button", name=name).first.click(timeout=4000)
                clicked = True
            except Exception as e:
                return ToolResult(success=False, error=f"Cannot click button: {e}", url=page.url)

        await self._wait_stable()
        return await self._extract_result()

    async def _execute_navigate(self, tool: ToolDefinition, arguments: dict) -> ToolResult:
        """Navigate using link bindings, extraction lookup, or link-text match."""
        page = self._page
        target = arguments.get("page", "")
        if not target:
            return ToolResult(success=False, error="'page' parameter required for navigate")

        # 1. Explicit binding from the heuristic analyzer
        selector = tool.link_bindings.get(target, "")

        # 2. Look up the link in the extraction by matching text
        if not selector and self._extraction:
            for el in self._extraction.elements:
                if el.kind == "link" and el.text == target and el.selector:
                    selector = el.selector
                    break

        if selector and await self._resilient_click(selector):
            await self._wait_navigation()
            return await self._extract_result()

        # 3. Click by accessible link name (exact, then partial)
        for exact in (True, False):
            try:
                await page.get_by_role("link", name=target, exact=exact).first.click(timeout=4000)
                await self._wait_navigation()
                return await self._extract_result()
            except Exception:
                continue

        # 4. Direct navigation if we know the href
        if self._extraction:
            for el in self._extraction.elements:
                if el.kind == "link" and el.text == target and el.action:
                    try:
                        await page.goto(el.action, wait_until="domcontentloaded")
                        await self._wait_stable()
                        return await self._extract_result()
                    except Exception:
                        break

        return ToolResult(success=False, error=f"Cannot navigate to '{target}'", url=page.url)

    async def _fill_field(self, selector: str, value: Any, field_type: str):
        """Fill a single field by selector, with scroll + visibility handling."""
        page = self._page
        el = await page.query_selector(selector)
        if not el:
            raise ValueError(f"element not found: {selector}")

        await el.scroll_into_view_if_needed(timeout=3000)

        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        input_type = (await el.evaluate("el => el.type || ''")) or ""

        if tag == "select":
            try:
                await page.select_option(selector, label=str(value), timeout=4000)
            except Exception:
                try:
                    await page.select_option(selector, value=str(value), timeout=4000)
                except Exception:
                    # Last resort: select by partial label match against options
                    await page.select_option(selector, index=1, timeout=4000)
        elif input_type == "checkbox":
            if value:
                await el.check()
            else:
                await el.uncheck()
        elif input_type == "radio":
            await page.check(f"{selector}[value='{value}']")
        elif input_type == "file":
            await el.set_input_files(str(value))
        else:
            await el.click()
            try:
                await el.fill("")
            except Exception:
                pass
            await el.type(str(value), delay=10)

    async def _resilient_click(self, selector: str, attempts: int = 2) -> bool:
        """Click with scroll-into-view and a retry. Returns True on success."""
        page = self._page
        for attempt in range(attempts):
            try:
                el = await page.query_selector(selector)
                if not el:
                    return False
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(timeout=4000)
                return True
            except Exception as e:
                logger.debug(f"click attempt {attempt+1} failed: {e}")
                await page.wait_for_timeout(500)
        return False

    async def _submit(self, form_selector: str):
        """Submit a form — try submit button, then Enter key."""
        page = self._page
        candidates = [
            f"{form_selector} button[type='submit']",
            f"{form_selector} input[type='submit']",
            f"{form_selector} button:not([type])",
        ] if form_selector else []

        for sel in candidates:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.scroll_into_view_if_needed(timeout=2000)
                    await btn.click(timeout=4000)
                    return
            except Exception:
                continue
        # Fallback: Enter key (works for most search inputs)
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass

    async def _wait_stable(self, timeout_ms: int = 8000):
        """Wait for the page to stabilize after an action."""
        page = self._page
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            await page.wait_for_timeout(1500)

    async def _wait_navigation(self, timeout_ms: int = 10000):
        page = self._page
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        await self._wait_stable()

    async def _validate_result(self, result: ToolResult, url_before: str) -> ToolResult:
        """Detect silent failures: bot-walls, error pages, or no state change."""
        if not result.success:
            return result

        # Check URL for block/challenge fragments
        url_lower = (result.url or "").lower()
        for frag in _BLOCK_URL_FRAGMENTS:
            if frag in url_lower:
                result.success = False
                result.blocked = True
                result.error = (
                    f"Blocked by anti-bot protection (redirected to '{frag}'). "
                    "The site detected automation. Try --headful or an authenticated session."
                )
                return result

        # Check page text/title for block signals
        haystack = (result.text or "").lower() + " " + (result.page_title or "").lower()
        for sig in _BLOCK_SIGNALS:
            if sig in haystack:
                result.success = False
                result.blocked = True
                result.error = (
                    f"Blocked by anti-bot protection (matched '{sig}'). "
                    "The site detected automation. Try --headful or an authenticated session."
                )
                return result
        return result

    async def _extract_result(self) -> ToolResult:
        """Extract structured + text content from the current page.

        Resilient to mid-extraction navigation (re-tries once if the execution
        context is destroyed by an in-flight navigation).
        """
        page = self._page

        for attempt in range(2):
            try:
                # Make sure any in-flight navigation settles first
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass

                data = await page.evaluate(_RESULT_EXTRACT_JS)
                items = [
                    ResultItem(title=i["title"], url=i["url"], snippet=i.get("snippet", ""))
                    for i in data.get("items", [])
                ]
                return ToolResult(
                    success=True,
                    items=items,
                    text=data.get("text", "").strip(),
                    url=page.url,
                    page_title=await page.title(),
                )
            except Exception as e:
                msg = str(e)
                if "context was destroyed" in msg or "navigating" in msg.lower():
                    # A navigation happened mid-extraction — wait and retry once
                    await page.wait_for_timeout(1500)
                    continue
                raise
        # If both attempts hit navigation, return what we can
        return ToolResult(success=True, items=[], text="", url=page.url,
                          page_title=await page.title())

    def _find_tool(self, name: str) -> Optional[ToolDefinition]:
        """Find a tool by exact name, then case-insensitive, then semantic."""
        for t in self._tools:
            if t.name == name:
                return t
        name_lower = name.lower()
        for t in self._tools:
            if t.name.lower() == name_lower:
                return t
        search_words = {"search", "find", "query", "lookup"}
        if any(w in name_lower for w in search_words):
            for t in self._tools:
                if t.parameters.get("properties"):
                    return t
        return None

    def _fallback_selector(self, param_name: str, tool: ToolDefinition) -> str:
        """Find a selector for a parameter using the extraction data."""
        if not self._extraction:
            return ""
        idx = tool.element_index
        if 0 <= idx < len(self._extraction.elements):
            el = self._extraction.elements[idx]
            for f in el.fields:
                if (f.name.lower() == param_name.lower() or
                        f.id.lower() == param_name.lower() or
                        (f.label and param_name.lower() in f.label.lower())):
                    return f.selector
        return ""
