"""Generate WebMCP tool definitions from extracted DOM elements.

Heuristic mode needs no API key; LLM mode produces better names and descriptions.
Both emit a `_selector` per parameter so the executor fills fields directly
instead of guessing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

import httpx

from .extract import PageExtraction, InteractiveElement, FormField

logger = logging.getLogger(__name__)


# --- LLM system prompt ---

SYSTEM_PROMPT = """You are an expert at analyzing website interfaces and generating structured tool definitions for AI agents.

Given interactive elements extracted from a web page (forms, buttons, links), generate WebMCP-compliant tool definitions.

Rules:
1. Each form becomes one tool. Name it as a camelCase verb phrase (searchFlights, submitTicket, loginUser).
2. Form fields become parameters. Use label/placeholder to infer meaningful parameter names.
3. OMIT submit buttons as parameters — they are NOT input fields.
4. OMIT hidden fields as parameters — they are internal to the form.
5. Use field type to set JSON schema type: textstring, numbernumber, checkboxboolean, datestring(format:date).
6. Mark truly required fields. Default to optional if unsure.
7. Standalone action buttons (not marketing/promo) become zero-parameter tools.
8. SKIP promotional buttons, FAQ expanders, and browser download buttons — they are not agent-useful tools.
9. Group navigation links into one "navigate" tool with a "page" enum parameter.
10. Descriptions should say what the tool DOES for an agent, not describe the UI element.
11. Parameter descriptions should say what to pass, with examples if the field type isn't obvious.
12. For each parameter, include a `_selector` field with the CSS selector of the DOM element to fill.

Output ONLY this JSON structure (no markdown, no commentary):
{
  "tools": [
    {
      "name": "camelCaseToolName",
      "description": "What this tool does in one sentence",
      "parameters": {
        "type": "object",
        "properties": {
          "paramName": {
            "type": "string",
            "description": "What to pass here",
            "_selector": "CSS selector for the input element"
          }
        },
        "required": ["paramName"]
      },
      "_submit_selector": "CSS selector for the submit button or form",
      "_element_index": 0
    }
  ],
  "site": {
    "name": "Human-readable site name",
    "description": "What this website is for, in one sentence"
  }
}"""


def build_user_prompt(extraction: PageExtraction) -> str:
    """Build the user prompt from the page extraction."""
    parts = [
        f"Website: {extraction.title}",
        f"URL: {extraction.url}",
    ]
    if extraction.description:
        parts.append(f"Description: {extraction.description}")
    parts.append("\n--- Interactive Elements ---\n")

    for i, el in enumerate(extraction.elements):
        parts.append(f"[{i}] {el.kind.upper()}: \"{el.text}\"")
        parts.append(f"selector: {el.selector}")
        if el.context:
            parts.append(f"context: {el.context}")
        if el.aria_label:
            parts.append(f"aria-label: {el.aria_label}")
        if el.action:
            parts.append(f"action: {el.action} ({el.method})")
        if el.fields:
            parts.append(f"fields:")
            for f in el.fields:
                desc = f.label or f.placeholder or f.aria_label or f.name
                req = " [REQUIRED]" if f.required else ""
                ftype = f"[{f.tag}/{f.type}]"
                opts = f" options={f.options}" if f.options else ""
                parts.append(
                    f"- {desc} {ftype} name=\"{f.name}\" "
                    f"selector=\"{f.selector}\"{req}{opts}"
                )
        parts.append("")

    return "\n".join(parts)


async def analyze_with_llm(
    extraction: PageExtraction,
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
    base_url: str = "https://api.openai.com/v1",
    max_retries: int = 2,
    timeout: float = 90.0,
) -> dict:
    """Send the extraction to an LLM and get back clean tool definitions.

    Uses httpx for robust HTTP with proper timeouts, retries, and streaming support.

    Works with any OpenAI-compatible API:
      - OpenAI: base_url="https://api.openai.com/v1"
      - Groq: base_url="https://api.groq.com/openai/v1"
      - Ollama: base_url="http://localhost:11434/v1"
      - Together: base_url="https://api.together.xyz/v1"
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY", "")
    if not api_key and "openai.com" in base_url:
        raise ValueError(
            "Set OPENAI_API_KEY or LLM_API_KEY environment variable, "
            "or use --base-url for a local model (e.g., http://localhost:11434/v1)"
        )

    user_prompt = build_user_prompt(extraction)

    # Truncate if too long (avoid token limits on smaller models)
    max_chars = 15000
    if len(user_prompt) > max_chars:
        user_prompt = user_prompt[:max_chars] + "\n\n[...truncated — showing first elements only]"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
    }
    # response_format for models that support structured JSON output
    if any(x in model for x in ("gpt", "gemini")):
        body["response_format"] = {"type": "json_object"}

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "webmcp-gen/0.2.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = f"{base_url.rstrip('/')}/chat/completions"

    last_error: Optional[str] = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries + 1):
            try:
                resp = await client.post(url, json=body, headers=headers)

                if resp.status_code == 429:
                    # Rate limited — exponential backoff
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt+1})")
                    await asyncio.sleep(wait)
                    last_error = f"Rate limited (429)"
                    continue
                elif resp.status_code >= 500:
                    # Server error — retry
                    logger.warning(f"Server error {resp.status_code}, retrying")
                    await asyncio.sleep(1)
                    last_error = f"Server error ({resp.status_code})"
                    continue
                elif resp.status_code != 200:
                    error_text = resp.text[:300]
                    raise RuntimeError(
                        f"LLM API error (HTTP {resp.status_code}): {error_text}. "
                        "Check your API key and base URL."
                    )

                result = resp.json()
                break

            except httpx.TimeoutException:
                last_error = "Request timed out"
                if attempt < max_retries:
                    logger.warning(f"Timeout, retrying (attempt {attempt+1})")
                    continue
                raise RuntimeError(
                    f"LLM API timed out after {max_retries+1} attempts ({timeout}s each)."
                )
            except httpx.ConnectError as e:
                raise RuntimeError(
                    f"Cannot connect to LLM API at {base_url}: {e}. "
                    "Is the server running?"
                )
        else:
            raise RuntimeError(
                f"LLM API failed after {max_retries+1} attempts: {last_error}"
            )

    content = result["choices"][0]["message"]["content"]

    # Parse JSON — handle markdown code blocks if model wraps output
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*\n?", "", content)
        content = re.sub(r"\n?\s*```$", "", content)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM returned invalid JSON: {e}\nContent: {content[:500]}")

    # Normalize structure
    if "tools" not in parsed:
        if isinstance(parsed, list):
            parsed = {"tools": parsed, "site": {"name": extraction.title, "description": ""}}
        else:
            parsed["tools"] = []

    if "site" not in parsed:
        parsed["site"] = {"name": extraction.title, "description": extraction.description}

    return parsed


def analyze_without_llm(extraction: PageExtraction) -> dict:
    """Generate tool definitions heuristically (no LLM needed).

    Produces tools with `_bindings` that map parameters to selectors,
    so the executor can use them directly without guessing.
    """
    tools = []
    seen_names: set[str] = set()

    for el in extraction.elements:
        if el.kind == "form" and el.fields:
            # Filter to actual input fields (not submit/hidden)
            input_fields = [
                f for f in el.fields
                if f.type not in ("submit", "button", "reset", "image", "hidden")
                and f.tag != "button"
            ]
            if not input_fields:
                continue

            name = _infer_tool_name(el)
            if name in seen_names:
                name = f"{name}{el.element_index}"
            seen_names.add(name)

            properties = {}
            required = []
            for f in input_fields:
                param_name = _clean_param_name(
                    f.name or f.id or f.label.lower().replace("", "_")
                )
                if not param_name or param_name == "param":
                    continue

                prop: dict = {"type": _field_type_to_json_type(f.type)}
                desc = f.label or f.placeholder or f.aria_label
                if desc:
                    prop["description"] = desc
                if f.options:
                    prop["enum"] = f.options
                if f.type == "date":
                    prop["format"] = "date"
                # Bind parameter to its DOM selector
                prop["_selector"] = f.selector

                properties[param_name] = prop
                if f.required:
                    required.append(param_name)

            if not properties:
                continue

            tool: dict = {
                "name": name,
                "description": _infer_description(el),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
                "_submit_selector": el.selector,
                "_element_index": el.element_index,
            }
            if required:
                tool["parameters"]["required"] = required
            tools.append(tool)

        elif el.kind == "button" and el.text:
            # Filter noise
            if len(el.text) > 30:
                continue
            if el.text.endswith("?"):
                continue

            name = _infer_tool_name(el)
            noise = {
                "close", "dismiss", "x", "ok", "cancel", "accept",
                "gotIt", "hide", "show", "toggle", "submit", "menu",
            }
            if name in noise:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)

            tools.append({
                "name": name,
                "description": f"Click the '{el.text}' button",
                "parameters": {"type": "object", "properties": {}},
                "_submit_selector": el.selector,
                "_element_index": el.element_index,
            })

    # Group nav links into one tool
    nav_links = [el for el in extraction.elements if el.kind == "link"]
    if nav_links:
        pages = []
        seen_pages: set[str] = set()
        for link in nav_links:
            if link.text and link.text not in seen_pages:
                seen_pages.add(link.text)
                pages.append(link.text)

        if pages:
            # Build a mapping of page name  selector for execution
            link_bindings = {}
            for link in nav_links:
                if link.text and link.text not in link_bindings:
                    link_bindings[link.text] = link.selector

            tools.append({
                "name": "navigate",
                "description": "Navigate to a section of the site",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page": {
                            "type": "string",
                            "enum": pages[:20],
                            "description": "Target page or section",
                        }
                    },
                    "required": ["page"],
                },
                "_link_bindings": link_bindings,
                "_element_index": nav_links[0].element_index if nav_links else -1,
            })

    return {
        "tools": tools,
        "site": {
            "name": extraction.title,
            "description": extraction.description or f"Tools extracted from {extraction.url}",
        },
    }


# --- Helpers ---

def _infer_tool_name(el: InteractiveElement) -> str:
    """Infer a camelCase tool name from an element."""
    # Prefer aria-label for forms as it's usually more semantic
    text = el.aria_label or el.text or el.context or "action"
    # Clean: remove special chars, limit to 4 words
    words = re.sub(r'[^a-zA-Z0-9\s]', '', text).split()[:4]
    words = [w.lower() for w in words if len(w) > 1]
    if not words:
        return "submit"
    return words[0] + "".join(w.capitalize() for w in words[1:])


def _infer_description(el: InteractiveElement) -> str:
    """Infer a tool description from form context."""
    parts = []
    if el.context:
        parts.append(el.context)
    if el.aria_label:
        parts.append(el.aria_label)
    if el.text and el.text.lower() not in ("submit", "go", "search"):
        parts.append(el.text)
    if parts:
        return " — ".join(parts[:2])
    # Fallback: describe by fields
    field_names = [
        f.label or f.placeholder or f.name
        for f in el.fields[:3]
        if f.type not in ("submit", "hidden")
    ]
    if field_names:
        return f"Submit form with: {', '.join(field_names)}"
    return "Submit form"


def _clean_param_name(name: str) -> str:
    """Clean a parameter name to be a valid camelCase identifier."""
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    parts = name.split('_')
    if parts:
        name = parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
    if name and name[0].isdigit():
        name = 'field' + name
    return name or "param"


def _field_type_to_json_type(html_type: str) -> str:
    """Map HTML input type to JSON schema type."""
    mapping = {
        "number": "number", "range": "number",
        "checkbox": "boolean",
        "date": "string", "datetime-local": "string",
        "email": "string", "url": "string", "tel": "string",
        "password": "string", "hidden": "string",
        "radio": "string", "color": "string",
        "file": "string", "search": "string",
    }
    return mapping.get(html_type, "string")
