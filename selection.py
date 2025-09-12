import json, time
from typing import List
from registry import AgentState, MCPServer

SELECTION_SYSTEM = "You select MCP servers for a task. Return JSON only."

SELECTION_USER_TMPL = """Task: {task}
Targets: {targets}

Servers (id, tools, tags, healthy):
{servers_brief}

Selection rules:
- Strongly prefer servers with good CAPTCHA capability (detect, metadata, handoff, enterprise token). 
  Score: 1.0 = full support; 0.75 = detect+metadata+handoff; 0.5 = detect only; 0.0 = none.
- After CAPTCHA, prefer healthy servers, then lower latency, then region {preferred_region}.
- If info is missing, treat as neutral. Do not consider non-compliant CAPTCHA bypass.

Return JSON ONLY:
[
  {{
    "server_id": "<id>",
    "score": <0..1>,
    "reason": "<brief>"
  }}
]
"""

def brief_servers(servers: List[MCPServer]) -> str:
    """Create a short JSON summary of servers for prompt construction."""
    short = []
    for s in servers:
        short.append({
            "id": s["id"],
            "tags": s.get("tags", [])[:6],
            "tools": [t["name"] for t in s.get("tools", [])[:6]],
            "healthy": s.get("healthy", True),
        })
    return json.dumps(short, ensure_ascii=False)

def _extract_servers_array_from_prompt(user: str) -> List[dict]:
    """
    Pull the servers JSON array back out of the user prompt so the stubbed
    llm_chat_fn can work without a real LLM.
    """
    header = "Servers (id, tools, tags, healthy):"
    i = user.find(header)
    if i == -1:
        return []

    j = user.find("[", i)
    if j == -1:
        return []

    depth = 0
    end = -1
    for k, ch in enumerate(user[j:], start=j):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = k
                break
    if end == -1:
        return []

    try:
        arr = json.loads(user[j:end + 1])
        if isinstance(arr, list) and all(isinstance(x, dict) and "id" in x for x in arr):
            return arr
    except Exception:
        pass
    return []

# Stub “LLM” ranking — ranks by presence of a browser tag
async def llm_chat_fn(system: str, user: str) -> str:
    servers = _extract_servers_array_from_prompt(user)
    ranked = []
    for s in servers:
        has_browser_tag = any("browser" in t for t in s.get("tags", []))
        score = 0.6 + 0.4 * (1 if has_browser_tag else 0)
        ranked.append({"server_id": s["id"], "score": round(score, 3), "reason": "stub"})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return json.dumps(ranked[:5], ensure_ascii=False)

async def select_candidates_node(state: AgentState) -> AgentState:
    """
    Select candidate servers based on the task, targets, and simple heuristics.
    """
    preferred_region = state.get("preferred_region") or "auto"
    if preferred_region == "auto":
        for s in state.get("servers", []):
            for t in s.get("tags", []):
                if t.startswith("region:"):
                    preferred_region = t.split(":", 1)[1]
                    break
            if preferred_region != "auto":
                break

    prompt = SELECTION_USER_TMPL.format(
        task=state["task"],
        targets=state["targets"],
        servers_brief=brief_servers(state["servers"]),
        preferred_region=preferred_region,
    )

    raw = await llm_chat_fn(SELECTION_SYSTEM, prompt)
    try:
        shortlist = json.loads(raw)
    except Exception:
        shortlist = []

    # Fallbacks to ensure we always return at least one candidate
    if not shortlist:
        shortlist = [
            {"server_id": s["id"], "score": 0.7, "reason": "no-llm"}
            for s in state.get("servers", [])
        ]

    now = time.time()
    backoff_until = state.get("backoff_until", {})
    filtered = [
        x
        for x in shortlist
        if any(s["id"] == x["server_id"] and s.get("healthy", True) for s in state["servers"])
        and (backoff_until.get(x["server_id"], 0) <= now)
    ]
    if not filtered:
        filtered = [
            {"server_id": s["id"], "score": 0.7, "reason": "fallback"}
            for s in state.get("servers", [])
            if s.get("healthy", True)
        ]

    return {
        "shortlist": filtered,
        "idx": 0,
        "attempts": 0,
        "max_attempts": max(5, len(filtered) * 2),
    }
