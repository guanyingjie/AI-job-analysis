"""ScraperAPI proxy wrapper with retry, timeout, and call-counting."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

_SCRAPER_BASE = "https://api.scraperapi.com"
_TIMEOUT_STATIC = 30.0
_TIMEOUT_RENDER = 60.0
_MAX_RETRIES = 3


@dataclass
class ScraperBudget:
    """Tracks ScraperAPI call budget across a single agent run."""

    max_calls: int = field(default_factory=lambda: settings.koshien_max_scraper_calls)
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)

    def consume(self) -> bool:
        if self.used >= self.max_calls:
            return False
        self.used += 1
        return True


async def scraper_fetch(
    url: str,
    *,
    render: bool = False,
    budget: ScraperBudget | None = None,
    accept_404: bool = False,
) -> str:
    """Fetch *url* via ScraperAPI.  Returns raw HTML or ``""`` on failure."""
    api_key = settings.scraper_api_key
    if not api_key:
        logger.warning("SCRAPER_API_KEY not set – skipping %s", url)
        return ""

    if budget is not None and not budget.consume():
        logger.warning("ScraperAPI budget exhausted – skipping %s", url)
        return ""

    params: dict[str, str] = {
        "api_key": api_key,
        "url": url,
        "country_code": "jp",
    }
    if render:
        params["render"] = "true"

    timeout = _TIMEOUT_RENDER if render else _TIMEOUT_STATIC
    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(_SCRAPER_BASE, params=params)
                if accept_404 and resp.status_code == 404 and len(resp.text) > 500:
                    return resp.text
                resp.raise_for_status()
                return resp.text
        except (httpx.HTTPError, Exception) as exc:
            last_err = exc
            logger.warning("ScraperAPI attempt %d/%d for %s: %s", attempt, _MAX_RETRIES, url, exc)

    logger.error("ScraperAPI all retries failed for %s: %s", url, last_err)
    return ""
