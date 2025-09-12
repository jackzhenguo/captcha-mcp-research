from typing import TypedDict, List, Dict, Any, Optional

class MCPTool(TypedDict):
    name: str
    schema: Dict[str, Any]

class MCPServer(TypedDict):
    id: str
    base_url: str
    auth: Optional[Dict[str, Any]]
    tools: List[MCPTool]
    tags: List[str]
    healthy: bool

class AgentState(TypedDict, total=False):
    """
    Shared state passed through the graph.
    total=False makes every field optional so you can add
    new ones like `preferred_region` without breaking callers.
    """
    task: str
    targets: List[str]
    servers: List[MCPServer]
    shortlist: List[Dict[str, Any]]
    idx: int
    attempts: int
    max_attempts: int
    last_error: str
    result: Dict[str, Any]
    backoff_until: Dict[str, Any]
    preferred_region: Optional[str]  # optional region hint
