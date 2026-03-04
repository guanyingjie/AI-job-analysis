"""M3 Node Functions

Linear pipeline: create_research_plan → dispatch_to_subgraphs → research_executor
→ summarize_findings → format_output_with_retry
"""

import asyncio
import json
import logging
from datetime import date
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage
from langchain_core.documents import Document
from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import get_settings
from src.agent.state import AgentState
from src.agent.tools import tools
from src.agent.prompts import SYSTEM_PROMPT, PLANNING_PROMPT, FORMAT_PROMPT
from src.agent.types import ResearchPlan
from src.agent.models import JobTrendReport
from src.agent.research.defaults import (
    get_default_macro_queries, get_default_job_market_queries, get_default_tech_queries,
)
from src.agent.research.macro_research import build_research_subgraph as build_macro
from src.agent.research.job_market_research import build_research_subgraph as build_job_market
from src.agent.research.tech_frontier_research import build_research_subgraph as build_tech

logger = logging.getLogger("agent")

# Compile three subgraphs at module level (only once)
macro_subgraph = build_macro()
job_market_subgraph = build_job_market()
tech_subgraph = build_tech()

MAX_FORMAT_RETRIES = 3


# ─────────────────────────────────────────────
# Research Plan Generation (from M2, enhanced)
# ─────────────────────────────────────────────

async def create_research_plan(state: AgentState, config: RunnableConfig) -> dict:
    """Generate research plan and write to state.plan_steps"""
    settings = get_settings()
    logger.info("📋 Generating research plan (max %d steps)", state.max_searches)

    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    ).with_structured_output(ResearchPlan)

    # ⭐ Inject current date into planning prompt
    today = date.today()
    planning_content = PLANNING_PROMPT.format(
        max_searches=state.max_searches,
        today=today.isoformat(),
        year=today.year,
        month=today.month,
        year_month=today.strftime("%Y-%m"),
    )

    # ⭐ Exponential backoff retry
    max_retries = 3
    for attempt in range(max_retries):
        try:
            plan = await model.ainvoke([
                {"role": "system", "content": planning_content},
                *state.messages,
            ])

            steps = plan["steps"]
            for i, step in enumerate(steps):
                logger.info("  📌 Step %d: [%s] %s", i + 1, step["dimension"], step["query"])

            return {
                "plan_steps": steps,
                "step_index": 0,
                "current_step": None,
            }
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error("❌ Research plan generation failed (retried %d times): %s", max_retries, e)
                return {
                    "plan_steps": [],
                    "step_index": 0,
                    "current_step": None,
                }
            wait_time = 2 ** attempt
            logger.warning("⚠️ Plan generation failed (attempt %d), retrying in %ds: %s", attempt + 1, wait_time, e)
            await asyncio.sleep(wait_time)

    return {"plan_steps": [], "step_index": 0, "current_step": None}


# ─────────────────────────────────────────────
# M3: Dispatch Node
# ─────────────────────────────────────────────

def dispatch_to_subgraphs(state: AgentState) -> dict:
    """Dispatch plan steps to dimension-specific query lists"""
    macro_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "macro"]
    job_market_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "job_market"]
    tech_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "tech_frontier"]

    logger.info("📦 Dispatching queries: macro=%d, job_market=%d, tech=%d",
                len(macro_steps), len(job_market_steps), len(tech_steps))

    return {
        "macro_queries": macro_steps or get_default_macro_queries(),
        "job_market_queries": job_market_steps or get_default_job_market_queries(),
        "tech_queries": tech_steps or get_default_tech_queries(),
    }


# ─────────────────────────────────────────────
# M3: Subgraph Executor
# ─────────────────────────────────────────────

async def research_executor(state: AgentState) -> dict:
    """
    Execute three research subgraphs sequentially, collecting all documents.

    Budget allocation for 20 total searches:
    - Macro reports: up to 8
    - Job market: up to 8
    - Tech frontier: up to 4
    """
    all_documents: list[Document] = []
    current_search_count = state.search_count
    remaining_budget = max(state.max_searches - current_search_count, 0)

    # Subgraph 1: Macro Reports (up to 8, constrained by remaining budget)
    macro_budget = min(8, remaining_budget)
    if macro_budget > 0:
        logger.info("🌐 Starting macro reports subgraph (budget: %d)", macro_budget)
        macro_result = await macro_subgraph.ainvoke({
            "queries": state.macro_queries,
            "max_searches": macro_budget,
        })
        all_documents.extend(macro_result.get("documents", []))
        macro_used = macro_result.get("tavily_call_count", 0)
        current_search_count += macro_used
        remaining_budget = max(state.max_searches - current_search_count, 0)
        logger.info("🌐 Macro subgraph complete: %d docs, %d Tavily calls",
                     len(macro_result.get("documents", [])), macro_used)

    # Subgraph 2: Job Market (up to 8, constrained by remaining budget)
    job_budget = min(8, remaining_budget)
    if job_budget > 0:
        logger.info("💼 Starting job market subgraph (budget: %d)", job_budget)
        job_result = await job_market_subgraph.ainvoke({
            "queries": state.job_market_queries,
            "max_searches": job_budget,
        })
        all_documents.extend(job_result.get("documents", []))
        job_used = job_result.get("tavily_call_count", 0)
        current_search_count += job_used
        remaining_budget = max(state.max_searches - current_search_count, 0)
        logger.info("💼 Job market subgraph complete: %d docs, %d Tavily calls",
                     len(job_result.get("documents", [])), job_used)

    # Subgraph 3: Tech Frontier (up to 4, constrained by remaining budget)
    tech_budget = min(4, remaining_budget)
    if tech_budget > 0:
        logger.info("🚀 Starting tech frontier subgraph (budget: %d)", tech_budget)
        tech_result = await tech_subgraph.ainvoke({
            "queries": state.tech_queries,
            "max_searches": tech_budget,
        })
        all_documents.extend(tech_result.get("documents", []))
        tech_used = tech_result.get("tavily_call_count", 0)
        current_search_count += tech_used
        logger.info("🚀 Tech frontier subgraph complete: %d docs, %d Tavily calls",
                     len(tech_result.get("documents", [])), tech_used)

    logger.info("📊 All subgraphs complete: %d total docs, %d total Tavily calls",
                len(all_documents), current_search_count)

    return {
        "documents": all_documents,
        "search_count": current_search_count,
    }


# ─────────────────────────────────────────────
# M3: Summary Compression Node
# ─────────────────────────────────────────────

async def summarize_findings(state: AgentState, config: RunnableConfig) -> dict:
    """
    Compress all raw documents into a structured summary.

    This is the key node for solving token explosion:
    - Input: potentially tens of thousands of characters of raw web content
    - Output: a structured summary of 3000-6000 words
    """
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    )

    # ⭐ Defensive: empty documents
    if not state.documents:
        logger.warning("⚠️ No search results collected, returning fallback summary")
        return {
            "summary": "No search results were collected. Possible causes: network failure, "
                       "API quota exhaustion, or all URLs filtered by deduplication."
        }

    # Concatenate all documents, each truncated to 3000 chars (increased from 2000)
    combined = "\n\n---\n\n".join([
        f"Source: {doc.metadata.get('source', 'unknown')}\nTitle: {doc.metadata.get('title', '')}\n{doc.page_content[:3000]}"
        for doc in state.documents
    ])

    today = date.today()
    logger.info("📝 Compressing %d documents into structured summary (date: %s)...",
                len(state.documents), today.isoformat())

    summary_prompt = f"""**Current date: {today.isoformat()}**

Please organize the following search results into a structured summary, focusing on {today.year} data and trends.

Organize by these categories:
1. Red Zone (Declining) — jobs being replaced by AI, with reasons and data
2. Yellow Zone (Evolving) — jobs being reshaped by AI, with specific changes
3. Green Zone (Emerging) — new jobs created by AI, with required skills
4. Key data points and market insights from job platforms

Requirements:
- Prioritize {today.year} data; if older data exists, label the time period
- Preserve specific data points, source names, and URLs for citation
- Ignore clearly outdated (>1 year old) data
- Aim for at least 3 specific job titles per zone
- Include data from authoritative sources (WEF, McKinsey, LinkedIn, Indeed, etc.)

Search results:
{combined}"""

    messages = [
        {"role": "system", "content": "You are an information synthesis expert. Extract key information precisely, "
                                       "preserve data and sources. Prioritize the most recent data."},
        {"role": "user", "content": summary_prompt}
    ]

    # ⭐ Exponential backoff retry
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)
            summary_text = _extract_text(response.content)
            logger.info("📝 Summary compression complete (%d chars)", len(summary_text))
            return {"summary": summary_text}
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error("❌ Summary generation failed (retried %d times): %s", max_retries, e)
                return {
                    "summary": f"Summary generation failed (retried {max_retries} times): {str(e)}. "
                    f"Raw document count: {len(state.documents)}"
                }
            wait_time = 2 ** attempt
            logger.warning("⚠️ Summary generation failed (attempt %d), retrying in %ds: %s",
                          attempt + 1, wait_time, e)
            await asyncio.sleep(wait_time)

    return {"summary": "Unknown error during summary generation"}


# ─────────────────────────────────────────────
# M3: Structured Output Node (with retry + fallback)
# ─────────────────────────────────────────────

async def format_output_with_retry(state: AgentState, config: RunnableConfig) -> dict:
    """
    Convert compressed summary into strict JobTrendReport JSON.

    Input is summary (a few thousand chars), not raw messages (potentially tens of thousands).
    """
    settings = get_settings()
    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=0,  # temperature=0 for stable structured output
    ).with_structured_output(JobTrendReport)

    today = date.today()
    format_content = FORMAT_PROMPT.format(
        today=today.isoformat(),
        year=today.year,
    )
    logger.info("📊 Starting structured output (summary: %d chars, date: %s)",
                len(state.summary), today.isoformat())

    for attempt in range(MAX_FORMAT_RETRIES):
        try:
            result = await model.ainvoke([
                {"role": "system", "content": format_content},
                {"role": "user", "content": state.summary}
            ])
            # Validate required fields
            total_jobs = len(result.declining_jobs) + len(result.evolving_jobs) + len(result.emerging_jobs)
            assert total_jobs > 0, "Report must contain at least one job trend"
            logger.info("✅ Structured output success: %d declining, %d evolving, %d emerging jobs",
                         len(result.declining_jobs), len(result.evolving_jobs), len(result.emerging_jobs))
            return {"final_report": result}
        except Exception as e:
            logger.warning("⚠️ Structured output failed (attempt %d): %s", attempt + 1, e)
            if attempt == MAX_FORMAT_RETRIES - 1:
                logger.error("❌ Structured output final failure, returning fallback report")
                fallback_summary = f"Structured output failed ({MAX_FORMAT_RETRIES} retries). Summary excerpt: {state.summary[:500]}"
                if len(fallback_summary) < 20:
                    fallback_summary = fallback_summary.ljust(20, ".")
                fallback_report = JobTrendReport(
                    report_date=str(date.today()),
                    executive_summary=fallback_summary,
                    declining_jobs=[], evolving_jobs=[], emerging_jobs=[],
                    market_insights=[], key_reports_referenced=[]
                )
                return {"final_report": fallback_report}
            await asyncio.sleep(2 ** attempt)

    fallback_report = JobTrendReport(
        report_date=str(date.today()),
        executive_summary="Unknown error during structured output. Please check logs.",
        declining_jobs=[], evolving_jobs=[], emerging_jobs=[],
        market_insights=[], key_reports_referenced=[]
    )
    return {"final_report": fallback_report}


# ─────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────

def _extract_text(content) -> str:
    """Extract plain text from LLM response content (handles str / list[dict] formats)"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)
