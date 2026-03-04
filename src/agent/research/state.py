"""Subgraph shared state + builder

All three research subgraphs (macro / job_market / tech_frontier) share the same
SubgraphState and build_research_subgraph() builder. At runtime they are
differentiated by the queries and max_searches passed in.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.documents import Document
from langgraph.graph import StateGraph, END

from src.agent.tools import search_web, google_search, read_page, download_pdf, search_report_summary

logger = logging.getLogger("agent.subgraph")

# Number of top URLs to read per search query
TOP_URLS_PER_SEARCH = 4


# ─────────────────────────────────────────────
# Reducer
# ─────────────────────────────────────────────

def append_docs(existing: list[Document] | None, new: list[Document] | None) -> list[Document]:
    """Simple append reducer for subgraph-internal documents.
    The main graph's reduce_docs handles deduplication."""
    return (existing or []) + (new or [])


# ─────────────────────────────────────────────
# Subgraph State
# ─────────────────────────────────────────────

@dataclass(kw_only=True)
class SubgraphState:
    """Internal state shared by all three research subgraphs"""
    queries: list[str] = field(default_factory=list)       # Search queries for this dimension
    step_index: int = 0                                     # Current query index
    max_searches: int = 8                                   # Tavily call budget for this subgraph
    tavily_call_count: int = 0                              # Tavily calls used so far
    documents: Annotated[list[Document], append_docs] = field(default_factory=list)


# ─────────────────────────────────────────────
# Subgraph Nodes & Routing
# ─────────────────────────────────────────────

def _is_pdf_url(url: str) -> bool:
    """Check if a URL points to a PDF document"""
    lower = url.lower().split("?")[0].split("#")[0]
    return lower.endswith(".pdf")


def _dedupe_results(tavily_results: list[dict], serper_results: list[dict]) -> list[dict]:
    """Merge and deduplicate search results from Tavily and Serper by URL.
    Tavily results come first (higher priority), then unique Serper results."""
    seen_urls = set()
    merged = []
    for item in tavily_results + serper_results:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(item)
    return merged


async def search_and_read(state: SubgraphState) -> dict:
    """Execute one search + read cycle, appending results to documents.
    Uses both Tavily and Google/Serper for broader coverage.
    Reads top 4 unique URLs. Detects PDFs and uses PDF parser."""
    if (
        state.step_index >= len(state.queries)
        or state.tavily_call_count >= state.max_searches
    ):
        return {}

    query = state.queries[state.step_index]
    logger.info("🔍 Subgraph search [%d/%d]: %s", state.step_index + 1, len(state.queries), query)

    tavily_calls_this_step = 0

    # 1. Tavily search (counts toward budget)
    search_result_str = await search_web.ainvoke({"query": query})
    search_result = json.loads(search_result_str)

    if search_result.get("error"):
        logger.warning("⚠️ Tavily search failed: %s", search_result["error"])
        tavily_results = []
    else:
        tavily_calls_this_step += 1
        tavily_results = search_result.get("results", [])

    # 2. Google/Serper search (bonus, doesn't count toward Tavily budget)
    serper_results = []
    try:
        serper_str = await google_search.ainvoke({"query": query})
        serper_result = json.loads(serper_str)
        if not serper_result.get("error"):
            serper_results = serper_result.get("results", [])
            logger.info("🔎 Serper returned %d additional results", len(serper_results))
    except Exception as e:
        logger.debug("Serper search skipped: %s", e)

    # 3. Merge and deduplicate results
    merged_results = _dedupe_results(tavily_results, serper_results)
    logger.info("📋 Merged %d unique URLs (Tavily: %d, Serper: %d)",
                len(merged_results), len(tavily_results), len(serper_results))

    # 4. Read top N URLs, with PDF detection
    new_docs = []
    for item in merged_results[:TOP_URLS_PER_SEARCH]:
        url = item.get("url", "")
        if not url:
            continue

        try:
            if _is_pdf_url(url):
                # Use PDF parser for PDF URLs
                logger.info("📄 Downloading PDF: %s", url[:80])
                page_str = await download_pdf.ainvoke({"url": url})
            else:
                page_str = await read_page.ainvoke({"url": url})

            page_result = json.loads(page_str)

            if page_result.get("status") == "ok":
                new_docs.append(Document(
                    page_content=page_result["content"],
                    metadata={"source": url, "title": item.get("title", "")},
                ))
            elif page_result.get("status") in {"paywalled", "forbidden", "timeout"}:
                # Budget guard: skip fallback if Tavily budget is exhausted
                if state.tavily_call_count + tavily_calls_this_step >= state.max_searches:
                    continue
                # Fallback: search for public summaries (Tavily call)
                report_name = item.get("title") or query
                logger.info("📖 Paywall fallback, searching summary: %s", report_name[:60])
                fallback_str = await search_report_summary.ainvoke({"report_name": report_name})
                tavily_calls_this_step += 1
                fallback_result = json.loads(fallback_str)
                for r in fallback_result.get("results", [])[:2]:
                    new_docs.append(Document(
                        page_content=r.get("snippet", ""),
                        metadata={"source": r.get("url", ""), "title": r.get("title", "")},
                    ))
        except Exception as e:
            logger.warning("⚠️ Failed to read %s: %s", url[:60], e)
            continue

    logger.info("📄 Step complete: %d docs collected, %d Tavily calls this step",
                len(new_docs), tavily_calls_this_step)
    return {
        "documents": new_docs,
        "step_index": state.step_index + 1,
        "tavily_call_count": state.tavily_call_count + tavily_calls_this_step,
    }


def has_more_queries(state: SubgraphState) -> str:
    """Check if there are more queries to execute"""
    if state.step_index < len(state.queries) and state.tavily_call_count < state.max_searches:
        return "continue"
    return "done"


def check_has_queries(state: SubgraphState) -> str:
    """Entry check: skip directly to END if no queries or zero budget."""
    if state.step_index < len(state.queries) and state.max_searches > 0:
        return "run"
    return "done"


def route_entry(state: SubgraphState) -> dict:
    """Empty entry node for conditional routing."""
    return {}


# ─────────────────────────────────────────────
# Subgraph Builder
# ─────────────────────────────────────────────

def build_research_subgraph():
    """Build and compile a research subgraph. max_searches passed via input state."""
    builder = StateGraph(SubgraphState)
    builder.add_node("route_entry", route_entry)
    builder.add_node("search_and_read", search_and_read)
    builder.set_entry_point("route_entry")
    builder.add_conditional_edges("route_entry", check_has_queries, {
        "run": "search_and_read",
        "done": END,
    })
    builder.add_conditional_edges("search_and_read", has_more_queries, {
        "continue": "search_and_read",
        "done": END,
    })
    return builder.compile()
