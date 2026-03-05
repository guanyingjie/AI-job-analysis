"""Sports Blog Agent — LangGraph definition

Linear pipeline: search_node -> write_node -> save_node
"""

from langgraph.graph import StateGraph, END
from src.sports.state import SportsState
from src.sports.nodes import search_node, write_node, save_node

builder = StateGraph(SportsState)

builder.add_node("search_node", search_node)
builder.add_node("write_node", write_node)
builder.add_node("save_node", save_node)

builder.set_entry_point("search_node")
builder.add_edge("search_node", "write_node")
builder.add_edge("write_node", "save_node")
builder.add_edge("save_node", END)

graph = builder.compile()
