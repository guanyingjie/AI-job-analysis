"""LangGraph linear pipeline for the Koshien school agent."""

from __future__ import annotations

from langgraph.graph import StateGraph

from src.koshien.nodes import (
    extract_modules,
    fetch_all_sources,
    resolve_school,
    save_document,
    write_document,
)
from src.koshien.state import KoshienState


def build_graph() -> StateGraph:
    """Construct and compile the Koshien pipeline graph."""
    builder = StateGraph(KoshienState)

    builder.add_node("resolve_school", resolve_school)
    builder.add_node("fetch_all_sources", fetch_all_sources)
    builder.add_node("extract_modules", extract_modules)
    builder.add_node("write_document", write_document)
    builder.add_node("save_document", save_document)

    builder.set_entry_point("resolve_school")
    builder.add_edge("resolve_school", "fetch_all_sources")
    builder.add_edge("fetch_all_sources", "extract_modules")
    builder.add_edge("extract_modules", "write_document")
    builder.add_edge("write_document", "save_document")

    return builder.compile()
