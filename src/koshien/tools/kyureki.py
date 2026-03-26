"""kyureki.com scraper – searches for a school and fetches team data."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from src.koshien.tools.html_cleaner import clean_html
from src.koshien.tools.scraper import ScraperBudget, scraper_fetch

logger = logging.getLogger(__name__)

_BASE = "https://www.kyureki.com"
_TEAM_PAGE_RE = re.compile(r"/koko/\d+/\d+/?$")


async def _find_team_url(
    school_name: str,
    *,
    budget: ScraperBudget | None = None,
) -> str | None:
    """Search kyureki.com and return the team-top page URL, or None.

    Ranking pages list many schools in one link text (e.g. "1位 智弁学園…4位 大阪桐蔭…").
    We need the link whose text *starts* with the target school name, which is the
    actual team page (e.g. "大阪桐蔭高校野球部 - 2026年…").
    """
    query = f"{school_name} 高校野球"
    search_url = f"{_BASE}/search/?q={query}"
    search_html = await scraper_fetch(search_url, render=False, budget=budget, accept_404=True)
    if not search_html:
        return None

    soup = BeautifulSoup(search_html, "html.parser")
    short = school_name.replace("高等学校", "").replace("高校", "")[:4]

    candidates: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not _TEAM_PAGE_RE.search(href):
            continue
        if short not in text:
            continue

        if not href.startswith("http"):
            href = _BASE + href

        if text.startswith(short):
            return href

        candidates.append((text.index(short), href))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    return None


_PRIORITY_KEYWORDS = re.compile(
    r"甲子園|センバツ|選手権|大会名|結果|出場回数|優勝|全国|戦績|創部|読み方",
)


def _table_to_md(table: BeautifulSoup) -> str:
    rows = table.find_all("tr")
    if not rows:
        return ""
    md_rows: list[str] = []
    for row in rows:
        cells = row.find_all(["th", "td"])
        md_rows.append("| " + " | ".join(c.get_text(strip=True) for c in cells) + " |")
    if len(md_rows) >= 2:
        col_count = md_rows[0].count("|") - 1
        separator = "| " + " | ".join(["---"] * col_count) + " |"
        md_rows.insert(1, separator)
    return "\n".join(md_rows)


def _tables_to_markdown(html: str) -> str:
    """Extract tables, prioritising tournament/stats tables over rosters."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return clean_html(html, max_chars=8000)

    priority: list[str] = []
    secondary: list[str] = []

    for table in tables:
        md = _table_to_md(table)
        if not md:
            continue
        text = table.get_text()
        if _PRIORITY_KEYWORDS.search(text):
            priority.append(md)
        else:
            secondary.append(md)

    parts = priority + secondary
    result = ""
    for part in parts:
        if len(result) + len(part) + 2 > 8000:
            break
        result += part + "\n\n"

    return result.strip()


async def fetch_kyureki(
    school_name: str,
    *,
    budget: ScraperBudget | None = None,
) -> str:
    """Return Markdown-formatted text from a school's kyureki.com page."""
    if not school_name:
        return ""

    team_url = await _find_team_url(school_name, budget=budget)
    if not team_url:
        logger.warning("kyureki.com: no team page found for '%s'", school_name)
        return ""

    logger.info("kyureki.com: fetching %s", team_url)
    raw = await scraper_fetch(team_url, render=False, budget=budget)
    if not raw:
        logger.warning("kyureki.com: empty response for %s", team_url)
        return ""

    try:
        return _tables_to_markdown(raw)
    except Exception as exc:
        logger.error("kyureki.com parse error: %s", exc)
        return ""
