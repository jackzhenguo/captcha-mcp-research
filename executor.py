import random, time
from typing import Dict, List
from registry import AgentState, MCPServer
from mcp_client import MCPConnector


def find_server(servers: List[MCPServer], server_id: str) -> MCPServer:
    for s in servers:
        if s["id"] == server_id:
            return s
    raise KeyError(f"Server {server_id} not found")


def build_actions(url: str, server: MCPServer) -> List[Dict[str, Any]]:
    return [
        {"tool": "web.open", "args": {"url": url, "viewport": {"width": 1280, "height": 800}}},
        {"tool": "web.wait", "args": {"selector": "body", "timeout_ms": 8000}},
        {"tool": "web.text", "args": {"selector": "title"}},
    ]


async def try_visit(conn: MCPConnector, url: str, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for step in actions:
        out[step["tool"]] = await conn.call_tool(step["tool"], step.get("args", {}))
    return out


async def invoke_server_node(state: AgentState) -> AgentState:
    if state["idx"] >= len(state["shortlist"]):
        return {"last_error": "No more candidates."}

    cand = state["shortlist"][state["idx"]]
    srv = find_server(state["servers"], cand["server_id"])
    token = srv.get("auth", {}).get("token") if srv.get("auth", {}).get("type") == "bearer" else None
    conn = MCPConnector(srv["base_url"], token=token, timeout=45)

    try:
        all_results = {}
        for url in state["targets"]:
            actions = build_actions(url, srv)
            res = await try_visit(conn, url, actions)
            all_results[url] = res
        bo = state.get("backoff_until", {}).copy()
        bo.pop(srv["id"], None)
        return {"result": {"server": srv["id"], "outputs": all_results}, "backoff_until": bo}
    except Exception as e:
        wait = 2.0 * (1 + random.random())
        bo = state.get("backoff_until", {}).copy()
        bo[srv["id"]] = time.time() + wait
        return {"last_error": f"{srv['id']} failed: {e}", "backoff_until": bo}


def pick_next_node(state: AgentState) -> AgentState:
    return {"idx": state.get("idx", 0) + 1, "attempts": state.get("attempts", 0) + 1}


def should_continue(state: AgentState) -> str:
    if "result" in state and state["result"]:
        return "done"
    if state.get("attempts", 0) >= state.get("max_attempts", 5):
        return "done"
    if state.get("idx", 0) >= len(state.get("shortlist", [])):
        return "done"
    return "loop"
