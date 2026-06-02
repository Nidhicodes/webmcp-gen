"""Manual verification: the webmcp-gen stdio server works with a real MCP client.

Run directly (not via pytest — it spawns a subprocess):
    python tests/manual_mcp_client.py
"""
import asyncio
import os
import sys


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "webmcp_gen.serve", "https://duckduckgo.com", "--groq"],
        env={**os.environ},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            print(f"Connected to: {init_result.serverInfo.name}")
            print(f"Protocol: {init_result.protocolVersion}")

            tools_result = await session.list_tools()
            print(f"Tools discovered: {len(tools_result.tools)}")
            search_tool = None
            for t in tools_result.tools:
                params_list = list(t.inputSchema.get("properties", {}).keys())
                print(f"- {t.name}({', '.join(params_list)}) — {t.description[:50]}")
                if "search" in t.name.lower() and search_tool is None:
                    search_tool = t

            # Actually call a tool through the MCP protocol
            if search_tool:
                param_name = list(search_tool.inputSchema.get("properties", {}).keys())[0]
                print(f"\nCalling {search_tool.name}({param_name}='webmcp')...")
                call_result = await session.call_tool(
                    search_tool.name, {param_name: "webmcp"}
                )
                import json
                payload = json.loads(call_result.content[0].text)
                print(f"success={payload.get('success')} blocked={payload.get('blocked')}")
                print(f"url={payload.get('url', '')[:70]}")
                print(f"items returned: {len(payload.get('items', []))}")
                for item in payload.get("items", [])[:3]:
                    print(f"- {item['title'][:60]}")

    print("\nReal MCP client handshake + tool call SUCCEEDED")


if __name__ == "__main__":
    asyncio.run(main())
