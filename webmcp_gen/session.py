"""Persist and restore browser sessions (cookies + localStorage).

A session is Playwright's storage_state saved as JSON under a chosen name in
~/.cache/webmcp-gen/sessions/ (override with WEBMCP_CACHE_DIR). Files contain
auth cookies and are written 0600 — treat them as secrets.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _sessions_dir() -> Path:
    override = os.environ.get("WEBMCP_CACHE_DIR")
    base = Path(override) if override else (Path.home() / ".cache" / "webmcp-gen")
    d = base / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:64]


def session_path(name: str) -> Path:
    return _sessions_dir() / f"{_safe_name(name)}.json"


def save_session(name: str, storage_state: dict) -> Path:
    """Persist a storage_state dict under `name`. Returns the file path."""
    path = session_path(name)
    with open(path, "w") as f:
        json.dump(storage_state, f)
    # Restrict permissions — these contain auth cookies
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    logger.info(f"saved session '{name}' to {path}")
    return path


def load_session(name: str) -> Optional[dict]:
    """Load a previously saved storage_state, or None if not found/invalid."""
    path = session_path(name)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_sessions() -> list[str]:
    """Return the names of saved sessions."""
    return sorted(p.stem for p in _sessions_dir().glob("*.json"))


def delete_session(name: str) -> bool:
    """Delete a saved session. Returns True if it existed."""
    path = session_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


async def capture_session(url: str, headless: bool = False,
                          wait_seconds: int = 120, stealth: bool = True) -> dict:
    """Open a browser at `url` so a human can log in, then capture the session.

    Runs headful by default. Waits up to `wait_seconds` for the user to finish,
    or until they close the page. Returns the storage_state dict.

    This is the human-in-the-loop step: webmcp-gen never handles passwords; the
    user authenticates in a real browser window and we capture the resulting
    cookies/localStorage.
    """
    from playwright.async_api import async_playwright
    from .stealth import (
        stealth_context_options, apply_stealth, STEALTH_LAUNCH_ARGS, STEALTH_USER_AGENT,
    )

    async with async_playwright() as p:
        launch_args = STEALTH_LAUNCH_ARGS if stealth else []
        browser = await p.chromium.launch(headless=headless, args=launch_args)
        if stealth:
            context = await browser.new_context(**stealth_context_options())
            await apply_stealth(context)
        else:
            context = await browser.new_context(user_agent=STEALTH_USER_AGENT)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")

        if not headless:
            print(f"\nLog in to {url} in the browser window.")
            print(f"Waiting up to {wait_seconds}s. Close the page when done.\n")
            try:
                # Wait until the page is closed or the timeout elapses
                await page.wait_for_event("close", timeout=wait_seconds * 1000)
            except Exception:
                pass  # timeout — capture whatever state exists

        try:
            state = await context.storage_state()
        finally:
            await context.close()
            await browser.close()
    return state


def main():
    """CLI: capture or manage browser sessions.

    Examples:
        webmcp-login https://github.com/login --session github
        webmcp-login --list
        webmcp-login --delete github
    """
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        prog="webmcp-login",
        description="Capture an authenticated browser session for webmcp-gen",
    )
    parser.add_argument("url", nargs="?", help="Login URL to open")
    parser.add_argument("--session", metavar="NAME", help="Name to save the session under")
    parser.add_argument("--wait", type=int, default=120,
                        help="Seconds to wait for login (default: 120)")
    parser.add_argument("--list", action="store_true", help="List saved sessions")
    parser.add_argument("--delete", metavar="NAME", help="Delete a saved session")
    parser.add_argument("--headless", action="store_true",
                        help="Run headless (no window — only for already-authed flows)")
    args = parser.parse_args()

    if args.list:
        sessions = list_sessions()
        if sessions:
            print("Saved sessions:")
            for s in sessions:
                print(f"- {s}")
        else:
            print("No saved sessions.")
        return

    if args.delete:
        ok = delete_session(args.delete)
        print(f"{'Deleted' if ok else 'No such session:'} {args.delete}")
        return

    if not args.url or not args.session:
        parser.error("url and --session are required to capture a session")

    state = asyncio.run(capture_session(
        args.url, headless=args.headless, wait_seconds=args.wait,
    ))
    path = save_session(args.session, state)
    n_cookies = len(state.get("cookies", []))
    print(f"Saved session '{args.session}' ({n_cookies} cookies)  {path}")
    print(f"Use it: webmcp-gen <url> --session {args.session}  (or the serve command)")


if __name__ == "__main__":
    main()
