import json
import httpx
from langchain_core.tools import tool
from src.config import get_settings

settings = get_settings()

# ── Tavily (primary search engine) ──
from tavily import TavilyClient
tavily_client = TavilyClient(api_key=settings.tavily_api_key)

# ── Jina Reader API (page reading) ──
JINA_READER_PREFIX = "https://r.jina.ai/"
JINA_HEADERS = {"Authorization": f"Bearer {settings.jina_api_key}"} if settings.jina_api_key else {}


@tool
async def search_web(query: str) -> str:
    """Search the web using Tavily API. Returns JSON with titles, URLs, snippets, and relevance scores.
    Best for AI and job market reports, data, and news."""
    try:
        results = tavily_client.search(query=query, max_results=5)
        output = {
            "query": query,
            "results": [
                {"title": r["title"], "url": r["url"], "snippet": r.get("content", ""), "score": r.get("score")}
                for r in results.get("results", [])
            ],
            "result_count": len(results.get("results", [])),
            "error": None,
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"query": query, "results": [], "result_count": 0, "error": str(e)}, ensure_ascii=False)


@tool
async def google_search(query: str) -> str:
    """Search Google via Serper API for comprehensive web results including LinkedIn, Indeed,
    WEF, McKinsey official sites. Returns JSON with titles, URLs, and snippets.
    Provides better coverage of English-language authoritative sources than Tavily alone."""
    serper_key = get_settings().serper_api_key
    if not serper_key:
        return json.dumps({"query": query, "results": [], "result_count": 0,
                           "error": "Serper API key not configured"}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                json={"q": query, "num": 10},
                headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data.get("organic", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "score": item.get("position"),
                })

            return json.dumps({
                "query": query,
                "results": results,
                "result_count": len(results),
                "error": None,
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"query": query, "results": [], "result_count": 0, "error": str(e)}, ensure_ascii=False)


@tool
async def read_page(url: str) -> str:
    """Read a web page. Uses Jina Reader API for clean Markdown, falls back to httpx + BeautifulSoup.
    Returns JSON with status and content fields. Content truncated to 8000 characters."""
    # ── Method 1: Jina Reader API (handles JS rendering, outputs clean Markdown) ──
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            jina_url = f"{JINA_READER_PREFIX}{url}"
            resp = await client.get(jina_url, headers=JINA_HEADERS)
            if resp.status_code == 200 and resp.text.strip():
                content = resp.text[:8000]
                return json.dumps({
                    "url": url, "status": "ok", "content": content,
                    "error": None, "truncated": len(resp.text) > 8000,
                }, ensure_ascii=False)
    except httpx.TimeoutException:
        pass
    except Exception:
        pass

    # ── Method 2: httpx + BeautifulSoup basic extraction ──
    try:
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)

            if resp.status_code == 403:
                return json.dumps({
                    "url": url, "status": "forbidden",
                    "content": None, "error": "Access forbidden (403)",
                    "truncated": False,
                }, ensure_ascii=False)

            if resp.status_code in {401, 402}:
                return json.dumps({
                    "url": url, "status": "paywalled",
                    "content": None, "error": f"Paywalled ({resp.status_code})",
                    "truncated": False,
                }, ensure_ascii=False)

            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            content = text[:8000]
            return json.dumps({
                "url": url, "status": "ok", "content": content,
                "error": None, "truncated": len(text) > 8000,
            }, ensure_ascii=False)

    except httpx.TimeoutException:
        return json.dumps({
            "url": url, "status": "timeout",
            "content": None, "error": "Request timed out (10s)",
            "truncated": False,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "url": url, "status": "error",
            "content": None, "error": str(e),
            "truncated": False,
        }, ensure_ascii=False)


@tool
async def download_pdf(url: str) -> str:
    """Download and parse a PDF document from a URL (e.g., WEF, McKinsey, OECD reports).
    Extracts text from up to 30 pages. Returns JSON with status and content.
    Content truncated to 12000 characters for thorough extraction."""
    try:
        import pdfplumber
        import io

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })

            if resp.status_code in {401, 402, 403}:
                return json.dumps({
                    "url": url, "status": "forbidden",
                    "content": None, "error": f"Access denied ({resp.status_code})",
                    "truncated": False, "total_pages": 0, "pages_parsed": 0,
                }, ensure_ascii=False)

            resp.raise_for_status()

            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text_parts = []
                max_pages = min(30, len(pdf.pages))
                for page in pdf.pages[:max_pages]:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)

                full_text = "\n\n".join(text_parts)
                content = full_text[:12000]

                return json.dumps({
                    "url": url,
                    "status": "ok",
                    "content": content,
                    "error": None,
                    "truncated": len(full_text) > 12000,
                    "total_pages": len(pdf.pages),
                    "pages_parsed": max_pages,
                }, ensure_ascii=False)

    except httpx.TimeoutException:
        return json.dumps({
            "url": url, "status": "timeout",
            "content": None, "error": "PDF download timed out (30s)",
            "truncated": False, "total_pages": 0, "pages_parsed": 0,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "url": url, "status": "error",
            "content": None, "error": str(e),
            "truncated": False, "total_pages": 0, "pages_parsed": 0,
        }, ensure_ascii=False)


@tool
async def search_report_summary(report_name: str) -> str:
    """Search for public summaries and interpretations of a specific report.
    Use when the original report is paywalled. Returns JSON search results."""
    try:
        query = f"{report_name} summary key findings analysis"
        results = tavily_client.search(query=query, max_results=5)
        output = {
            "query": query,
            "results": [
                {"title": r["title"], "url": r["url"], "snippet": r.get("content", ""), "score": r.get("score")}
                for r in results.get("results", [])
            ],
            "result_count": len(results.get("results", [])),
            "error": None,
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"query": report_name, "results": [], "result_count": 0, "error": str(e)}, ensure_ascii=False)


# Export tool list
tools = [search_web, google_search, read_page, download_pdf, search_report_summary]
