import asyncio, json
from registry import AgentState, MCPServer
from graph import build_app
from mcp_client import MCPConnector


async def main():
    servers: list[MCPServer] = [
        {
            "id": "browser-us-1",
            "base_url": "https://mcp.example.com/browser-us-1",
            "auth": {"type": "bearer", "token": "XXX"},
            "tools": [{"name": "web.open", "schema": {}},
                      {"name": "web.wait", "schema": {}},
                      {"name": "web.text", "schema": {}}],
            "tags": ["browser", "region:us"],
            "healthy": True,
        }
    ]

    # Monkey-patch for demo
    async def fake_call_tool(self, name, args):
        if "web" in name:
            return {"ok": True, "tool": name, "args": args}
        
        raise RuntimeError("Simulated failure")
    
    MCPConnector.call_tool = fake_call_tool

    init_state: AgentState = {
        "task": "Fetch homepage title",
        "targets": ["https://example.com"],
        "servers": servers,
        "backoff_until": {},
    }

    app = build_app()
    final = await app.ainvoke(init_state)
    print(json.dumps(final, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
