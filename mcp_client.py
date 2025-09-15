# mcp_connector.py
import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp


class MCPError(RuntimeError):
    pass


def _parse_json_or_sse(txt: str) -> dict:
    """
    Accept either plain JSON text or SSE-like text containing JSON 'data:' lines.
    Returns the JSON-RPC envelope.
    """
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


class MCPConnector:
    """
    Minimal viable connector with:
      - Protocol negotiation (tries several versions and adopts server's if returned)
      - Session forwarding via 'Mcp-Session-Id' header (captured from initialize response)
      - Spec-compliant POST headers (Accept both JSON and SSE)
      - notifications/initialized right after initialize
      - Simple RPC helper + convenience methods for tools/list and tools/call
    """

    PROTO_CANDIDATES: Tuple[str, ...] = ("2024-11-05", "2025-06-18")

    def __init__(self, base_url: str, timeout: int = 60):
        """
        base_url: e.g., http://localhost:8931/mcp   (the JSON-RPC POST endpoint)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self._session: Optional[aiohttp.ClientSession] = None
        self._protocol: Optional[str] = None  # negotiated protocol version
        self._session_id: Optional[str] = None  # from response header on initialize

        # state
        self.available_tools: List[Dict[str, Any]] = []

    # ---------------- Context manager ----------------

    async def __aenter__(self) -> "MCPConnector":
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        await self._initialize_flow()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session:
            await self._session.close()
            self._session = None

    # ---------------- Public API (MVP) ----------------

    async def list_tools(self) -> List[Dict[str, Any]]:
        res = await self._rpc("tools/list", {}, msg_id=2)
        tools = res.get("tools", []) if isinstance(res, dict) else []
        self.available_tools = tools
        return tools

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        res = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            msg_id=3,
        )
        return res if isinstance(res, dict) else {}

    # ---------------- Core flow ----------------

    async def _initialize_flow(self):
        """
        Initialize → notifications/initialized → (optionally list tools)
        """
        proto, sid = await self._initialize_negotiate()
        self._protocol = proto
        self._session_id = sid

        await self._notify_initialized()

        # Optional: eager fetch tools to mirror your working script
        await self.list_tools()

    async def _initialize_negotiate(self) -> Tuple[str, Optional[str]]:
        """
        Returns (negotiated_protocol_version, session_id_or_None).
        Tries PROTO_CANDIDATES; adopts server-returned protocolVersion if present.
        """
        assert self._session, "HTTP session not ready"

        for proto in self.PROTO_CANDIDATES:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": proto,
                    "clientInfo": {"name": "spec-http-client", "version": "0.1"},
                    "capabilities": {},
                },
            }
            resp = await self._post_jsonrpc(payload, proto=proto, sid=None)
            txt = await resp.text()

            if resp.status != 200:
                # If complaint mentions version/protocol, try next proto
                if resp.status == 400 and ("protocol" in txt.lower() or "version" in txt.lower()):
                    continue
                raise MCPError(f"initialize HTTP {resp.status}: {txt}")

            data = _parse_json_or_sse(txt)
            if "error" in data:
                raise MCPError(f"initialize error: {data['error']}")

            result = data.get("result", {}) if isinstance(data, dict) else {}
            negotiated = result.get("protocolVersion", proto)

            # spec: server MAY set session id in Mcp-Session-Id response header
            sid = resp.headers.get("Mcp-Session-Id")
            return negotiated, sid

        raise MCPError("initialize: all protocol versions failed")

    async def _notify_initialized(self):
        """
        notifications/initialized — accept 200 or 202. Drain text to keep session clean.
        """
        assert self._session, "HTTP session not ready"
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        resp = await self._post_jsonrpc(payload, proto=self._protocol or self.PROTO_CANDIDATES[0], sid=self._session_id)
        _ = await resp.text()
        if resp.status not in (200, 202):
            raise MCPError(f"notifications/initialized HTTP {resp.status}: {_}")

    async def _rpc(self, method: str, params: Dict[str, Any], msg_id: int) -> Any:
        """
        Single POST round-trip, handling inline JSON or SSE-in-POST.
        If a 404 is returned while we have a session id, we re-init once and retry.
        """
        assert self._session, "HTTP session not ready"

        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        }

        # First attempt
        try:
            resp = await self._post_jsonrpc(payload, proto=self._protocol or self.PROTO_CANDIDATES[0], sid=self._session_id)
            txt = await resp.text()

            if resp.status == 404 and self._session_id:
                raise MCPError("session expired")

            if resp.status != 200:
                raise MCPError(f"{method} HTTP {resp.status}: {txt}")

            data = _parse_json_or_sse(txt)
            if "error" in data:
                raise MCPError(f"{method} error: {data['error']}")
            return data.get("result")

        except MCPError as e:
            # Re-initialize once on session expiry
            if "session expired" in str(e).lower():
                proto, sid = await self._initialize_negotiate()
                self._protocol = proto
                self._session_id = sid
                await self._notify_initialized()

                # Retry once
                resp = await self._post_jsonrpc(payload, proto=self._protocol, sid=self._session_id)
                txt = await resp.text()
                if resp.status != 200:
                    raise MCPError(f"{method} HTTP {resp.status}: {txt}")
                data = _parse_json_or_sse(txt)
                if "error" in data:
                    raise MCPError(f"{method} error: {data['error']}")
                return data.get("result")
            raise

    # ---------------- HTTP ----------------

    async def _post_jsonrpc(
        self,
        payload: Dict[str, Any],
        proto: str,
        sid: Optional[str],
    ) -> aiohttp.ClientResponse:
        """
        POST helper that always sends spec-compliant headers:
          - Accept: application/json, text/event-stream
          - Content-Type: application/json
          - MCP-Protocol-Version: <proto>
          - Mcp-Session-Id: <sid> (if present)
        """
        assert self._session, "HTTP session not ready"

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": proto,
        }
        if sid:
            headers["Mcp-Session-Id"] = sid

        return await self._session.post(self.base_url, json=payload, headers=headers)


# ---------------- Demo (MVP) ----------------

async def _demo():
    base = os.environ.get("MCP_BASE", "http://localhost:8931")
    mcp_url = f"{base.rstrip('/')}/mcp"
    async with MCPConnector(mcp_url) as cli:
        print("[OK] initialize", {"protocolVersion": cli._protocol, "sessionId": cli._session_id})

        print("[OK] tools/list:", [t.get("name") for t in cli.available_tools])
        if any(t.get("name") == "browser_tabs" for t in cli.available_tools):
            res = await cli.call_tool("browser_tabs", {"action": "list"})
            print("[OK] browser_tabs:", res)


if __name__ == "__main__":
    asyncio.run(_demo())
