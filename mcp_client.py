import aiohttp
from typing import Any, Dict, Optional


class MCPConnector:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: int = 45):
        self.base_url, self.token, self.timeout = base_url, token, timeout

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout) as r:
                r.raise_for_status()
                return await r.json()

    async def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post("/tool.call", {"name": name, "arguments": args})
