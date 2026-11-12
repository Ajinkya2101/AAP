"""The agent graph (plan section 8).

No vendor email in this build: an invoice the supplier must fix (invoice maths, missing
PO number) goes to a person with the reason and a suggested action.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes.decide import decide_fields
from .nodes.finalize import finalize
from .nodes.gl import gl_code
from .nodes.matching import find_po, hold, pair_lines, three_way
from .nodes.triage import triage
from .state import AgentState

GRAPH_VERSION = "4"   # 4: decide (adapter B) after gl_code; 3: several POs per invoice, blanket releases


def _after_triage(state: AgentState) -> str:
    return {"review": "finalize", "po": "find_po", "non_po": "gl_code"}[state["route"]]


def _after_find_po(state: AgentState) -> str:
    return "pair_lines" if state.get("po_number") else "finalize"


def _after_pair(state: AgentState) -> str:
    return "three_way" if state.get("pairs") else "finalize"


def _after_three_way(state: AgentState) -> str:
    return "hold" if state.get("variances") else "gl_code"


def build(checkpointer=None):  # noqa: ANN001
    g = StateGraph(AgentState)
    for name, fn in [("triage", triage), ("find_po", find_po), ("pair_lines", pair_lines), ("three_way", three_way),
                     ("hold", hold), ("gl_code", gl_code), ("decide", decide_fields), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", _after_triage, ["finalize", "find_po", "gl_code"])
    g.add_conditional_edges("find_po", _after_find_po, ["pair_lines", "finalize"])
    g.add_conditional_edges("pair_lines", _after_pair, ["three_way", "finalize"])
    g.add_conditional_edges("three_way", _after_three_way, ["hold", "gl_code"])
    g.add_edge("hold", "finalize")
    g.add_edge("gl_code", "decide")
    g.add_edge("decide", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
