import asyncio
import json
from registry import AgentState, MCPServer
from graph import build_app

def summarize(final_state: dict) -> str:
    """
    Produce a concise PASS/FAIL summary from the final graph state.
    Looks for executor outputs[*]['_verdict_struct'] populated by executor.py.
    """
    res = final_state.get("result", {})
    outputs = res.get("outputs", {}) or {}
    lines = []

    if not outputs:
        # No outputs; show last_error to aid debugging.
        lines.append(f"[NO RESULT] last_error={final_state.get('last_error')}")
        return "\n".join(lines)

    for url, out in outputs.items():
        verdict_struct = out.get("_verdict_struct") or {}
        ok = verdict_struct.get("success")
        raw = verdict_struct.get("raw")
        if ok is True:
            lines.append(f"[SUCCESS] {url} -> {raw}")
        elif ok is False:
            lines.append(f"[FAIL]    {url} -> {raw}")
        else:
            lines.append(f"[UNKNOWN] {url} -> {raw}")
    return "\n".join(lines)

async def main():
    servers: list[MCPServer] = [
        {
            "id": "playwright-local",
            # JSON-RPC transport (use /mcp for this client)
            "base_url": "http://localhost:8931/mcp",
            "auth": None,
            # Let the connector enumerate tools from the server
            "tools": [],
            "tags": ["browser", "region:us", "playwright"],
            "healthy": True,
        },
    ]

    init_state: AgentState = {
        "task": "Invisible reCAPTCHA: automatically click the verify button",
        "targets": ["https://captcha-mcp-vercel-client.vercel.app"],
        "servers": servers,
        "backoff_until": {},

        "max_attempts": 3,
        "attempts": 0,
        "idx": 0,

        # At least one candidate so the graph runs
        "shortlist": [
            {"server_id": "playwright-local", "score": 1.0, "reason": "manual"}
        ],

        "result": {},
        "last_error": "",
    }

    app = build_app()
    final = await app.ainvoke(init_state)

    # Print a concise verdict first, then the full JSON for debugging.
    print(summarize(final))
    print(json.dumps(final, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
