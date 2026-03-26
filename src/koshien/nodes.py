"""Pipeline node functions for the Koshien agent graph."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import settings
from src.koshien.models import SchoolReport
from src.koshien.prompts import EXTRACTION_PROMPT, WRITER_PROMPT
from src.koshien.school_registry import SchoolEntry, resolve
from src.koshien.state import KoshienState
from src.koshien.tools.draft_kaigi import fetch_draft_kaigi
from src.koshien.tools.hb_nippon import fetch_hb_nippon
from src.koshien.tools.kyureki import fetch_kyureki
from src.koshien.tools.news import fetch_news
from src.koshien.tools.scraper import ScraperBudget
from src.koshien.tools.tavily_search import tavily_search
from src.koshien.tools.wikipedia import fetch_wikipedia

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node 1 – resolve_school
# ---------------------------------------------------------------------------

async def resolve_school(state: KoshienState) -> dict:
    """Map user input to a canonical SchoolEntry."""
    entry = resolve(state.school_input)
    if entry is not None:
        logger.info("Resolved '%s' -> %s", state.school_input, entry.full_name)
        return {"school_entry": _entry_to_dict(entry)}

    logger.warning("Registry miss for '%s' – falling back to LLM guess", state.school_input)
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=settings.google_api_key,
        temperature=0,
    )
    resp = await llm.ainvoke(
        f"以下は日本の高校野球の学校名です: 「{state.school_input}」。"
        "正式名称（例: ○○高等学校）、Wikipedia記事タイトル、所在都道府県を JSON で返してください。"
        'フォーマット: {{"full_name":"...","wiki_title":"...","prefecture":"..."}}'
    )
    try:
        info = json.loads(resp.content)
        fallback = SchoolEntry(
            full_name=info.get("full_name", state.school_input),
            short_names=[state.school_input],
            prefecture=info.get("prefecture", ""),
            wiki_title=info.get("wiki_title", state.school_input),
        )
        return {"school_entry": _entry_to_dict(fallback)}
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("LLM fallback parse error: %s", exc)
        fallback = SchoolEntry(
            full_name=state.school_input,
            short_names=[state.school_input],
            wiki_title=state.school_input,
        )
        return {"school_entry": _entry_to_dict(fallback)}


# ---------------------------------------------------------------------------
# Node 2 – fetch_all_sources (restructured by responsibility)
# ---------------------------------------------------------------------------
#
#  Layer A  "历史与文化打底"   Wikipedia (free)         → Modules 1-4
#  Layer B  "战绩与OB挖掘"    kyureki.com (ScraperAPI)  → Modules 2, 5
#  Layer C  "时效与现役焦点"   hb-nippon / draft-kaigi / Yahoo News / Tavily
#                                                       → Module 6
# ---------------------------------------------------------------------------

async def fetch_all_sources(state: KoshienState) -> dict:
    """Fetch all data sources in parallel, grouped by responsibility."""
    entry = state.school_entry or {}
    wiki_title = entry.get("wiki_title", state.school_input)
    school_name = entry.get("full_name", state.school_input)
    short_name = (entry.get("short_names") or [school_name])[0]

    budget = ScraperBudget()

    # Layer A: historical (free, no budget)
    wiki_task = fetch_wikipedia(wiki_title)

    # Layer B: records & OB (ScraperAPI, ~2 calls)
    kyureki_task = fetch_kyureki(short_name, budget=budget)

    # Layer C: current focus (ScraperAPI + Tavily)
    hb_task = fetch_hb_nippon(short_name, budget=budget)
    draft_task = fetch_draft_kaigi(short_name, budget=budget)
    news_task = fetch_news(school_name, budget=budget)
    tavily_task = tavily_search(short_name)

    results = await asyncio.gather(
        wiki_task, kyureki_task, hb_task, draft_task, news_task, tavily_task,
        return_exceptions=True,
    )

    names = ["Wikipedia", "kyureki.com", "hb-nippon.com", "draft-kaigi.jp", "Yahoo News", "Tavily"]
    fields = ["wiki_content", "kyureki_content", "hb_nippon_content",
              "draft_content", "news_content", "tavily_content"]

    errors: list[str] = []
    out: dict = {}
    for name, field, val in zip(names, fields, results):
        if isinstance(val, Exception):
            logger.error("%s error: %s", name, val)
            errors.append(f"{name} fetch failed: {val}")
            out[field] = ""
        else:
            out[field] = val or ""

    logger.info(
        "Fetched sources – %s (budget %d/%d)",
        ", ".join(f"{n}={len(out[f])}ch" for n, f in zip(names, fields)),
        budget.used,
        budget.max_calls,
    )

    out["errors"] = state.errors + errors
    return out


# ---------------------------------------------------------------------------
# Node 3 – extract_modules
# ---------------------------------------------------------------------------

async def extract_modules(state: KoshienState) -> dict:
    """Use LLM structured output to extract SchoolReport JSON from raw text."""
    entry = state.school_entry or {}
    school_name = entry.get("full_name", state.school_input)

    all_sources = [
        state.wiki_content, state.kyureki_content, state.news_content,
        state.hb_nippon_content, state.draft_content, state.tavily_content,
    ]
    if not any(all_sources):
        logger.error("No source data available for extraction")
        return {
            "extracted_data": SchoolReport(
                generated_at=_now_iso(),
                sources_used=[],
            ).model_dump(),
            "errors": state.errors + ["All data sources returned empty"],
        }

    realtime_content = "\n\n".join(filter(None, [
        state.news_content,
        state.hb_nippon_content,
        state.draft_content,
        state.tavily_content,
    ])) or "(データなし)"

    today_str = datetime.now().strftime("%Y年%m月%d日")
    prompt_text = EXTRACTION_PROMPT.format(
        school_name=school_name,
        today=today_str,
        wiki_content=state.wiki_content or "(データなし)",
        kyureki_content=state.kyureki_content or "(データなし)",
        realtime_content=realtime_content,
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=settings.google_api_key,
        temperature=0,
    )
    structured_llm = llm.with_structured_output(SchoolReport)

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            report: SchoolReport = await structured_llm.ainvoke(prompt_text)
            break
        except Exception as exc:
            last_err = exc
            logger.warning("Extraction attempt %d/3 failed: %s", attempt, exc)
    else:
        logger.error("Extraction exhausted retries: %s", last_err)
        report = SchoolReport()

    sources: list[str] = []
    if state.wiki_content:
        sources.append("Wikipedia (ja)")
    if state.kyureki_content:
        sources.append("kyureki.com")
    if state.hb_nippon_content:
        sources.append("高校野球ドットコム (hb-nippon.com)")
    if state.draft_content:
        sources.append("ドラフト会議.jp")
    if state.news_content:
        sources.append("Yahoo Japan News")
    if state.tavily_content:
        sources.append("Tavily Web Search")

    report.generated_at = _now_iso()
    report.sources_used = sources

    return {"extracted_data": report.model_dump()}


# ---------------------------------------------------------------------------
# Node 4 – write_document
# ---------------------------------------------------------------------------

async def write_document(state: KoshienState) -> dict:
    """Generate a polished Markdown article from the structured SchoolReport."""
    entry = state.school_entry or {}
    school_name = entry.get("full_name", state.school_input)
    data = state.extracted_data or {}

    sources_text = "\n".join(f"- {s}" for s in data.get("sources_used", []))
    missing: list[str] = []
    if not state.news_content:
        missing.append("Yahoo Japan News")
    if not state.hb_nippon_content:
        missing.append("高校野球ドットコム")
    if missing:
        sources_text += "\n" + "\n".join(f"- ⚠️ {s}: 未能获取" for s in missing)

    prompt_text = WRITER_PROMPT.format(
        school_name=school_name,
        extracted_json=json.dumps(data, ensure_ascii=False, indent=2),
        sources=sources_text or "(无可用来源)",
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=settings.google_api_key,
        temperature=0.6,
    )

    try:
        resp = await llm.ainvoke([
            SystemMessage(content=prompt_text),
            HumanMessage(content=f"请为「{school_name}」撰写强校巡礼文章。"),
        ])
        markdown = resp.content
    except Exception as exc:
        logger.error("Writer LLM failed: %s – falling back to raw JSON", exc)
        markdown = (
            f"# {school_name} — 強校巡礼（Raw Data Fallback）\n\n"
            f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```\n"
        )

    return {"document_markdown": markdown}


# ---------------------------------------------------------------------------
# Node 5 – save_document
# ---------------------------------------------------------------------------

async def save_document(state: KoshienState) -> dict:
    """Persist the final Markdown document to ``docs/``."""
    entry = state.school_entry or {}
    school_name = entry.get("short_names", [entry.get("full_name", state.school_input)])
    if isinstance(school_name, list):
        school_name = school_name[0] if school_name else state.school_input

    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{school_name}_{date_str}.md"
    out_dir = Path("docs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    out_path.write_text(state.document_markdown, encoding="utf-8")
    logger.info("Document saved to %s", out_path)

    return {"output_path": str(out_path)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry_to_dict(entry: SchoolEntry) -> dict:
    return {
        "full_name": entry.full_name,
        "short_names": list(entry.short_names),
        "prefecture": entry.prefecture,
        "wiki_title": entry.wiki_title,
        "kyureki_id": entry.kyureki_id,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
