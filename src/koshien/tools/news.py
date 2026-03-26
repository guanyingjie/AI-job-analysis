"""Yahoo Japan news fetcher – searches for recent baseball coverage."""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from src.koshien.tools.html_cleaner import clean_html
from src.koshien.tools.scraper import ScraperBudget, scraper_fetch

logger = logging.getLogger(__name__)

_YAHOO_SEARCH = "https://news.yahoo.co.jp/search"


async def fetch_news(
    school_name: str,
    *,
    budget: ScraperBudget | None = None,
) -> str:
    """Scrape Yahoo Japan News search results for *school_name* baseball news."""
    year = datetime.now().year
    query = f"{school_name} 野球部 {year}"
    url = f"{_YAHOO_SEARCH}?p={quote(query, safe='')}&ei=UTF-8"

    raw = await scraper_fetch(url, render=True, budget=budget)
    if not raw:
        logger.warning("Yahoo News returned empty for %s", school_name)
        return ""

    try:
        return clean_html(raw, max_chars=6000)
    except Exception as exc:
        logger.error("Yahoo News clean error for %s: %s", school_name, exc)
        return ""
