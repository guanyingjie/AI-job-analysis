"""Tavily API search – zero ScraperAPI budget cost, returns summarised web results."""

from __future__ import annotations

import logging
from datetime import datetime

from src.config import settings

logger = logging.getLogger(__name__)


async def tavily_search(school_name: str) -> str:
    """Run a Tavily search for recent baseball info about *school_name*.

    Returns concatenated result snippets, or ``""`` if unavailable.
    This does NOT consume ScraperAPI budget.
    """
    api_key = settings.tavily_api_key
    if not api_key:
        logger.warning("TAVILY_API_KEY not set – skipping Tavily search")
        return ""

    try:
        from tavily import AsyncTavilyClient
    except ImportError:
        logger.warning("tavily-python not installed – skipping Tavily search")
        return ""

    year = datetime.now().year
    queries = [
        f"{school_name} 高校野球 {year} 甲子園",
        f"site:hb-nippon.com {school_name} 選手名鑑",
    ]

    client = AsyncTavilyClient(api_key=api_key)
    all_snippets: list[str] = []

    for query in queries:
        try:
            result = await client.search(
                query=query,
                search_depth="basic",
                max_results=5,
                include_answer=True,
            )
            answer = result.get("answer", "")
            if answer:
                all_snippets.append(f"[Tavily summary] {answer}")

            for r in result.get("results", []):
                title = r.get("title", "")
                content = r.get("content", "")
                url = r.get("url", "")
                if content:
                    all_snippets.append(f"[{title}]({url})\n{content}")
        except Exception as exc:
            logger.warning("Tavily search failed for '%s': %s", query, exc)

    text = "\n\n".join(all_snippets)
    if len(text) > 6000:
        text = text[:6000] + "\n…[truncated]"

    logger.info("Tavily search returned %d chars for %s", len(text), school_name)
    return text
