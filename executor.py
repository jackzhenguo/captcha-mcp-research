import random, time, json, re
from typing import Dict, List, Any, Set, Optional, Tuple
from registry import AgentState, MCPServer
from mcp_client import MCPConnector  # your class from mcp_client.py

PROTOCOL_VERSION = "2024-11-05"  # kept for reference if you later expose it in MCPConnector
INIT_TIMEOUT = 60
CALL_TIMEOUT = 45


def find_server(servers: List[MCPServer], server_id: str) -> MCPServer:
    for s in servers:
        if s["id"] == server_id:
            return s
    raise KeyError(f"Server {server_id} not found")


def _pick(tools: Set[str], *candidates: str) -> Optional[str]:
    for name in candidates:
        if name in tools:
            return name
    return None


# ----------------- Snapshot parsing helpers -----------------
# Example YAML line from Playwright snapshot text:
# - button "Verify Now By Zhen" [ref=e4]
_YAML_BUTTON_LINE = re.compile(
    r'-\s*button\s+"(?P<label>[^"]+)"\s+\[ref=(?P<ref>[a-zA-Z0-9_:-]+)\]'
)

def _extract_button_ref_from_yaml_text(snapshot_text: str, label: str) -> Optional[str]:
    """
    From the Playwright 'Page Snapshot' YAML block embedded in markdown,
    find:  - button "LABEL" [ref=e4]  and return 'e4'.
    """
    if not snapshot_text:
        return None
    m = re.search(r"```yaml(.*?)```", snapshot_text, flags=re.S)
    if not m:
        return None
    yaml_block = m.group(1)
    for line in yaml_block.splitlines():
        line = line.strip()
        mm = _YAML_BUTTON_LINE.match(line)
        if mm and mm.group("label") == label:
            return mm.group("ref")
    return None


def _snapshot_root_from_tool_result(res: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Return (json_root_or_None, raw_text_or_None).
    Supports:
      1) {"content":[{"type":"text","text":"..."}]} where text may contain JSON or markdown+YAML
      2) {"data": {...}}
    """
    if not isinstance(res, dict):
        return None, None

    raw_text = None
    content = res.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict) and "text" in content[0]:
        raw_text = content[0]["text"]

        # Try plain JSON
        try:
            maybe = json.loads(raw_text)
            if isinstance(maybe, dict):
                return maybe, raw_text
        except Exception:
            pass

        # Try fenced ```json ... ```
        m = re.search(r"```json(.*?)```", raw_text or "", flags=re.S)
        if m:
            try:
                maybe = json.loads(m.group(1))
                if isinstance(maybe, dict):
                    return maybe, raw_text
            except Exception:
                pass

    data = res.get("data")
    if isinstance(data, dict):
        return data, raw_text

    return None, raw_text


def _traverse(snapshot: Dict[str, Any]):
    yield snapshot
    for child in snapshot.get("children", []) or []:
        yield from _traverse(child)


def find_ref_by_id(snapshot_root: Dict[str, Any], target_id: str) -> Optional[str]:
    for node in _traverse(snapshot_root):
        attrs = node.get("attributes") or {}
        if attrs.get("id") == target_id and "ref" in node:
            return node["ref"]
    return None


def find_ref_by_name_and_role(snapshot_root: Dict[str, Any], name: str, role: Optional[str] = None) -> Optional[str]:
    for node in _traverse(snapshot_root):
        if node.get("name") == name and "ref" in node:
            if role is None or node.get("role") == role:
                return node["ref"]
    return None


def extract_text_by_id(snapshot_root: Dict[str, Any], target_id: str) -> Optional[str]:
    for node in _traverse(snapshot_root):
        attrs = node.get("attributes") or {}
        if attrs.get("id") == target_id:
            for k in ("name", "value", "description", "text"):
                v = node.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""  # Exists but no text
    return None


def parse_verdict_text(verdict: Optional[str]) -> Dict[str, Any]:
    if not verdict:
        return {"success": None, "raw": verdict}
    low = verdict.lower()
    if "pass" in low:
        return {"success": True, "raw": verdict}
    if "fail" in low or "error" in low:
        return {"success": False, "raw": verdict}
    return {"success": None, "raw": verdict}


# ----------------- Build actions (navigate -> wait -> snapshot) -----------------
def build_actions(url: str, tool_names: Set[str]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []

    open_tool = _pick(tool_names, "browser_tab_new")
    nav_tool = _pick(tool_names, "browser_navigate")
    if open_tool:
        actions.append({"tool": open_tool, "args": {"url": url}})
    elif nav_tool:
        actions.append({"tool": nav_tool, "args": {"url": url}})
    else:
        raise RuntimeError("No open/navigate tool found")

    wait_tool = _pick(tool_names, "browser_wait", "browser_wait_for")
    if wait_tool:
        # If you have browser_wait_for(selector/timeout), switch args accordingly.
        actions.append({"tool": wait_tool, "args": {"time": 1.5}})

    snap_tool = _pick(tool_names, "browser_snapshot")
    if snap_tool:
        actions.append({"tool": snap_tool, "args": {}})

    return actions


# ----------------- Visit + interact by snapshot refs -----------------
async def try_visit(conn: MCPConnector, url: str, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    # Convert available_tools (list of dicts) -> set of names
    tool_names: Set[str] = {
        t.get("name") for t in (conn.available_tools or [])
        if isinstance(t, dict) and "name" in t
    }

    wait_tool = _pick(tool_names, "browser_wait", "browser_wait_for")
    snap_tool = _pick(tool_names, "browser_snapshot")
    click_tool = _pick(tool_names, "browser_click")
    key_tool = _pick(tool_names, "browser_press_key")

    # Run initial sequence
    for step in actions:
        out[step["tool"]] = await conn.call_tool(step["tool"], step.get("args", {}))

    # First snapshot root
    first_snap_res = out.get(snap_tool or "", None)
    root, raw_text = _snapshot_root_from_tool_result(first_snap_res)

    # Retry to allow page scripts to settle
    retries = 2
    while (not isinstance(root, dict) and raw_text is None) and retries > 0:
        if wait_tool:
            await conn.call_tool(wait_tool, {"time": 1.0})
        if snap_tool:
            first_snap_res = await conn.call_tool(snap_tool, {})
            out[snap_tool + f"#retry{3 - retries}"] = first_snap_res
            root, raw_text = _snapshot_root_from_tool_result(first_snap_res)
        retries -= 1

    if root is None and raw_text is None:
        return out  # no snapshot parsed

    # Locate the button (JSON first, then YAML fallback)
    btn_ref = None
    if isinstance(root, dict):
        btn_ref = find_ref_by_id(root, "verifyBtn") or \
                  find_ref_by_name_and_role(root, "Verify Now By Zhen", role="button")
    if not btn_ref and isinstance(raw_text, str):
        btn_ref = _extract_button_ref_from_yaml_text(raw_text, "Verify Now By Zhen")

    # Click (ref required by server)
    clicked = False
    if click_tool and btn_ref:
        try:
            out[click_tool] = await conn.call_tool(click_tool, {
                "element": "Verify button (text=Verify Now By Zhen)",
                "ref": btn_ref
            })
            clicked = True
        except Exception:
            pass

    # Page tip says: press V or Enter
    if not clicked and key_tool:
        for key in ("V", "Enter"):
            try:
                out[key_tool + f"#{key}"] = await conn.call_tool(key_tool, {"key": key})
                clicked = True
                break
            except Exception:
                continue

    if not clicked:
        return out  # no way to trigger

    # Allow time for verification + DOM update
    if wait_tool:
        out[wait_tool] = await conn.call_tool(wait_tool, {"time": 2.0})

    # Read verdict
    verdict_text: Optional[str] = None
    if snap_tool:
        second_snap = await conn.call_tool(snap_tool, {})
        out[snap_tool + "#2"] = second_snap
        root2, raw_text2 = _snapshot_root_from_tool_result(second_snap)

        v_retries = 2
        while v_retries > 0:
            if isinstance(root2, dict):
                verdict_text = extract_text_by_id(root2, "verdict")
            if verdict_text not in (None, ""):
                break

            if wait_tool:
                await conn.call_tool(wait_tool, {"time": 1.0})
            second_snap = await conn.call_tool(snap_tool, {})
            out[snap_tool + f"#2.retry{3 - v_retries}"] = second_snap
            root2, raw_text2 = _snapshot_root_from_tool_result(second_snap)
            v_retries -= 1

    if verdict_text is not None:
        out["_verdict"] = verdict_text
        out["_verdict_struct"] = parse_verdict_text(verdict_text)

    return out


async def invoke_server_node(state: AgentState) -> AgentState:
    # 1) End if no more candidates
    if state.get("idx", 0) >= len(state.get("shortlist", [])):
        return {"last_error": "No more candidates."}

    cand = state["shortlist"][state["idx"]]
    srv = find_server(state["servers"], cand["server_id"])

    # 2) Per-server backoff check
    backoff = (state.get("backoff_until") or {}).copy()
    until = backoff.get(srv["id"])
    now = time.time()
    if until and now < until:
        return {"last_error": f"{srv['id']} backoff in effect", "backoff_until": backoff}

    try:
        # 3) Connect — your MCPConnector does init+tools inside __aenter__
        async with MCPConnector(srv["base_url"], timeout=INIT_TIMEOUT) as conn:
            # available_tools is already populated (list of dicts)
            tools = conn.available_tools or []
            tool_names: Set[str] = {
                t.get("name") for t in tools
                if isinstance(t, dict) and "name" in t
            }

            # Optional: keep for observability
            srv["tools"] = tools

            if not tool_names:
                # Configuration/session problem — don't back off
                return {
                    "last_error": f"{srv['id']} has no tools after initialize",
                    "backoff_until": backoff
                }

            # 4) Visit each target using discovered tools
            all_results = {}
            for url in state["targets"]:
                actions = build_actions(url, tool_names)
                res = await try_visit(conn, url, actions)
                all_results[url] = res

        # 5) Success: clear backoff for this server
        backoff.pop(srv["id"], None)
        return {
            "result": {"server": srv["id"], "outputs": all_results},
            "backoff_until": backoff
        }

    except Exception as e:
        # 6) Failure: short randomized backoff
        wait = 2.0 * (1 + random.random())
        backoff = (state.get("backoff_until") or {}).copy()
        backoff[srv["id"]] = time.time() + wait
        return {"last_error": f"{srv['id']} failed: {e}", "backoff_until": backoff}


def pick_next_node(state: AgentState) -> AgentState:
    return {"idx": state.get("idx", 0) + 1, "attempts": state.get("attempts", 0) + 1}


def should_continue(state: AgentState) -> str:
    if state.get("result"):
        return "done"
    if state.get("attempts", 0) >= state.get("max_attempts", 5):
        return "done"
    if state.get("idx", 0) >= len(state.get("shortlist", [])):
        return "done"
    return "loop"
