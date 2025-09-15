import asyncio, aiohttp, json, re, os
from typing import Optional, Dict, Any

BASE = os.environ.get("MCP_BASE", "http://localhost:8931")
MCP_URL = f"{BASE}/mcp"

# Try the broadly compatible protocol first; update if your server needs newer.
PROTO_CANDIDATES = ["2024-11-05", "2025-06-18"]

def parse_json_or_sse(txt: str):
    t = (txt or "").strip()
    if not t:
        return {}
    if t.startswith("{") or t.startswith("["):
        return json.loads(t)
    # Some servers reply as SSE on POST: last data: { ... }
    m = re.findall(r"^data:\s*(\{.*\})\s*$", t, flags=re.M)
    if m:
        return json.loads(m[-1])
    raise ValueError(f"Could not find JSON payload in:\n{t[:200]}")

async def post_jsonrpc(session: aiohttp.ClientSession, payload: Dict[str, Any],
                       proto: str, sid: Optional[str]) -> aiohttp.ClientResponse:
    headers = {
        # Spec: must advertise BOTH types on POST
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": proto,  # use negotiated version after init
    }
    if sid:
        headers["Mcp-Session-Id"] = sid  # if server gave you one at init
    return await session.post(MCP_URL, json=payload, headers=headers)

async def initialize(session: aiohttp.ClientSession) -> tuple[str, Optional[str]]:
    """
    Returns (protocol_version_negotiated, session_id_or_None)
    """
    for proto in PROTO_CANDIDATES:
        resp = await post_jsonrpc(session, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": proto,
                "clientInfo": {"name": "spec-http-client", "version": "0.1"},
                "capabilities": {}
            }
        }, proto=proto, sid=None)

        txt = await resp.text()
        if resp.status != 200:
            # if complaint mentions version/protocol, try next proto
            if resp.status == 400 and ("protocol" in txt.lower() or "version" in txt.lower()):
                continue
            raise RuntimeError(f"initialize HTTP {resp.status}: {txt}")

        data = parse_json_or_sse(txt)
        if "error" in data:
            raise RuntimeError(f"initialize error: {data['error']}")

        # pull negotiated version from result if present; else keep proto we sent
        result = data.get("result", {})
        negotiated = result.get("protocolVersion", proto)

        # spec: server MAY set session id in Mcp-Session-Id response header
        sid = resp.headers.get("Mcp-Session-Id")
        return negotiated, sid

    raise RuntimeError("initialize: all protocol versions failed")

async def notify_initialized(session, proto, sid):
    resp = await post_jsonrpc(session, {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {}
    }, proto=proto, sid=sid)

    # Spec: accepted notifications may return 202 with no body
    _ = await resp.text()   # drain if any
    if resp.status not in (200, 202):
        raise RuntimeError(f"notifications/initialized HTTP {resp.status}: {_}")

async def rpc(session: aiohttp.ClientSession, proto: str, sid: Optional[str],
              method: str, params: Dict[str, Any], msg_id: int):
    resp = await post_jsonrpc(session, {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params or {}
    }, proto=proto, sid=sid)
    txt = await resp.text()
    if resp.status == 404 and sid:
        # spec: 404 means the session id is no longer valid -> re-init
        raise RuntimeError(f"{method} got 404 (session expired)")
    if resp.status != 200:
        raise RuntimeError(f"{method} HTTP {resp.status}: {txt}")
    data = parse_json_or_sse(txt)
    if "error" in data:
        raise RuntimeError(f"{method} error: {data['error']}")
    return data.get("result")

async def main():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        proto, sid = await initialize(s)
        print("[OK] initialize", {"protocolVersion": proto, "sessionId": sid})

        # MUST send initialized before normal requests
        try:
            await notify_initialized(s, proto, sid)
            print("[OK] notifications/initialized")
        except Exception as e:
            # Some servers are lax; print and continue. If the server enforces it,
            # the next call will still fail and you’ll see why.
            print("[WARN] notifications/initialized failed:", e)

        # Normal request: tools/list
        tools = await rpc(s, proto, sid, "tools/list", {}, msg_id=2)
        print("[OK] tools/list:", [t.get("name") for t in tools.get("tools", [])])

        # Example tool call if present
        if any(t.get("name") == "browser_tabs" for t in tools.get("tools", [])):
            res = await rpc(s, proto, sid, "tools/call",
                            {"name": "browser_tabs", "arguments": {"action": "list"}},
                            msg_id=3)
            print("[OK] browser_tabs:", res)

if __name__ == "__main__":
    asyncio.run(main())
