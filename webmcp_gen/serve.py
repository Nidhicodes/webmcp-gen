"""MCP server exposing generated website tools.

stdio transport uses the official `mcp` SDK so real clients (Claude Desktop,
Kiro, Cline) connect directly. SSE and streamable-HTTP are available for network
clients; a minimal JSON-RPC HTTP mode exists for testing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Optional

from .extract import extract_page, PageExtraction
from .analyze import analyze_without_llm, analyze_with_llm
from .execute import WebExecutor

logger = logging.getLogger(__name__)

SERVER_NAME = "webmcp-gen"
SERVER_VERSION = "0.6.1"


class ToolRegistry:
    """Holds tool definitions + a lazily-created executor.

    Transport-agnostic: both the stdio (SDK) and HTTP servers use this.
    """

    def __init__(self, url: str, extraction: PageExtraction, tools: dict,
                 execute: bool = True, headless: bool = True,
                 storage_state: Optional[dict] = None):
        self.url = url
        self.extraction = extraction
        self.tools_data = tools
        self.execute = execute
        self.headless = headless
        self.storage_state = storage_state
        self._executor: Optional[WebExecutor] = None
        self._lock = asyncio.Lock()

    def mcp_tools(self) -> list[dict]:
        """Tool list in MCP shape (inputSchema), stripped of internal bindings."""
        out = []
        for t in self.tools_data.get("tools", []):
            params = t.get("parameters", {})
            clean_props = {}
            for pname, pdef in params.get("properties", {}).items():
                clean_props[pname] = {k: v for k, v in pdef.items() if not k.startswith("_")}
            schema = {"type": "object", "properties": clean_props}
            if "required" in params:
                schema["required"] = params["required"]
            out.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "inputSchema": schema,
            })
        return out

    async def call(self, name: str, arguments: dict) -> str:
        """Execute a tool and return a JSON string result."""
        if not self.execute:
            return json.dumps({"error": "Execution disabled (read-only mode)"})

        async with self._lock:
            if self._executor is None:
                self._executor = WebExecutor(
                    self.url,
                    tools=self.tools_data.get("tools", []),
                    extraction=self.extraction,
                    headless=self.headless,
                    storage_state=self.storage_state,
                )
                await self._executor.__aenter__()

            result = await self._executor.call(name, arguments)
            return json.dumps(result.to_dict(), indent=2)

    async def shutdown(self):
        if self._executor:
            await self._executor.__aexit__(None, None, None)
            self._executor = None


# --- stdio transport (official MCP SDK) ---

async def run_stdio(registry: ToolRegistry):
    """Run a spec-compliant MCP server over stdio using the official SDK."""
    try:
        from mcp.server.stdio import stdio_server
    except ImportError:
        raise RuntimeError(
            "The 'mcp' package is required for stdio transport. "
            "Install it with: pip install mcp\n"
            "Or use HTTP transport: --http"
        )

    server = _build_sdk_server(registry)
    init_options = server.create_initialization_options()

    logger.info("MCP stdio server starting")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)
    finally:
        await registry.shutdown()


def _build_sdk_server(registry: ToolRegistry):
    """Build a low-level MCP SDK Server wired to the registry. Shared by stdio + SSE."""
    from mcp.server import Server
    import mcp.types as types

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in registry.mcp_tools()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list:
        result_json = await registry.call(name, arguments or {})
        return [types.TextContent(type="text", text=result_json)]

    return server


# --- SSE / streamable-HTTP transport (official MCP SDK) ---

async def run_sse(registry: ToolRegistry, port: int = 3000,
                  transport: str = "sse"):
    """Run a spec-compliant MCP server over SSE or streamable-HTTP.

    Uses the official SDK's network transports so MCP clients that connect over
    HTTP (rather than spawning a stdio subprocess) work correctly.

    transport: "sse" or "streamable-http"
    """
    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from mcp.server.sse import SseServerTransport
    except ImportError as e:
        raise RuntimeError(
            "SSE transport needs 'uvicorn' and 'starlette' "
            "(installed with the 'mcp' package extras). "
            f"Missing: {e}. Falling back: use --http for plain JSON-RPC."
        )

    server = _build_sdk_server(registry)
    init_options = server.create_initialization_options()

    if transport == "streamable-http":
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        session_manager = StreamableHTTPSessionManager(app=server)

        async def handle_streamable(scope, receive, send):
            await session_manager.handle_request(scope, receive, send)

        from starlette.routing import Mount as _Mount
        import contextlib

        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with session_manager.run():
                yield
            await registry.shutdown()

        app = Starlette(
            routes=[_Mount("/mcp", app=handle_streamable)],
            lifespan=lifespan,
        )
        print(f"streamable-HTTP MCP server on http://localhost:{port}/mcp",
              file=sys.stderr)
    else:
        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, init_options)

        from starlette.routing import Route
        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ])
        print(f"SSE MCP server on http://localhost:{port}/sse", file=sys.stderr)

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    uv_server = uvicorn.Server(config)
    try:
        await uv_server.serve()
    finally:
        await registry.shutdown()


# --- HTTP transport (minimal JSON-RPC, for testing) ---

async def run_http(registry: ToolRegistry, port: int = 3000):
    """Run a minimal JSON-RPC-over-HTTP server (for testing / non-MCP clients)."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading

    loop = asyncio.get_event_loop()

    def handle_rpc(request: dict) -> Optional[dict]:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": f"{SERVER_NAME} ({registry.url})", "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            }}
        if method in ("initialized", "notifications/initialized"):
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": registry.mcp_tools()}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            future = asyncio.run_coroutine_threadsafe(registry.call(name, arguments), loop)
            text = future.result(timeout=90)
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": text}],
            }}
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            request = json.loads(body) if body else {}
            try:
                response = handle_rpc(request)
            except Exception as e:
                response = {"jsonrpc": "2.0", "id": request.get("id"),
                            "error": {"code": -32603, "message": str(e)}}
            if response is None:
                self.send_response(204)
                self.end_headers()
                return
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            tools = registry.mcp_tools()
            info = {
                "server": SERVER_NAME, "version": SERVER_VERSION, "url": registry.url,
                "tools_count": len(tools),
                "tools": [{"name": t["name"], "description": t["description"]} for t in tools],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(info, indent=2).encode())

        def log_message(self, *args):
            pass

    http_server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"HTTP server on http://localhost:{port}  (GET=tool list, POST=JSON-RPC)",
          file=sys.stderr)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        http_server.shutdown()
        await registry.shutdown()


# --- Entrypoint ---

async def serve(url: str, port: int = 3000, llm: bool = False,
                model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1",
                execute: bool = True, transport: str = "stdio", headless: bool = True,
                storage_state: Optional[dict] = None):
    """Extract + analyze a site, then start the MCP server.

    transport: one of "stdio", "sse", "streamable-http", "http" (plain JSON-RPC).
    storage_state: optional Playwright storage_state for an authenticated session.
    """
    print(f"Extracting tools from: {url}", file=sys.stderr)
    extraction = await extract_page(url)
    print(f"Found {len(extraction.elements)} interactive elements", file=sys.stderr)

    if llm:
        print(f"Analyzing with {model}...", file=sys.stderr)
        tools = await analyze_with_llm(extraction, model=model, base_url=base_url)
    else:
        tools = analyze_without_llm(extraction)

    n = len(tools.get("tools", []))
    print(f"Generated {n} tools", file=sys.stderr)
    for t in tools.get("tools", []):
        params = [p for p in t.get("parameters", {}).get("properties", {})]
        print(f"- {t['name']}({', '.join(params)})", file=sys.stderr)

    registry = ToolRegistry(url, extraction, tools, execute=execute,
                            headless=headless, storage_state=storage_state)

    if transport == "stdio":
        await run_stdio(registry)
    elif transport in ("sse", "streamable-http"):
        await run_sse(registry, port, transport=transport)
    elif transport == "http":
        await run_http(registry, port)
    else:
        raise ValueError(f"Unknown transport: {transport}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Serve website tools as an MCP server")
    parser.add_argument("url", help="URL to generate tools from")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http", "http"],
                        default="stdio",
                        help="Transport: stdio (default), sse, streamable-http, "
                             "or http (plain JSON-RPC for testing)")
    parser.add_argument("--http", action="store_true",
                        help="Shortcut for --transport http (plain JSON-RPC)")
    parser.add_argument("--sse", action="store_true",
                        help="Shortcut for --transport sse")
    parser.add_argument("--port", type=int, default=3000, help="Port (network transports)")
    parser.add_argument("--llm", action="store_true", help="Use LLM for analysis")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--groq", action="store_true",
                        help="Use Groq API (llama-3.3-70b-versatile)")
    parser.add_argument("--no-execute", action="store_true", help="Read-only mode")
    parser.add_argument("--session", metavar="NAME",
                        help="Use a saved session (run 'webmcp-login' first)")
    parser.add_argument("--headful", action="store_true",
                        help="Show the browser (helps bypass some bot detection)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.groq:
        args.llm = True
        args.model = "llama-3.3-70b-versatile"
        args.base_url = "https://api.groq.com/openai/v1"

    # Resolve transport shortcuts
    transport = args.transport
    if args.http:
        transport = "http"
    elif args.sse:
        transport = "sse"

    # Load a saved session if requested
    storage_state = None
    if args.session:
        from .session import load_session
        storage_state = load_session(args.session)
        if storage_state is None:
            print(f"No saved session '{args.session}'. "
                  f"Run: webmcp-login {args.url} --session {args.session}",
                  file=sys.stderr)

    # stdio mode must keep stdout clean for JSON-RPC — log to stderr only
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        stream=sys.stderr,
    )

    asyncio.run(serve(
        args.url, port=args.port, llm=args.llm, model=args.model,
        base_url=args.base_url, execute=not args.no_execute,
        transport=transport, headless=not args.headful,
        storage_state=storage_state,
    ))


if __name__ == "__main__":
    main()
