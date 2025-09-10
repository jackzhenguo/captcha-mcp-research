import asyncio, json
from registry import AgentState, MCPServer
from graph import build_app
from mcp_client import MCPConnector


async def main():
    # If you run Playwright MCP locally with:  
    # npx @playwright/mcp@latest --port 8931
    # then your SSE endpoint is http://localhost:8931/sse
    servers: list[MCPServer] = [
        {
            "id": "playwright-local",
            "base_url": "http://localhost:8931/sse",   # SSE endpoint of Playwright MCP
            "auth": None,                           
            "tools": [
                {"name": "browser_tab_new", "schema": {"url": {"type": "string", "required": False}}},
                {"name": "browser_tab_select", "schema": {"index": {"type": "number"}}},
                {"name": "browser_navigate", "schema": {"url": {"type": "string"}}},
                {"name": "browser_snapshot", "schema": {}},
                # Optional helpers you may use later:
                {"name": "browser_take_screenshot", "schema": {"raw": {"type": "boolean", "required": False}}},
                {"name": "browser_wait", "schema": {"time": {"type": "number"}}},
                {"name": "browser_close", "schema": {}},
            ],
            "tags": ["browser", "region:us", "playwright"],
            "healthy": True,
        },
    ]

    init_state: AgentState = {
        "task": "Invisable reCaptcha",
        "targets": ["https://jackzhenguo.github.io/captcha-mcp-research/", ],
        "servers": servers,
        "backoff_until": {},
    }

    app = build_app()
    final = await app.ainvoke(init_state)
    print(json.dumps(final, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
