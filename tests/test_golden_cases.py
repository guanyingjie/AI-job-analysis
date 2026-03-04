"""Milestone 3 Golden Test Cases

Includes offline mock tests (CI-friendly, no external API dependency) and online test markers.
Run:
  uv run pytest tests/test_golden_cases.py -v          # offline only
  uv run pytest tests/test_golden_cases.py -v -m online # online only (needs API Keys)
"""

import json
import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock

from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────
# Case 5: Offline Mock Tests (CI-friendly)
# ─────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_search_web_returns_valid_json(mock_tavily):
    """Offline: mock Tavily API, verify search_web output format"""
    from src.agent.tools import search_web

    mock_tavily.search.return_value = {
        "results": [
            {
                "title": "WEF Future of Jobs Report 2025",
                "url": "https://example.com/wef-report",
                "content": "AI is reshaping the global job market...",
                "score": 0.95,
            },
            {
                "title": "McKinsey AI Impact Study",
                "url": "https://example.com/mckinsey",
                "content": "Automation will displace 85 million jobs...",
                "score": 0.88,
            },
        ]
    }

    result_str = await search_web.ainvoke({"query": "AI job impact 2025"})
    result = json.loads(result_str)

    assert "results" in result
    assert "query" in result
    assert "result_count" in result
    assert "error" in result
    assert result["error"] is None
    assert len(result["results"]) == 2
    assert result["result_count"] == 2

    for r in result["results"]:
        assert "title" in r
        assert "url" in r
        assert "snippet" in r


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_search_web_handles_api_error(mock_tavily):
    """Offline: Tavily API failure returns structured error JSON"""
    from src.agent.tools import search_web

    mock_tavily.search.side_effect = Exception("API rate limit exceeded")

    result_str = await search_web.ainvoke({"query": "AI job impact"})
    result = json.loads(result_str)

    assert result["results"] == []
    assert result["result_count"] == 0
    assert result["error"] is not None
    assert "rate limit" in result["error"]


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_search_report_summary_returns_valid_json(mock_tavily):
    """Offline: mock Tavily API, verify search_report_summary output format"""
    from src.agent.tools import search_report_summary

    mock_tavily.search.return_value = {
        "results": [
            {
                "title": "WEF Report Summary and Key Findings",
                "url": "https://example.com/wef-summary",
                "content": "Key findings from the WEF report...",
                "score": 0.92,
            }
        ]
    }

    result_str = await search_report_summary.ainvoke(
        {"report_name": "WEF Future of Jobs Report 2025"}
    )
    result = json.loads(result_str)

    assert "results" in result
    assert len(result["results"]) > 0
    assert "url" in result["results"][0]
    assert result["error"] is None


@pytest.mark.asyncio
async def test_read_page_timeout():
    """Offline: simulate Jina and httpx dual timeout scenario"""
    from src.agent.tools import read_page

    with patch("src.agent.tools.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result_str = await read_page.ainvoke({"url": "https://example.com/slow-page"})
        result = json.loads(result_str)

        assert result["status"] == "timeout"
        assert result["error"] is not None
        assert result["content"] is None


@pytest.mark.asyncio
async def test_read_page_forbidden():
    """Offline: simulate 403 Forbidden response"""
    from src.agent.tools import read_page

    with patch("src.agent.tools.httpx.AsyncClient") as mock_client_cls:
        mock_resp_jina = MagicMock()
        mock_resp_jina.status_code = 403
        mock_resp_jina.text = ""

        mock_resp_direct = MagicMock()
        mock_resp_direct.status_code = 403

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp_jina, mock_resp_direct])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result_str = await read_page.ainvoke({"url": "https://example.com/forbidden"})
        result = json.loads(result_str)

        assert result["status"] == "forbidden"
        assert result["error"] is not None


# ─────────────────────────────────────────────
# Google Search (Serper) Tests
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_google_search_no_api_key():
    """Offline: google_search returns error when Serper key is not configured"""
    from src.agent.tools import google_search

    with patch("src.agent.tools.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(serper_api_key="")
        result_str = await google_search.ainvoke({"query": "AI jobs 2026"})
        result = json.loads(result_str)

        assert result["results"] == []
        assert result["error"] is not None
        assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_google_search_returns_valid_json():
    """Offline: mock Serper API, verify google_search output format"""
    from src.agent.tools import google_search

    mock_serper_response = MagicMock()
    mock_serper_response.status_code = 200
    mock_serper_response.json.return_value = {
        "organic": [
            {"title": "AI Jobs Report", "link": "https://example.com/ai-jobs", "snippet": "AI employment trends..."},
            {"title": "WEF Report", "link": "https://weforum.org/report", "snippet": "Future of jobs..."},
        ]
    }
    mock_serper_response.raise_for_status = MagicMock()

    with patch("src.agent.tools.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(serper_api_key="test-key")
        with patch("src.agent.tools.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_serper_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result_str = await google_search.ainvoke({"query": "AI jobs 2026"})
            result = json.loads(result_str)

            assert result["error"] is None
            assert len(result["results"]) == 2
            assert result["results"][0]["url"] == "https://example.com/ai-jobs"


# ─────────────────────────────────────────────
# PDF Download Tests
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_pdf_timeout():
    """Offline: PDF download timeout returns proper error"""
    from src.agent.tools import download_pdf

    with patch("src.agent.tools.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result_str = await download_pdf.ainvoke({"url": "https://example.com/report.pdf"})
        result = json.loads(result_str)

        assert result["status"] == "timeout"
        assert result["content"] is None


@pytest.mark.asyncio
async def test_download_pdf_forbidden():
    """Offline: PDF 403 returns proper error"""
    from src.agent.tools import download_pdf

    with patch("src.agent.tools.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 403

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result_str = await download_pdf.ainvoke({"url": "https://example.com/report.pdf"})
        result = json.loads(result_str)

        assert result["status"] == "forbidden"
        assert result["content"] is None


# ─────────────────────────────────────────────
# Tool Contract Stability
# ─────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_tool_contract_search_web(mock_tavily):
    """Tool contract: search_web returns json.loads-able string with results list containing url"""
    from src.agent.tools import search_web

    mock_tavily.search.return_value = {
        "results": [
            {"title": "Test", "url": "https://example.com", "content": "Test content", "score": 0.9}
        ]
    }

    result_str = await search_web.ainvoke({"query": "test query"})
    result = json.loads(result_str)
    assert isinstance(result, dict)
    assert "results" in result
    assert isinstance(result["results"], list)
    if result["results"]:
        assert "url" in result["results"][0]


@pytest.mark.asyncio
async def test_tool_contract_read_page():
    """Tool contract: read_page returns json.loads-able string with status and content/error"""
    from src.agent.tools import read_page

    with patch("src.agent.tools.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Hello World - test page content"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result_str = await read_page.ainvoke({"url": "https://example.com/test"})
        result = json.loads(result_str)
        assert isinstance(result, dict)
        assert "status" in result
        assert "url" in result
        if result["status"] == "ok":
            assert "content" in result
        else:
            assert "error" in result


# ─────────────────────────────────────────────
# Subgraph Helper Tests
# ─────────────────────────────────────────────


def test_is_pdf_url_detection():
    """Verify PDF URL detection logic"""
    from src.agent.research.state import _is_pdf_url

    assert _is_pdf_url("https://example.com/report.pdf") is True
    assert _is_pdf_url("https://example.com/report.PDF") is True
    assert _is_pdf_url("https://example.com/report.pdf?v=1") is True
    assert _is_pdf_url("https://example.com/report.pdf#page=5") is True
    assert _is_pdf_url("https://example.com/report.html") is False
    assert _is_pdf_url("https://example.com/page") is False


def test_dedupe_results():
    """Verify search result deduplication"""
    from src.agent.research.state import _dedupe_results

    tavily = [
        {"url": "https://a.com", "title": "A"},
        {"url": "https://b.com", "title": "B"},
    ]
    serper = [
        {"url": "https://b.com", "title": "B duplicate"},  # duplicate
        {"url": "https://c.com", "title": "C"},
    ]

    merged = _dedupe_results(tavily, serper)
    assert len(merged) == 3
    urls = [r["url"] for r in merged]
    assert urls == ["https://a.com", "https://b.com", "https://c.com"]
    assert merged[1]["title"] == "B"


# ─────────────────────────────────────────────
# M3: Pydantic Model Tests (updated for hiring_data field)
# ─────────────────────────────────────────────


def test_pydantic_models_serialize_deserialize():
    """M3: JobTrendReport with hiring_data serializes/deserializes correctly"""
    from src.agent.models import JobTrendReport, JobTrend, JobZone, RequiredSkill, Source, MarketInsight

    report = JobTrendReport(
        report_date="2026-03-02",
        executive_summary="AI is profoundly reshaping the global job market. "
                          "LinkedIn data shows AI Engineer postings grew 74% YoY to ~45,000 active listings. "
                          "Meanwhile, data entry clerk postings declined 35% per Indeed.",
        declining_jobs=[
            JobTrend(
                job_title="Data Entry Clerk",
                job_title_en="Data Entry Clerk",
                zone=JobZone.RED,
                trend_description="Indeed data shows data entry postings declined 35% YoY in 2026",
                ai_impact="OCR + LLM automates document processing; RPA handles repetitive data tasks",
                required_skills=[RequiredSkill(skill_name="Data Processing", is_ai_related=False)],
                demand_change="Job postings down 35% YoY per Indeed data",
                hiring_data="~8,000 active listings on Indeed (down from ~12,300 last year), $28K-$38K avg salary (Glassdoor)",
                sources=[Source(url="https://example.com/wef", name="WEF Future of Jobs 2025")],
            )
        ],
        evolving_jobs=[
            JobTrend(
                job_title="Software Engineer",
                job_title_en="Software Engineer",
                zone=JobZone.YELLOW,
                trend_description="~185,000 active postings on LinkedIn; roles requiring AI/ML skills pay 25% premium",
                ai_impact="GitHub Copilot used by 77% of surveyed developers; shifts focus to architecture",
                required_skills=[
                    RequiredSkill(skill_name="Python", is_ai_related=False),
                    RequiredSkill(skill_name="Prompt Engineering", is_ai_related=True),
                ],
                demand_change="Total postings stable (-2% YoY), but AI-augmented roles grew +40%",
                hiring_data="~185,000 active listings on LinkedIn, $120K-$180K avg salary (Glassdoor), "
                           "AI-skilled roles at $150K-$220K premium",
                sources=[Source(url="https://example.com/mckinsey", name="McKinsey AI Report")],
            )
        ],
        emerging_jobs=[
            JobTrend(
                job_title="AI Prompt Engineer",
                job_title_en="AI Prompt Engineer",
                zone=JobZone.GREEN,
                trend_description="New role with ~5,200 active postings, up from near-zero two years ago",
                ai_impact="LLM applications creating entirely new profession category",
                required_skills=[RequiredSkill(skill_name="Prompt Engineering", is_ai_related=True)],
                demand_change="Annual growth >200%, postings grew from ~800 to ~5,200 in 12 months",
                hiring_data="~5,200 active listings on LinkedIn, $90K-$160K salary range (Glassdoor), "
                           "top hirers: Google, Microsoft, Anthropic, OpenAI",
                sources=[Source(url="https://example.com/linkedin", name="LinkedIn Jobs Report")],
            )
        ],
        market_insights=[
            MarketInsight(
                platform="LinkedIn",
                insight="AI-related job postings growing significantly across all major markets",
                data_point="AI Engineer postings grew 74% YoY, ~45,000 active listings globally",
                date_observed="2026-Q1",
            )
        ],
        key_reports_referenced=["WEF Future of Jobs 2025", "McKinsey AI Report"],
    )

    json_str = report.model_dump_json(ensure_ascii=False)
    parsed = json.loads(json_str)
    restored = JobTrendReport.model_validate(parsed)

    assert restored.report_date == "2026-03-02"
    assert len(restored.declining_jobs) == 1
    assert len(restored.evolving_jobs) == 1
    assert len(restored.emerging_jobs) == 1
    assert len(restored.market_insights) == 1
    assert restored.declining_jobs[0].zone == JobZone.RED
    assert restored.declining_jobs[0].sources[0].url == "https://example.com/wef"
    # Verify hiring_data field exists and is populated
    assert "8,000" in restored.declining_jobs[0].hiring_data
    assert "185,000" in restored.evolving_jobs[0].hiring_data
    assert "5,200" in restored.emerging_jobs[0].hiring_data


def test_pydantic_source_binding():
    """M3: Source model correctly binds URL and name"""
    from src.agent.models import Source

    s = Source(url="https://example.com", name="Test Report")
    assert s.url == "https://example.com"
    assert s.name == "Test Report"

    schema = Source.model_json_schema()
    assert "url" in schema["properties"]
    assert "name" in schema["properties"]


def test_pydantic_job_trend_requires_sources():
    """M3: JobTrend's sources cannot be empty"""
    from src.agent.models import JobTrend, JobZone, RequiredSkill

    with pytest.raises(Exception):
        JobTrend(
            job_title="Test",
            job_title_en="Test",
            zone=JobZone.RED,
            trend_description="test",
            ai_impact="test",
            required_skills=[RequiredSkill(skill_name="test", is_ai_related=False)],
            demand_change="test",
            hiring_data="test data",
            sources=[],  # empty list should trigger validator
        )


def test_pydantic_job_trend_has_hiring_data():
    """M3: JobTrend includes hiring_data field"""
    from src.agent.models import JobTrend, JobZone, RequiredSkill, Source

    job = JobTrend(
        job_title="AI Engineer",
        job_title_en="AI Engineer",
        zone=JobZone.GREEN,
        trend_description="Growing rapidly with ~45,000 active postings",
        ai_impact="Core role building AI systems",
        required_skills=[RequiredSkill(skill_name="PyTorch", is_ai_related=True)],
        demand_change="Grew 74% YoY per LinkedIn data",
        hiring_data="~45,000 active listings on LinkedIn, $150K-$250K salary (Glassdoor)",
        sources=[Source(url="https://linkedin.com/data", name="LinkedIn Economic Graph")],
    )
    assert job.hiring_data is not None
    assert "45,000" in job.hiring_data

    # Verify it appears in JSON schema
    schema = JobTrend.model_json_schema()
    assert "hiring_data" in schema["properties"]


# ─────────────────────────────────────────────
# M3: reduce_docs Reducer Tests
# ─────────────────────────────────────────────


def test_reduce_docs_dedup_by_url():
    """M3: reduce_docs deduplicates by URL, keeps longer version"""
    from langchain_core.documents import Document
    from src.agent.state import reduce_docs

    existing = [
        Document(page_content="short", metadata={"source": "https://a.com"}),
    ]
    new = [
        Document(page_content="this is a longer version of the content", metadata={"source": "https://a.com"}),
        Document(page_content="new doc", metadata={"source": "https://b.com"}),
    ]

    result = reduce_docs(existing, new)
    assert len(result) == 2

    a_doc = next(d for d in result if d.metadata["source"] == "https://a.com")
    assert a_doc.page_content == "this is a longer version of the content"


def test_reduce_docs_truncation():
    """M3: reduce_docs truncates to 4000 characters"""
    from langchain_core.documents import Document
    from src.agent.state import reduce_docs

    long_content = "x" * 5000
    result = reduce_docs(None, [Document(page_content=long_content, metadata={"source": "https://c.com"})])
    assert len(result) == 1
    assert len(result[0].page_content) == 4000


def test_reduce_docs_none_safety():
    """M3: reduce_docs handles None inputs"""
    from src.agent.state import reduce_docs

    assert reduce_docs(None, None) == []
    assert reduce_docs(None, []) == []
    assert reduce_docs([], None) == []


# ─────────────────────────────────────────────
# Online Tests (require API Keys, marked @pytest.mark.online)
# Run with: uv run pytest tests/test_golden_cases.py -v --run-online
# ─────────────────────────────────────────────

online = pytest.mark.skipif(
    "not config.getoption('--run-online', default=False)",
    reason="Requires --run-online flag and valid API Keys to run online tests",
)


def pytest_addoption(parser):
    """Register --run-online CLI option"""
    parser.addoption(
        "--run-online", action="store_true", default=False,
        help="Run online tests that require external APIs",
    )


@online
@pytest.mark.asyncio
async def test_online_case1_basic_search():
    """Case 1 - Basic search (online): Agent generates structured report with hiring data"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content":
            "Search for AI job market data: how many AI engineer postings on LinkedIn, "
            "salary ranges, and growth rates in 2026"
        }]},
        config={"recursion_limit": 50},
    )
    report = result.get("final_report")
    assert report is not None, "Should generate final_report"
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) >= 20


@online
@pytest.mark.asyncio
async def test_online_case2_paywall_fallback():
    """Case 2 - Paywall fallback (online): Agent searches public summaries when direct read fails"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content":
            "Read and summarize the Gartner 2025 AI technology maturity report with specific statistics"
        }]},
        config={"recursion_limit": 50},
    )
    report = result.get("final_report")
    assert report is not None
    assert isinstance(report, JobTrendReport)


@online
@pytest.mark.asyncio
async def test_online_case3_full_pipeline():
    """Case 3 - End-to-end (online): Full pipeline outputs data-driven structured report"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content":
            "Comprehensive DATA-DRIVEN analysis of AI's impact on the job market: "
            "job posting counts, salary ranges, YoY changes for declining, evolving, and emerging jobs"
        }]},
        config={"recursion_limit": 50},
    )

    report = result.get("final_report")
    assert report is not None
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) >= 20

    total_jobs = len(report.declining_jobs) + len(report.evolving_jobs) + len(report.emerging_jobs)
    assert total_jobs > 0, "Report should contain at least one job trend"

    all_jobs = report.declining_jobs + report.evolving_jobs + report.emerging_jobs
    for job in all_jobs:
        assert len(job.sources) > 0, f"{job.job_title} missing sources"
        assert job.hiring_data, f"{job.job_title} missing hiring_data"
        for source in job.sources:
            assert source.url, f"{job.job_title} source missing URL"
            assert source.name, f"{job.job_title} source missing name"

    json_str = report.model_dump_json(indent=2, ensure_ascii=False)
    parsed = json.loads(json_str)
    assert "executive_summary" in parsed
