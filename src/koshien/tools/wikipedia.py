"""MediaWiki Action API wrapper – fetches baseball-relevant sections from Japanese Wikipedia."""

from __future__ import annotations

import logging
import re

import httpx

from src.koshien.tools.html_cleaner import clean_html

logger = logging.getLogger(__name__)

_API_URL = "https://ja.wikipedia.org/w/api.php"
_TIMEOUT = 15.0
_HEADERS = {
    "User-Agent": "KoshienSchoolAgent/1.0 (https://github.com/example; contact@example.com)",
}

_BASEBALL_KEYWORDS = re.compile(
    r"野球|甲子園|選手権|センバツ|OB|著名な出身者|硬式|監督|部活動|戦績|全国大会|部史",
    re.IGNORECASE,
)


async def _api_get(client: httpx.AsyncClient, **params: str) -> dict:
    params.update({"format": "json"})
    resp = await client.get(_API_URL, params=params)
    resp.raise_for_status()
    return resp.json()


async def fetch_wikipedia(title: str) -> str:
    """Return cleaned Markdown text for baseball-relevant sections, or ``""``."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
            sections_data = await _api_get(
                client, action="parse", page=title, prop="sections", redirects="true",
            )

            parse = sections_data.get("parse")
            if not parse:
                error = sections_data.get("error", {})
                logger.warning("Wikipedia page not found: %s – %s", title, error.get("info", ""))
                return ""

            page_title = parse.get("title", title)
            sections = parse.get("sections", [])

            target_indices = _pick_baseball_sections(sections)

            if not target_indices:
                logger.info("No baseball sections found for %s – fetching full page", title)
                full = await _api_get(
                    client, action="parse", page=title, prop="text", redirects="true",
                )
                html = full.get("parse", {}).get("text", {}).get("*", "")
                return f"# {page_title}\n\n" + clean_html(html, max_chars=12000)

            parts: list[str] = [f"# {page_title}"]
            for idx in target_indices:
                sec_data = await _api_get(
                    client,
                    action="parse",
                    page=title,
                    prop="text",
                    section=str(idx),
                    redirects="true",
                )
                sec_html = sec_data.get("parse", {}).get("text", {}).get("*", "")
                if sec_html:
                    parts.append(clean_html(sec_html, max_chars=6000))

            result = "\n\n".join(parts)
            logger.info(
                "Wikipedia fetched %d baseball sections for %s (%d chars)",
                len(target_indices), title, len(result),
            )
            return result

    except (httpx.HTTPError, Exception) as exc:
        logger.warning("Wikipedia fetch failed for %s: %s", title, exc)
        return ""


def _pick_baseball_sections(sections: list[dict]) -> list[int]:
    """Return section indices whose headings match baseball-related keywords."""
    indices: list[int] = []
    for sec in sections:
        heading = sec.get("line", "")
        idx = sec.get("index")
        if idx is None:
            continue
        if _BASEBALL_KEYWORDS.search(heading):
            indices.append(int(idx))

    if not indices:
        return []

    ancestor_indices: set[int] = set(indices)
    for sec in sections:
        heading = sec.get("line", "")
        idx = int(sec.get("index", 0))
        level = int(sec.get("toclevel", 99))
        if heading in ("概要", "沿革", "基礎データ") and level <= 2:
            ancestor_indices.add(idx)

    return sorted(ancestor_indices)
