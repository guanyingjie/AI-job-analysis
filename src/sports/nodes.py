"""Sports Blog Agent — Node functions

Linear pipeline: search_node -> write_node -> save_node
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx

# Beijing Time (UTC+8)
BEIJING_TZ = timezone(timedelta(hours=8))
from langchain_google_genai import ChatGoogleGenerativeAI
from tavily import TavilyClient

from src.config import get_settings
from src.sports.state import SportsState
from src.sports.prompts import BLOG_WRITER_PROMPT

logger = logging.getLogger("sports")

# MLB Stats API sport IDs
SPORT_IDS = {
    "MLB": 1,
    "WBC": 51,
}

# Jina Reader
JINA_READER_PREFIX = "https://r.jina.ai/"

# ── Team name English → Chinese mapping ──
TEAM_NAME_ZH: dict[str, str] = {
    # WBC national teams
    "Australia": "澳大利亚",
    "Brazil": "巴西",
    "Canada": "加拿大",
    "China": "中国",
    "Chinese Taipei": "中华台北",
    "Colombia": "哥伦比亚",
    "Cuba": "古巴",
    "Czechia": "捷克",
    "Czech Republic": "捷克",
    "Dominican Republic": "多米尼加",
    "Germany": "德国",
    "Great Britain": "英国",
    "Israel": "以色列",
    "Italy": "意大利",
    "Japan": "日本",
    "Korea": "韩国",
    "South Korea": "韩国",
    "Mexico": "墨西哥",
    "Kingdom of the Netherlands": "荷兰",
    "Netherlands": "荷兰",
    "Nicaragua": "尼加拉瓜",
    "Panama": "巴拿马",
    "Philippines": "菲律宾",
    "Puerto Rico": "波多黎各",
    "United States": "美国",
    "USA": "美国",
    "Venezuela": "委内瑞拉",
    "New Zealand": "新西兰",
    "Spain": "西班牙",
    "France": "法国",
    "Pakistan": "巴基斯坦",
    "India": "印度",
    "Peru": "秘鲁",
    "Argentina": "阿根廷",
    "Hong Kong": "中国香港",
    "South Africa": "南非",
    "Thailand": "泰国",
}


# ─────────────────────────────────────────────
# Step 1: search_node — Hybrid Retrieval
# ─────────────────────────────────────────────

async def search_node(state: SportsState) -> dict:
    """Hybrid retrieval: MLB Stats API (exact scores) + Tavily/Jina (highlights/news).

    For the API, we query today, tomorrow, AND the day after tomorrow.
    Why day-after-tomorrow? Because UTC→Beijing time (UTC+8) shifts late-night
    UTC games into the next calendar day. A game at 2026-03-07T22:00Z becomes
    2026-03-08 06:00 Beijing time, so we must also pull 03-08 from the API to
    show a complete "tomorrow preview" in Beijing time.
    """
    tournament = state["tournament"]
    game_date = state["date"]
    settings = get_settings()

    try:
        d = date.fromisoformat(game_date)
        tomorrow = (d + timedelta(days=1)).isoformat()
        day_after = (d + timedelta(days=2)).isoformat()
    except ValueError:
        tomorrow = game_date
        day_after = game_date

    # ── API: today's results ──
    today_api = await _fetch_api_data(tournament, game_date, label="Today's Games")
    # ── API: tomorrow + day-after (covers UTC→Beijing spillover) ──
    tomorrow_api = await _fetch_api_data(tournament, tomorrow, label="Tomorrow's Games")
    day_after_api = await _fetch_api_data(tournament, day_after, label="Day-After-Tomorrow's Games (late-night UTC → Beijing next-morning)")

    api_text = f"{today_api}\n\n{tomorrow_api}\n\n{day_after_api}"

    # ── Web: highlights + news ──
    web_text = await _fetch_web_data(tournament, game_date, settings)

    combined = (
        "== OFFICIAL API DATA (TRUST THIS FOR SCORES & SCHEDULE) ==\n"
        "NOTE: All game times below are shown in Beijing Time (UTC+8).\n\n"
        f"{api_text}\n\n"
        "== WEB SEARCH HIGHLIGHTS (USE FOR NARRATIVE COLOR) ==\n"
        f"{web_text}"
    )

    logger.info("search_node complete: API=%d chars, Web=%d chars", len(api_text), len(web_text))
    return {"search_results": combined}


async def _fetch_api_data(tournament: str, game_date: str, *, label: str = "") -> str:
    """Fetch structured game data from MLB Stats API for a specific date."""
    sport_id = SPORT_IDS.get(tournament.upper())
    if sport_id is None:
        return f"(No official API available for {tournament}. Relying on web search only.)"

    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?startDate={game_date}&endDate={game_date}"
        f"&sportId={sport_id}&hydrate=team,linescore"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("MLB Stats API failed for %s: %s", game_date, e)
        return f"(API call failed for {game_date}: {e}. Relying on web search only.)"

    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            games.append(_format_game(game))

    heading = label or f"{tournament} Games"
    if not games:
        return f"### {heading} — {game_date} (UTC date)\nNo games scheduled."

    return f"### {heading} — {game_date} (UTC date)\n\n" + "\n\n".join(games)


def _translate_team(name: str) -> str:
    """Translate an English team name to Chinese. Falls back to the original name."""
    return TEAM_NAME_ZH.get(name, name)


def _format_game(game: dict) -> str:
    """Format a single game from MLB Stats API into readable text."""
    status_en = game.get("status", {}).get("detailedState", "Unknown")
    status = _translate_status(status_en)

    away = game.get("teams", {}).get("away", {})
    home = game.get("teams", {}).get("home", {})

    away_name_en = away.get("team", {}).get("name", "TBD")
    home_name_en = home.get("team", {}).get("name", "TBD")
    away_name = _translate_team(away_name_en)
    home_name = _translate_team(home_name_en)

    away_score = away.get("score", "-")
    home_score = home.get("score", "-")

    line = f"**{away_name}** {away_score} @ **{home_name}** {home_score}  —  状态: {status}"

    # Linescore details if available
    linescore = game.get("linescore")
    if linescore:
        innings = linescore.get("innings", [])
        if innings:
            header = " | ".join([f"I{i+1}" for i in range(len(innings))]) + " | R | H | E"
            away_line = " | ".join([str(inn.get("away", {}).get("runs", "-")) for inn in innings])
            home_line = " | ".join([str(inn.get("home", {}).get("runs", "-")) for inn in innings])

            totals = linescore.get("teams", {})
            for side, side_line in [("away", away_line), ("home", home_line)]:
                t = totals.get(side, {})
                r = t.get("runs", "-")
                h = t.get("hits", "-")
                e = t.get("errors", "-")
                if side == "away":
                    away_line = f"{side_line} | {r} | {h} | {e}"
                else:
                    home_line = f"{side_line} | {r} | {h} | {e}"

            line += f"\n  {header}\n  {away_name}: {away_line}\n  {home_name}: {home_line}"

    # Game time — convert UTC to Beijing Time (UTC+8)
    game_date_str = game.get("gameDate", "")
    if game_date_str:
        try:
            dt_utc = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
            dt_beijing = dt_utc.astimezone(BEIJING_TZ)
            line += f"\n  比赛时间: 北京时间 {dt_beijing.strftime('%Y-%m-%d %H:%M')}"
        except (ValueError, TypeError):
            pass

    return line


def _translate_status(status: str) -> str:
    """Translate game status to Chinese."""
    mapping = {
        "Scheduled": "未开始",
        "Pre-Game": "赛前准备",
        "Warmup": "热身中",
        "In Progress": "进行中",
        "Final": "已结束",
        "Game Over": "已结束",
        "Postponed": "延期",
        "Suspended": "暂停",
        "Cancelled": "取消",
        "Delayed": "延迟",
        "Preview": "预告",
    }
    return mapping.get(status, status)


async def _fetch_web_data(tournament: str, game_date: str, settings) -> str:
    """Fetch news highlights via Tavily search + Jina page reading."""
    tavily = TavilyClient(api_key=settings.tavily_api_key)
    jina_headers = (
        {"Authorization": f"Bearer {settings.jina_api_key}"}
        if settings.jina_api_key else {}
    )

    # Compute tomorrow's date for preview query
    try:
        d = date.fromisoformat(game_date)
        tomorrow = (d + timedelta(days=1)).isoformat()
    except ValueError:
        tomorrow = game_date

    queries = [
        f"{tournament} {game_date} game highlights results recap",
        f"{tournament} {tomorrow} schedule preview upcoming games",
    ]

    all_snippets: list[str] = []
    urls_to_read: list[str] = []

    for query in queries:
        try:
            results = tavily.search(query=query, max_results=3)
            for r in results.get("results", []):
                snippet = r.get("content", "")
                title = r.get("title", "")
                url = r.get("url", "")
                all_snippets.append(f"**{title}**\n{snippet}\nURL: {url}")
                if url and len(urls_to_read) < 2:
                    urls_to_read.append(url)
        except Exception as e:
            logger.warning("Tavily search failed for '%s': %s", query, e)

    # Read top 2 URLs via Jina for richer content (truncated to 3000 chars each)
    for url in urls_to_read:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                jina_url = f"{JINA_READER_PREFIX}{url}"
                resp = await client.get(jina_url, headers=jina_headers)
                if resp.status_code == 200 and resp.text.strip():
                    content = resp.text[:3000]  # Safe truncation
                    all_snippets.append(f"--- Full page: {url} ---\n{content}")
        except Exception as e:
            logger.warning("Jina read failed for %s: %s", url, e)

    if not all_snippets:
        return "(No web search results found.)"

    return "\n\n".join(all_snippets)


# ─────────────────────────────────────────────
# Step 2: write_node — Blog Generation
# ─────────────────────────────────────────────

async def write_node(state: SportsState) -> dict:
    """Generate a Markdown blog post from search results using Gemini."""
    settings = get_settings()
    game_date = state["date"]

    try:
        d = date.fromisoformat(game_date)
        tomorrow = (d + timedelta(days=1)).isoformat()
    except ValueError:
        tomorrow = game_date

    prompt = BLOG_WRITER_PROMPT.format(
        date=game_date,
        tournament=state["tournament"],
        tomorrow_date=tomorrow,
    )

    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=0.5,  # Slightly creative for blog writing
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": state["search_results"]},
    ]

    try:
        response = await llm.ainvoke(messages)
        blog_text = _extract_text(response.content)
        logger.info("write_node complete: %d chars", len(blog_text))
        return {"blog_markdown": blog_text}
    except Exception as e:
        logger.error("Blog generation failed: %s", e)
        fallback = (
            f"# {state['tournament']} — {game_date}\n\n"
            f"Blog generation failed: {e}\n\n"
            f"## Raw Data\n\n{state['search_results'][:2000]}"
        )
        return {"blog_markdown": fallback}


# ─────────────────────────────────────────────
# Step 3: save_node — File Output
# ─────────────────────────────────────────────

async def save_node(state: SportsState) -> dict:
    """Save the blog Markdown to posts/ directory."""
    tournament = state["tournament"].upper()
    game_date = state["date"]

    os.makedirs("posts", exist_ok=True)
    filename = f"posts/{tournament}_{game_date}.md"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(state["blog_markdown"])

    abs_path = os.path.abspath(filename)
    logger.info("Blog saved to: %s", abs_path)
    return {"output_path": abs_path}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _extract_text(content) -> str:
    """Extract plain text from LLM response (handles str / list[dict])."""
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
