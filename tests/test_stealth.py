"""Tests for stealth evasions."""

import pytest

from webmcp_gen.stealth import (
    stealth_context_options,
    apply_stealth,
    STEALTH_LAUNCH_ARGS,
    STEALTH_USER_AGENT,
)


# Probe script: reports the detection signals stealth is meant to patch.
_PROBE = """
() => ({
    webdriver: navigator.webdriver,
    hasChrome: !!window.chrome,
    pluginsLength: navigator.plugins.length,
    languages: navigator.languages,
    hardwareConcurrency: navigator.hardwareConcurrency,
    webglVendor: (() => {
        try {
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl');
            return gl.getParameter(37445);
        } catch(e) { return 'error'; }
    })(),
})
"""


class TestStealthConfig:
    def test_context_options_shape(self):
        opts = stealth_context_options()
        assert "user_agent" in opts
        assert "viewport" in opts
        assert opts["locale"] == "en-US"
        assert "Accept-Language" in opts["extra_http_headers"]

    def test_user_agent_is_realistic(self):
        assert "Chrome" in STEALTH_USER_AGENT
        assert "HeadlessChrome" not in STEALTH_USER_AGENT

    def test_launch_args_disable_automation(self):
        joined = " ".join(STEALTH_LAUNCH_ARGS)
        assert "AutomationControlled" in joined


@pytest.mark.timeout(45)
class TestStealthLive:
    """Verify stealth actually patches detection signals in a real browser."""

    @pytest.mark.asyncio
    async def test_stealth_patches_signals(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=STEALTH_LAUNCH_ARGS)
            ctx = await browser.new_context(**stealth_context_options())
            await apply_stealth(ctx)
            page = await ctx.new_page()
            await page.goto("about:blank")
            signals = await page.evaluate(_PROBE)
            await browser.close()

        # webdriver must not be True
        assert signals["webdriver"] is not True
        # window.chrome should be present
        assert signals["hasChrome"] is True
        # plugins should be non-empty
        assert signals["pluginsLength"] > 0
        # languages should have the fallback entry
        assert "en" in signals["languages"]
        # WebGL should not report the software renderer
        assert "swiftshader" not in str(signals["webglVendor"]).lower()

    @pytest.mark.asyncio
    async def test_no_stealth_leaks_webdriver(self):
        """Control: without stealth, webdriver IS detectable (proves the test is real)."""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto("about:blank")
            signals = await page.evaluate(_PROBE)
            await browser.close()

        # Headless Chromium leaks webdriver=True by default
        assert signals["webdriver"] is True
