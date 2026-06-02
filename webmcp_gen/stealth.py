"""Anti-detection evasions for headless Chromium.

Injects an init script that patches the signals bot-detection reads:
navigator.webdriver, window.chrome, plugins, languages, WebGL vendor, and a few
others. Dependency-free subset of what playwright-stealth does.
"""

from __future__ import annotations

# A realistic, current desktop Chrome UA on macOS.
STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Launch args that reduce automation signals.
STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

# The init script injected into every page/frame before site scripts run.
_STEALTH_INIT_SCRIPT = r"""
(() => {
    // 1. navigator.webdriver  undefined
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    } catch (e) {}

    // 2. window.chrome object (present in real Chrome, absent in headless)
    try {
        if (!window.chrome) {
            window.chrome = {};
        }
        window.chrome.runtime = window.chrome.runtime || {};
        window.chrome.app = window.chrome.app || { isInstalled: false };
        window.chrome.csi = window.chrome.csi || function () {};
        window.chrome.loadTimes = window.chrome.loadTimes || function () {};
    } catch (e) {}

    // 3. navigator.plugins — real browsers report a few
    try {
        const fakePlugin = (name, filename, desc) => ({
            name, filename, description: desc, length: 1,
        });
        const plugins = [
            fakePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
            fakePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
            fakePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
        ];
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = plugins.slice();
                arr.item = (i) => arr[i];
                arr.namedItem = (n) => arr.find(p => p.name === n) || null;
                arr.refresh = () => {};
                return arr;
            },
        });
    } catch (e) {}

    // 4. navigator.languages
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });
    } catch (e) {}

    // 5. permissions.query — headless returns "denied" for notifications oddly
    try {
        const originalQuery = window.navigator.permissions &&
            window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) =>
                parameters && parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters);
        }
    } catch (e) {}

    // 6. WebGL vendor/renderer — headless reports SwiftShader (software)
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
            // UNMASKED_VENDOR_WEBGL
            if (parameter === 37445) return 'Intel Inc.';
            // UNMASKED_RENDERER_WEBGL
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.apply(this, [parameter]);
        };
        if (typeof WebGL2RenderingContext !== 'undefined') {
            const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function (parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter2.apply(this, [parameter]);
            };
        }
    } catch (e) {}

    // 7. navigator.hardwareConcurrency / deviceMemory — give realistic values
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    } catch (e) {}

    // 8. window.outerWidth/outerHeight — headless reports 0
    try {
        if (window.outerWidth === 0) {
            Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
            Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 74 });
        }
    } catch (e) {}
})();
"""


def stealth_context_options() -> dict:
    """Return new_context kwargs that improve stealth."""
    return {
        "viewport": {"width": 1280, "height": 800},
        "user_agent": STEALTH_USER_AGENT,
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
        },
    }


async def apply_stealth(context) -> None:
    """Inject the stealth init script into a Playwright browser context.

    Must be called before pages navigate. The script runs in every page/frame
    before site scripts execute.
    """
    await context.add_init_script(_STEALTH_INIT_SCRIPT)
