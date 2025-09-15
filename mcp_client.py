# mcp_client.py
import asyncio
import itertools
import json
from typing import Any, Dict, Optional, List, Set, Tuple

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
    JSON-RPC client for @playwright/mcp with:
      - SSE session binding (GET /sse)
      - inline JSON/SSE handling on POST
      - sessionId capture and forwarding (URL/header + params)
      - POST endpoint fallbacks and reuse of the variant that worked
      - verbose debug logs
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
        self.msg_url = self._derive_messages_url(self.base_url)
        self._session_id: Optional[str] = None
        self._last_working_label: Optional[str] = None  # remember which POST variant worked

        self.available_tools: Set[str] = set()

    @staticmethod
    def _derive_sse_url(base_url: str) -> str:
        u = base_url.rstrip("/")
        if u.endswith("/sse"):
            return u
        if u.endswith("/mcp"):
            return u[:-4] + "/sse"
        return u + "/sse"

    @staticmethod
    def _derive_messages_url(base_url: str) -> str:
        u = base_url.rstrip("/")
        if u.endswith("/messages"):
            return u
        if u.endswith("/mcp"):
            return u[:-4] + "/messages"
        return u + "/messages"

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
            # 1) Bind session state via SSE
            print("[MCP] Connecting SSE …")
            self._sse_resp = await self._session.get(
                self.sse_url,
                headers={"Accept": "text/event-stream"},
            )
            if self._sse_resp.status != 200:
                txt = await self._sse_resp.text()
                raise MCPError(f"SSE connect HTTP {self._sse_resp.status}: {txt[:300]}")
            print(f"[MCP] SSE connected: {self._sse_resp.status}; "
                  f"Set-Cookie={self._sse_resp.headers.get('Set-Cookie', '<none>')}")

            self._sse_task = asyncio.create_task(self._sse_reader(), name="mcp_sse_reader")

            # 2) Initialize → list tools (no notifications)
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
        # Capture sessionId from greeting like "/sse?sessionId=UUID"
        for line in msg.splitlines():
            s = line.strip()
            if s.startswith("data:"):
                payload = s[5:].strip()
                if "sessionId=" in payload and self._session_id is None:
                    sid = payload.split("sessionId=", 1)[1]
                    for sep in ("&", " ", "\t", "\r"):
                        sid = sid.split(sep, 1)[0]
                    sid = sid.strip()
                    if sid:
                        self._session_id = sid
                        print(f"[MCP] Captured sessionId={self._session_id}")

        # Now try to parse JSON data:
        data_lines = [ln[5:].strip() for ln in msg.splitlines() if ln.strip().startswith("data:")]
        if not data_lines:
            return
        payload = data_lines[-1]
        try:
            obj = json.loads(payload)
        except Exception:
            # Non-JSON (greeting handled above)
            return

        rpc_id = obj.get("id")
        if isinstance(rpc_id, int) and rpc_id in self._pending:
            fut = self._pending.pop(rpc_id)
            if not fut.done():
                fut.set_result(obj)
        else:
            kind = "error" if "error" in obj else "result" if "result" in obj else "unknown"
            print(f"[MCP] SSE {kind} (no waiter) id={rpc_id}: {str(obj)[:200]}")

    # ---------- HTTP POST mechanics ----------

    def _post_variants(self) -> List[Tuple[str, Dict[str, str], str]]:
        """
        Return (url, extra_headers, label) variants to try in order.
        Prefer reusing last working variant; otherwise:
          1) mcp
          2) messages
          3) mcp?sid
          4) messages?sid
        """
        variants: List[Tuple[str, Dict[str, str], str]] = []

        # Build base candidates
        cand: List[Tuple[str, Dict[str, str], str]] = []

        # 1) /mcp (cookies only)
        cand.append((self.base_url, {}, "mcp"))

        # 2) /messages (cookies only)
        cand.append((self.msg_url, {}, "messages"))

        # 3) /mcp?sessionId=... + header
        if self._session_id:
            url = f"{self.base_url}{'&' if '?' in self.base_url else '?'}sessionId={self._session_id}"
            cand.append((url, {"X-Session-Id": self._session_id}, "mcp?sid"))

        # 4) /messages?sessionId=... + header
        if self._session_id:
            url = f"{self.msg_url}{'&' if '?' in self.msg_url else '?'}sessionId={self._session_id}"
            cand.append((url, {"X-Session-Id": self._session_id}, "messages?sid"))

        # If we know what worked last time, try it first
        if self._last_working_label:
            ordered = [x for x in cand if x[2] == self._last_working_label] + [x for x in cand if x[2] != self._last_working_label]
            return ordered
        return cand

    async def _post_rpc(self, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Try POST variants until one returns 200 with parseable JSON/SSE.
        Return (obj, label) where obj is the parsed JSON-RPC envelope (or None).
        """
        assert self._session is not None
        last_err: Optional[str] = None

        for url, extra_headers, label in self._post_variants():
            async with self._session.post(url, json=payload, headers=extra_headers) as resp:
                text = await resp.text()
                ctype = resp.headers.get("Content-Type", "")
                cookie_hdr = resp.headers.get("Set-Cookie", "")
                print(f"[MCP] POST {label} -> status={resp.status} ctype={ctype or '<none>'} "
                      f"Set-Cookie={cookie_hdr or '<none>'}")
                if resp.status != 200:
                    last_err = f"HTTP {resp.status}: {text[:300]}"
                    continue

                # Inline JSON
                if "application/json" in ctype or text.lstrip().startswith("{"):
                    try:
                        inline_obj = json.loads(text)
                    except Exception:
                        inline_obj = None
                    if inline_obj is not None:
                        return inline_obj, label

                # Inline SSE
                if "text/event-stream" in ctype or text.lstrip().startswith(("event:", "data:")):
                    try:
                        inline_sse_obj = _parse_sse_text_to_json(text)
                    except Exception:
                        inline_sse_obj = None
                    if inline_sse_obj is not None:
                        return inline_sse_obj, label

                last_err = f"Unrecognized 200 response: {text[:200]}"

        raise MCPError(last_err or "All POST variants failed")

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._session:
            raise MCPError("Session not initialized")

        # ---- PATCH: embed sessionId into params for every RPC after we've captured it
        params = dict(params)
        if self._session_id and "sessionId" not in params:
            params["sessionId"] = self._session_id

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

        # POST (with fallbacks)
        obj, label = await self._post_rpc(payload)

        # If inline handled, resolve waiter and return
        if obj is not None and isinstance(obj, dict) and ("result" in obj or "error" in obj):
            self._pending.pop(rpc_id, None)
            if "error" in obj:
                raise MCPError(f"{method} error: {obj['error']}")
            self._last_working_label = label or self._last_working_label
            print(f"[MCP] {method} (inline via {self._last_working_label}) result keys: {list(obj['result'].keys())}")
            return obj["result"]

        # Wait for SSE if not inline
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
        res = await self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "captcha-mcp-client", "version": "0.1.0"},
                "capabilities": {},
            },
        )
        return res

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
