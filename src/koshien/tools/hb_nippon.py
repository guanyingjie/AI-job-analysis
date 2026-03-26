"""hb-nippon.com (高校野球ドットコム) scraper – player profiles & editorial articles."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from src.koshien.tools.html_cleaner import clean_html
from src.koshien.tools.scraper import ScraperBudget, scraper_fetch

logger = logging.getLogger(__name__)

_BASE = "https://www.hb-nippon.com"
_SEARCH_URL = f"{_BASE}/search/index.php"
_TEAM_RE = re.compile(r"/team/\d+")


async def fetch_hb_nippon(
    school_name: str,
    *,
    budget: ScraperBudget | None = None,
) -> str:
    """Search hb-nippon.com for *school_name* and return player/editorial content."""
    if not school_name:
        return ""

    search_url = f"{_SEARCH_URL}?keyword={school_name}&s=team"
    search_html = await scraper_fetch(search_url, render=True, budget=budget)
    if not search_html:
        logger.warning("hb-nippon search returned empty for %s", school_name)
        return ""

    team_url = _find_team_page(search_html, school_name)
    if not team_url:
        logger.warning("hb-nippon: no team page found for '%s'", school_name)
        return clean_html(search_html, max_chars=4000)

    logger.info("hb-nippon: fetching team page %s", team_url)
    team_html = await scraper_fetch(team_url, render=True, budget=budget)
    if not team_html:
        return ""

    return _extract_useful_content(team_html)


def _find_team_page(html: str, school_name: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    short = school_name.replace("高等学校", "").replace("高校", "")[:4]

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if _TEAM_RE.search(href) and short in text:
            if not href.startswith("http"):
                href = _BASE + href
            return href
    return None


def _extract_useful_content(html: str) -> str:
    """Pull player profiles, recent results, and editorial snippets."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    for cls in ("ad", "advertisement", "sidebar", "sns", "banner"):
        for el in soup.find_all(class_=lambda c: c and cls in str(c).lower()):
            el.decompose()

    parts: list[str] = []

    for section in soup.find_all(["section", "div", "article"]):
        text = section.get_text(separator=" ", strip=True)
        if any(kw in text for kw in ("選手", "名鑑", "プロフィール", "球速", "打率",
                                      "試合結果", "戦績", "ドラフト", "注目")):
            cleaned = re.sub(r"\s{2,}", " ", text)
            if len(cleaned) > 50:
                parts.append(cleaned)

    result = "\n\n".join(parts)
    if not result:
        result = clean_html(html, max_chars=4000)
    elif len(result) > 6000:
        result = result[:6000] + "\n…[truncated]"
    return result
