"""draft-kaigi.jp (ドラフト会議ホームページ) scraper – draft prospect tracking."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from src.koshien.tools.html_cleaner import clean_html
from src.koshien.tools.scraper import ScraperBudget, scraper_fetch

logger = logging.getLogger(__name__)

_BASE = "https://draft-kaigi.jp"


async def fetch_draft_kaigi(
    school_name: str,
    *,
    budget: ScraperBudget | None = None,
) -> str:
    """Search draft-kaigi.jp for *school_name* and return draft prospect data."""
    if not school_name:
        return ""

    short = school_name.replace("高等学校", "").replace("高校", "")
    search_url = f"{_BASE}/?s={short}"
    html = await scraper_fetch(search_url, render=False, budget=budget)
    if not html:
        logger.warning("draft-kaigi.jp returned empty for %s", school_name)
        return ""

    prospect_url = _find_prospect_page(html, short)
    if prospect_url:
        logger.info("draft-kaigi.jp: fetching %s", prospect_url)
        page_html = await scraper_fetch(prospect_url, render=False, budget=budget)
        if page_html:
            return _extract_prospect_data(page_html)

    return _extract_prospect_data(html)


def _find_prospect_page(html: str, short_name: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if short_name[:3] in text and _BASE in href:
            if any(kw in text for kw in ("ドラフト", "候補", "選手", "高校")):
                return href
    return None


def _extract_prospect_data(html: str) -> str:
    """Extract draft prospect information from page HTML."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["script", "style", "nav", "footer"]):
        tag.decompose()

    parts: list[str] = []

    tables = soup.find_all("table")
    for table in tables:
        text = table.get_text()
        if any(kw in text for kw in ("ドラフト", "球速", "選手", "指名", "候補", "位")):
            rows = table.find_all("tr")
            md_rows: list[str] = []
            for row in rows:
                cells = row.find_all(["th", "td"])
                md_rows.append("| " + " | ".join(c.get_text(strip=True) for c in cells) + " |")
            if len(md_rows) >= 2:
                col_count = md_rows[0].count("|") - 1
                separator = "| " + " | ".join(["---"] * col_count) + " |"
                md_rows.insert(1, separator)
            parts.append("\n".join(md_rows))

    if not parts:
        for el in soup.find_all(["article", "div", "section"]):
            text = el.get_text(separator=" ", strip=True)
            if any(kw in text for kw in ("ドラフト", "球速", "候補", "指名")):
                cleaned = re.sub(r"\s{2,}", " ", text)
                if 50 < len(cleaned) < 2000:
                    parts.append(cleaned)

    result = "\n\n".join(parts)
    if not result:
        return clean_html(html, max_chars=3000)
    if len(result) > 4000:
        result = result[:4000] + "\n…[truncated]"
    return result
