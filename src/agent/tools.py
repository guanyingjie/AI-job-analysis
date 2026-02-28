import json
import httpx
from langchain_core.tools import tool
from src.config import get_settings
from tavily import TavilyClient

settings = get_settings()
tavily_client = TavilyClient(api_key=settings.tavily_api_key)

# Jina Reader API 前缀（免费额度充足，优先使用）
JINA_READER_PREFIX = "https://r.jina.ai/"
JINA_HEADERS = {"Authorization": f"Bearer {settings.jina_api_key}"} if settings.jina_api_key else {}


@tool
async def search_web(query: str) -> str:
    """搜索网页获取最新信息。返回 JSON 格式的搜索结果列表，包含标题、URL、摘要和相关度评分。
    适用于搜索关于 AI 对就业市场影响的报告、数据和新闻。"""
    try:
        # 注意：Tavily SDK 是同步调用，M4 会用 asyncio.to_thread() 包装避免阻塞事件循环
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
async def read_page(url: str) -> str:
    """阅读指定 URL 的网页内容。优先使用 Jina Reader API 获取干净的 Markdown，
    如果 Jina 失败则降级为 httpx + BeautifulSoup 基础提取。
    返回 JSON 字符串，包含 status 和 content 字段。内容截断到前 8000 字符。"""
    # ── 方案 1：Jina Reader API（处理 JS 渲染，输出干净 Markdown）──
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
        pass  # Jina 超时，降级到方案 2
    except Exception:
        pass  # Jina 其他错误，降级

    # ── 方案 2：httpx + BeautifulSoup 基础提取 ──
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
            # 移除干扰元素
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
async def search_report_summary(report_name: str) -> str:
    """搜索某份权威报告的公开解读和摘要。当报告原文无法直接阅读（付费墙）时，
    使用此工具搜索该报告的公开解读文章。返回 JSON 格式的搜索结果。"""
    try:
        query = f"{report_name} 摘要 解读 key findings"
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


# 导出工具列表
tools = [search_web, read_page, search_report_summary]
