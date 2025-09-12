# graph.py
from langgraph.graph import StateGraph, END
from registry import AgentState
from selection import select_candidates_node
from executor import invoke_server_node, pick_next_node, should_continue

def _wrap_async(fn):
    async def _inner(state: AgentState) -> AgentState:
        return await fn(state)
    return _inner

def build_app():
    graph = StateGraph(AgentState)

    graph.add_node("select", _wrap_async(select_candidates_node))
    graph.add_node("invoke", _wrap_async(invoke_server_node))
    graph.add_node("pick_next", pick_next_node)

    graph.set_entry_point("select")
    graph.add_edge("select", "invoke")
    graph.add_conditional_edges(
        "invoke",
        should_continue,
        {"done": END, "loop": "pick_next"},
    )
    graph.add_edge("pick_next", "invoke")

    return graph.compile()
