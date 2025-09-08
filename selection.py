import json, time
from typing import List
from registry import AgentState, MCPServer

SELECTION_SYSTEM = "You select MCP servers for a task. Return JSON only."
SELECTION_USER_TMPL = """Task: {task}
Targets: {targets}

Servers (id, tools, tags, healthy, latency, capabilities.captcha, backoff_until): 
{servers_brief}

Selection rules:
- Strongly prefer servers with good CAPTCHA capability (detect, metadata, handoff, enterprise token). 
  Score: 1.0 = full support; 0.75 = detect+metadata+handoff; 0.5 = detect only; 0.0 = none.
- After CAPTCHA, prefer healthy servers, then lower latency, then region {preferred_region}.
- If info is missing, treat as neutral. Do not consider non-compliant CAPTCHA bypass.

Return JSON ONLY:
{
  "shortlist": [
    {"id": "<id>", "score": <0..1>, "subs": {"captcha": <0..1>, "health": <0|1>, "latency": <0..1>, "region": <0|0.1>}}
  ],
  "decision": {"primary": "<id|null>", "fallbacks": ["<id>", "..."]},
  "rejected": [{"id": "<id>", "reasons": ["no_captcha"|"backed_off"|"other"]}]
}
"""


def brief_servers(servers: List[MCPServer]) -> str:
    short = []
    for s in servers:
        short.append({
            "id": s["id"],
            "tags": s.get("tags", [])[:6],
            "tools": [t["name"] for t in s.get("tools", [])[:6]],
            "healthy": s.get("healthy", True),
        })
    return json.dumps(short, ensure_ascii=False)


# Replace this with a real LLM call (OpenAI, DeepSeek, etc.)
async def llm_chat_fn(system: str, user: str) -> str:
    data = json.loads(user.split("Available MCP servers (truncated fields):\n",1)[1])
    ranked = []
    for s in data:
        score = 0.6 + 0.4 * (1 if any("browser" in t for t in s.get("tags", [])) else 0)
        ranked.append({"server_id": s["id"], "score": score, "reason": "stub"})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return json.dumps(ranked[:5], ensure_ascii=False)


async def select_candidates_node(state: AgentState) -> AgentState:
    prompt = SELECTION_USER_TMPL.format(
        task=state["task"],
        targets=state["targets"],
        servers_brief=brief_servers(state["servers"]),
    )
    raw = await llm_chat_fn(SELECTION_SYSTEM, prompt)
    shortlist = json.loads(raw)
    now = time.time()
    backoff_until = state.get("backoff_until", {})
    filtered = [x for x in shortlist
                if any(s["id"] == x["server_id"] and s.get("healthy", True) for s in state["servers"])
                and (backoff_until.get(x["server_id"], 0) <= now)]
    return {"shortlist": filtered, "idx": 0, "attempts": 0, "max_attempts": max(5, len(filtered)*2)}
