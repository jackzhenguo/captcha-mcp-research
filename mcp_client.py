# mcp_client.py
import asyncio
import itertools
import json
from typing import Any, Dict, Optional, List, Set

import aiohttp


class MCPError(RuntimeError):
    pass


def _parse_sse_text_to_json(text: str) -> dict:
    """Parse an SSE payload text into the last 'data:' JSON object."""
    data = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
    if not data:
        raise ValueError("No data: line in SSE body")
    return json.loads(data)


class MCPConnector:
    """
    JSON-RPC client for @playwright/mcp with SSE support + session id forwarding.
    """

    def __init__(self, base_url: str, token: Optional[str] = None, timeout: int = 45):
        # Expected base_url: http://localhost:8931/mcp
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

        self._id = itertools.count(1)
        self._session: Optional[aiohttp.ClientSession] = None
        self._sse_resp: Optional[aiohttp.ClientResponse] = None
        self._sse_task: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}

        self.sse_url = self._derive_sse_url(self.base_url)
        self._session_id: Optional[str] = None  # <-- captured from SSE greeting

        self.available_tools: Set[str] = set()

    @staticmethod
    def _derive_sse_url(base_url: str) -> str:
        u = base_url.rstrip("/")
        if u.endswith("/sse"):
            return u
        if u.endswith("/mcp"):
            return u[:-4] + "/sse"  # strip 'mcp' and add '/sse'
        return u + "/sse"

    async def __aenter__(self):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        print(f"[MCP] Opening session to {self.base_url} with SSE {self.sse_url}")
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        )

        try:
            # 1) Bind session state by connecting SSE first
            print("[MCP] Connecting SSE …")
            self._sse_resp = await self._session.get(
                self.sse_url,
                headers={"Accept": "text/event-stream"},
            )
            if self._sse_resp.status != 200:
                txt = await self._sse_resp.text()
                raise MCPError(f"SSE connect HTTP {self._sse_resp.status}: {txt[:300]}")

            self._sse_task = asyncio.create_task(self._sse_reader(), name="mcp_sse_reader")

            # 2) Initialize and list tools (now bound to this SSE session)
            print("[MCP] Initializing …")
            await self.initialize()
            print("[MCP] Listing tools …")
            await self.list_tools()
            print(f"[MCP] Tools ready: {sorted(self.available_tools)}")
            return self

        except Exception as e:
            print(f"[MCP] Initialization failed: {e}")
            await self._shutdown()
            raise

    async def __aexit__(self, exc_type, exc, tb):
        print("[MCP] Closing session")
        await self._shutdown()

    async def _shutdown(self):
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._sse_task = None

        if self._sse_resp:
            try:
                await self._sse_resp.release()
            except Exception:
                pass
            self._sse_resp = None

        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(MCPError("Connection closed"))
        self._pending.clear()

        if self._session:
            await self._session.close()
            self._session = None

    async def _sse_reader(self):
        """Read and dispatch SSE events coming from GET /sse."""
        assert self._sse_resp is not None
        print("[MCP] SSE reader started")
        buffer: List[str] = []

        async for raw_line in self._sse_resp.content:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                if buffer:
                    await self._process_sse_message("\n".join(buffer))
                    buffer.clear()
                continue
            buffer.append(line)
        print("[MCP] SSE reader ended")

    async def _process_sse_message(self, msg: str):
        # capture sessionId from any non-JSON 'data:' greeting like "/sse?sessionId=..."
        for line in msg.splitlines():
            s = line.strip()
            if s.startswith("data:"):
                payload = s[5:].strip()
                if "sessionId=" in payload and self._session_id is None:
                    # e.g., "/sse?sessionId=UUID"
                    sid = payload.split("sessionId=", 1)[1].strip()
                    sid = sid.split("&", 1)[0]
                    sid = sid.split(" ", 1)[0]
                    if sid:
                        self._session_id = sid
                        print(f"[MCP] Captured sessionId={self._session_id}")

        # Extract last JSON data line (if any)
        data_lines = [ln[5:].strip() for ln in msg.splitlines() if ln.strip().startswith("data:")]
        if not data_lines:
            return

        payload = data_lines[-1]
        try:
            obj = json.loads(payload)
        except Exception:
            # Not JSON (likely the greeting we already handled)
            return

        rpc_id = obj.get("id")
        if isinstance(rpc_id, int) and rpc_id in self._pending:
            fut = self._pending.pop(rpc_id)
            if not fut.done():
                fut.set_result(obj)
        else:
            kind = "error" if "error" in obj else "result" if "result" in obj else "unknown"
            print(f"[MCP] SSE {kind} (no waiter) id={rpc_id}: {str(obj)[:200]}")

    def _post_url_and_headers(self) -> (str, Dict[str, str]):
        """Build POST URL + extra headers, forwarding sessionId when known."""
        url = self.base_url
        extra_headers: Dict[str, str] = {}
        if self._session_id:
            sep = "&" if ("?" in url) else "?"
            url = f"{url}{sep}sessionId={self._session_id}"
            extra_headers["X-Session-Id"] = self._session_id  # belt & suspenders
        return url, extra_headers

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._session:
            raise MCPError("Session not initialized")

        rpc_id = next(self._id)
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        }
        print(f"[MCP] → {method} (id={rpc_id}) params={params}")

        # waiter for SSE-delivered result
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[rpc_id] = fut

        post_url, extra_headers = self._post_url_and_headers()
        async with self._session.post(post_url, json=payload, headers=extra_headers) as resp:
            text = await resp.text()
            ctype = resp.headers.get("Content-Type", "")
            print(f"[MCP] ← {method} POST status={resp.status} ctype={ctype or '<none>'}")

            if resp.status != 200:
                self._pending.pop(rpc_id, None)
                raise MCPError(f"{method} HTTP {resp.status}: {text[:300]}")

            # Inline JSON
            if "application/json" in ctype or text.lstrip().startswith("{"):
                try:
                    inline_obj = json.loads(text)
                except Exception:
                    inline_obj = None
                if inline_obj is not None:
                    self._pending.pop(rpc_id, None)
                    if "error" in inline_obj:
                        raise MCPError(f"{method} error: {inline_obj['error']}")
                    if "result" not in inline_obj:
                        raise MCPError(f"{method} missing result: {inline_obj}")
                    print(f"[MCP] {method} (inline-json) result keys: {list(inline_obj['result'].keys())}")
                    return inline_obj["result"]

            # Inline SSE (text/event-stream on POST)
            if "text/event-stream" in ctype or text.lstrip().startswith(("event:", "data:")):
                try:
                    inline_sse_obj = _parse_sse_text_to_json(text)
                except Exception:
                    inline_sse_obj = None
                if inline_sse_obj is not None:
                    self._pending.pop(rpc_id, None)
                    if "error" in inline_sse_obj:
                        raise MCPError(f"{method} error: {inline_sse_obj['error']}")
                    if "result" not in inline_sse_obj:
                        raise MCPError(f"{method} missing result: {inline_sse_obj}")
                    print(f"[MCP] {method} (inline-sse) result keys: {list(inline_sse_obj['result'].keys())}")
                    return inline_sse_obj["result"]

        # Wait for SSE reader to deliver the result
        try:
            obj = await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rpc_id, None)
            raise MCPError(f"{method} timed out waiting for SSE response")

        if "error" in obj:
            raise MCPError(f"{method} error: {obj['error']}")
        if "result" not in obj:
            raise MCPError(f"{method} missing result: {obj}")
        print(f"[MCP] {method} (sse) result keys: {list(obj['result'].keys())}")
        return obj["result"]

    # ---------- Public RPCs ----------

    async def initialize(self) -> Dict[str, Any]:
        return await self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "captcha-mcp-client", "version": "0.1.0"},
                "capabilities": {},
            },
        )

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._rpc("tools/list", {})
        tools = result.get("tools", []) or []
        self.available_tools = {t.get("name") for t in tools if t.get("name")}
        print(f"[MCP] Available tools: {sorted(self.available_tools)}")
        return tools

    async def call_tool(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        print(f"[MCP] Calling tool: {name} args={args or {}}")
        result = await self._rpc("tools/call", {"name": name, "arguments": args or {}})
        print(f"[MCP] Tool {name} returned keys: {list(result.keys())}")
        return result
