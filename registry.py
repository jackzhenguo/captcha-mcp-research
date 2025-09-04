from typing import Any, Dict, List, TypedDict


class MCPTool(TypedDict):
    name: str
    schema: Dict[str, Any]


class MCPServer(TypedDict):
    id: str
    base_url: str
    auth: Dict[str, Any] | None
    tools: List[MCPTool]
    tags: List[str]
    healthy: bool


class AgentState(TypedDict, total=False):
    task: str
    targets: List[str]
    servers: List[MCPServer]
    shortlist: List[Dict[str, Any]]
    idx: int
    last_error: str
    result: Dict[str, Any]
    attempts: int
    max_attempts: int
    backoff_until: Dict[str, float]
