"""Extract interactive elements from a web page via Playwright.

Waits for SPA hydration, then collects forms, buttons, and nav links — including
open Shadow DOM and same-origin iframes — into a PageExtraction with stable
selectors the executor can act on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FormField:
    """A single input/select/textarea in a form."""
    tag: str                        # input, select, textarea
    type: str = "text"# text, email, number, date, password, etc.
    name: str = ""# name attribute
    id: str = ""# id attribute
    label: str = ""# associated <label> text
    placeholder: str = ""# placeholder text
    required: bool = False
    options: list[str] = field(default_factory=list)  # for <select>
    aria_label: str = ""
    value: str = ""# current/default value
    selector: str = ""# stable CSS selector to target this field


@dataclass
class InteractiveElement:
    """A high-level interactive element on the page."""
    kind: str                       # "form", "button", "link", "widget"
    text: str = ""# visible text / label
    action: str = ""# form action URL, link href, etc.
    method: str = ""# GET/POST for forms
    fields: list[FormField] = field(default_factory=list)  # for forms
    selector: str = ""# stable CSS selector to target this element
    context: str = ""# surrounding text for context
    aria_label: str = ""
    element_index: int = -1         # index in the extraction for stable reference


@dataclass
class PageExtraction:
    """Everything interactive we found on a page."""
    url: str
    title: str
    description: str = ""
    elements: list[InteractiveElement] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def find_forms(self) -> list[InteractiveElement]:
        """Get all form elements."""
        return [e for e in self.elements if e.kind == "form"]

    def find_buttons(self) -> list[InteractiveElement]:
        """Get all button elements."""
        return [e for e in self.elements if e.kind == "button"]

    def find_links(self) -> list[InteractiveElement]:
        """Get all link elements."""
        return [e for e in self.elements if e.kind == "link"]


# --- JavaScript extraction scripts ---
# These are injected into the page to extract interactive elements.
# They handle Shadow DOM by recursively traversing shadowRoots.

EXTRACT_JS = """
() => {
    // Helper: build a stable selector for an element
    function stableSelector(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        if (el.getAttribute('data-testid')) return `[data-testid="${el.getAttribute('data-testid')}"]`;
        if (el.name && el.tagName === 'INPUT') return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
        if (el.name && el.tagName === 'SELECT') return `select[name="${CSS.escape(el.name)}"]`;
        if (el.name && el.tagName === 'TEXTAREA') return `textarea[name="${CSS.escape(el.name)}"]`;

        // Build a path-based selector
        const parts = [];
        let current = el;
        while (current && current !== document.body && parts.length < 5) {
            let selector = current.tagName.toLowerCase();
            if (current.id) {
                selector = '#' + CSS.escape(current.id);
                parts.unshift(selector);
                break;
            }
            const parent = current.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                if (siblings.length > 1) {
                    const idx = siblings.indexOf(current) + 1;
                    selector += `:nth-of-type(${idx})`;
                }
            }
            parts.unshift(selector);
            current = parent;
        }
        return parts.join('> ');
    }

    // Helper: get visible text, limited length
    function visibleText(el, maxLen = 60) {
        const text = (el.textContent || '').trim().replace(/\\s+/g, ' ');
        return text.substring(0, maxLen);
    }

    // Helper: get label for a form field
    function getLabel(el) {
        // Explicit label via `for` attribute
        if (el.id) {
            const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (label) return label.textContent.trim();
        }
        // Wrapping label
        const parentLabel = el.closest('label');
        if (parentLabel) {
            const labelText = parentLabel.textContent.trim().replace(el.value || '', '').trim();
            if (labelText) return labelText;
        }
        // labels property
        if (el.labels && el.labels.length > 0) {
            return el.labels[0].textContent.trim();
        }
        // aria-label
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        // aria-labelledby
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const labelEl = document.getElementById(labelledBy);
            if (labelEl) return labelEl.textContent.trim();
        }
        return '';
    }

    // Helper: get context heading near an element
    function getContext(el) {
        // Walk up looking for section/article with a heading
        let current = el.parentElement;
        let depth = 0;
        while (current && depth < 6) {
            const heading = current.querySelector('h1, h2, h3, h4');
            if (heading) {
                const text = heading.textContent.trim();
                if (text.length <= 80) return text;
            }
            current = current.parentElement;
            depth++;
        }
        return '';
    }

    // Collect all elements, including inside shadow DOM
    function collectAll(root, results) {
        // Forms
        root.querySelectorAll('form').forEach((form, fi) => {
          try {
            const fields = [];
            form.querySelectorAll('input, select, textarea').forEach((el) => {
                try {
                const elType = el.getAttribute('type') || el.type || 'text';
                const elName = el.getAttribute('name') || '';
                if (elType === 'hidden' && !elName) return;
                if (el.offsetParent === null && elType !== 'hidden') return; // not visible

                const options = [];
                if (el.tagName === 'SELECT') {
                    el.querySelectorAll('option').forEach(opt => {
                        const ov = opt.getAttribute('value');
                        if (ov && ov !== '') options.push((opt.textContent || '').trim());
                    });
                }

                fields.push({
                    tag: el.tagName.toLowerCase(),
                    type: typeof elType === 'string' ? elType : 'text',
                    name: typeof elName === 'string' ? elName : '',
                    id: el.getAttribute('id') || '',
                    label: getLabel(el),
                    placeholder: el.getAttribute('placeholder') || '',
                    required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true',
                    options: options.slice(0, 20),
                    aria_label: el.getAttribute('aria-label') || '',
                    value: elType === 'hidden' ? (el.getAttribute('value') || '') : '',
                    selector: stableSelector(el)
                });
                } catch (fieldErr) { /* skip malformed field */ }
            });

            // Get submit button
            const submit = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
            const submitText = submit
                ? ((submit.textContent || '').trim() || submit.getAttribute('value') || 'Submit')
                : 'Submit';

            results.push({
                kind: 'form',
                text: String(submitText).substring(0, 60),
                action: form.getAttribute('action') || '',
                method: (form.getAttribute('method') || 'GET').toUpperCase(),
                fields: fields,
                selector: stableSelector(form),
                context: getContext(form),
                aria_label: form.getAttribute('aria-label') || ''
            });
          } catch (formErr) { /* skip malformed form, keep extracting */ }
        });

        // Standalone buttons (not in forms)
        root.querySelectorAll('button, [role="button"]').forEach((el) => {
            if (el.closest('form')) return;
            if (el.offsetParent === null) return; // hidden
            const text = visibleText(el);
            if (!text || text.length < 2) return;

            results.push({
                kind: 'button',
                text: text,
                selector: stableSelector(el),
                aria_label: el.getAttribute('aria-label') || '',
                action: el.getAttribute('data-action') || el.getAttribute('data-href') || '',
                context: getContext(el)
            });
        });

        // Navigation links
        root.querySelectorAll('nav a, header a, [role="navigation"] a, [role="menubar"] a').forEach((el) => {
            if (el.offsetParent === null) return;
            const text = visibleText(el, 50);
            if (!text || text.length < 2) return;
            // Skip anchor-only links
            const href = el.getAttribute('href') || '';
            if (href === '#' || href === '') return;

            results.push({
                kind: 'link',
                text: text,
                action: el.href || '',
                selector: stableSelector(el),
                context: 'navigation',
                aria_label: el.getAttribute('aria-label') || ''
            });
        });

        // Traverse open shadow DOMs
        root.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) {
                collectAll(el.shadowRoot, results);
            }
        });
    }

    const results = [];
    collectAll(document, results);

    // Also check iframes (same-origin only)
    try {
        document.querySelectorAll('iframe').forEach(iframe => {
            try {
                const iframeDoc = iframe.contentDocument || iframe.contentWindow?.document;
                if (iframeDoc) collectAll(iframeDoc, results);
            } catch(e) { /* cross-origin, skip */ }
        });
    } catch(e) {}

    return results;
}
"""

WAIT_FOR_HYDRATION_JS = """
() => new Promise((resolve) => {
    // If page already seems loaded, resolve quickly
    if (document.readyState === 'complete') {
        // Wait for any pending React/Vue/Svelte hydration
        let mutations = 0;
        const observer = new MutationObserver((records) => {
            mutations += records.length;
        });
        observer.observe(document.body, { childList: true, subtree: true, attributes: true });

        // Wait up to 3 seconds for DOM to stabilize
        const check = () => {
            const prev = mutations;
            setTimeout(() => {
                if (mutations === prev) {
                    observer.disconnect();
                    resolve(true);
                } else {
                    mutations = 0;
                    setTimeout(check, 500);
                }
            }, 500);
        };
        setTimeout(check, 500);

        // Hard timeout at 5 seconds
        setTimeout(() => { observer.disconnect(); resolve(true); }, 5000);
    } else {
        window.addEventListener('load', () => resolve(true));
        setTimeout(() => resolve(true), 5000);
    }
})
"""


async def extract_page(url: str, timeout: int = 30000,
                       wait_for_hydration: bool = True,
                       stealth: bool = True,
                       headless: bool = True) -> PageExtraction:
    """Load a URL and extract all interactive elements.

    Args:
        url: The URL to load and extract from.
        timeout: Page load timeout in milliseconds.
        wait_for_hydration: If True, wait for SPA frameworks to finish rendering.
        stealth: If True, apply anti-detection evasions.
        headless: If False, show the browser window.

    Returns:
        A PageExtraction with structured data about interactive elements.
    """
    from playwright.async_api import async_playwright
    from .stealth import (
        stealth_context_options, apply_stealth,
        STEALTH_LAUNCH_ARGS, STEALTH_USER_AGENT,
    )

    async with async_playwright() as p:
        launch_args = STEALTH_LAUNCH_ARGS if stealth else []
        browser = await p.chromium.launch(headless=headless, args=launch_args)

        if stealth:
            context = await browser.new_context(**stealth_context_options())
            await apply_stealth(context)
        else:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=STEALTH_USER_AGENT,
            )
        page = await context.new_page()

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            if response and response.status >= 400:
                logger.warning(f"Page returned HTTP {response.status}")
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            raise

        # Wait for SPA hydration
        if wait_for_hydration:
            try:
                await page.evaluate(WAIT_FOR_HYDRATION_JS)
            except Exception:
                # Fallback: simple timeout
                await page.wait_for_timeout(3000)
        else:
            await page.wait_for_timeout(1500)

        # Get page metadata
        title = await page.title()
        description = await page.evaluate("""
            () => {
                const meta = document.querySelector('meta[name="description"], meta[property="og:description"]');
                return meta ? meta.content : '';
            }
        """)

        # Extract all interactive elements — retry once if a late navigation
        # destroys the execution context.
        raw_elements = []
        for attempt in range(2):
            try:
                raw_elements = await page.evaluate(EXTRACT_JS)
                break
            except Exception as e:
                msg = str(e)
                if ("context was destroyed" in msg or "navigating" in msg.lower()) and attempt == 0:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(1000)
                    continue
                logger.warning(f"extraction failed: {e}")
                break

        await context.close()
        await browser.close()

    # Build structured extraction
    elements = []
    for i, raw in enumerate(raw_elements):
        fields_raw = raw.pop("fields", [])
        fields = [FormField(**f) for f in fields_raw] if fields_raw else []

        el = InteractiveElement(
            kind=raw.get("kind", "unknown"),
            text=raw.get("text", ""),
            action=raw.get("action", ""),
            method=raw.get("method", ""),
            fields=fields,
            selector=raw.get("selector", ""),
            context=raw.get("context", ""),
            aria_label=raw.get("aria_label", ""),
            element_index=i,
        )
        elements.append(el)

    # Deduplicate: remove elements with identical text + kind
    seen = set()
    deduped = []
    for el in elements:
        key = (el.kind, el.text, el.selector)
        if key not in seen:
            seen.add(key)
            deduped.append(el)

    return PageExtraction(
        url=url,
        title=title,
        description=description,
        elements=deduped,
    )


async def extract_from_page(page) -> PageExtraction:
    """Extract from an already-open Playwright page (for the executor to re-extract)."""
    raw_elements = await page.evaluate(EXTRACT_JS)
    title = await page.title()

    elements = []
    for i, raw in enumerate(raw_elements):
        fields_raw = raw.pop("fields", [])
        fields = [FormField(**f) for f in fields_raw] if fields_raw else []
        el = InteractiveElement(
            kind=raw.get("kind", "unknown"),
            text=raw.get("text", ""),
            action=raw.get("action", ""),
            method=raw.get("method", ""),
            fields=fields,
            selector=raw.get("selector", ""),
            context=raw.get("context", ""),
            aria_label=raw.get("aria_label", ""),
            element_index=i,
        )
        elements.append(el)

    return PageExtraction(url=page.url, title=title, elements=elements)
