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


# -----
# Offline Mock Tests (CI-friendly)
# -----


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


# -----
# Google Search (Serper) Tests
# -----


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


# -----
# PDF Download Tests
# -----


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


# -----
# Tool Contract Stability
# -----


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_tool_contract_search_web(mock_tavily):
    """Tool contract: search_web returns json.loads-able string with url"""
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
    """Tool contract: read_page returns json.loads-able string with status"""
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


# -----
# Subgraph Helper Tests
# -----


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
        {"url": "https://b.com", "title": "B duplicate"},
        {"url": "https://c.com", "title": "C"},
    ]

    merged = _dedupe_results(tavily, serper)
    assert len(merged) == 3
    urls = [r["url"] for r in merged]
    assert urls == ["https://a.com", "https://b.com", "https://c.com"]
    assert merged[1]["title"] == "B"


# -----
# M3: Pydantic Model Tests (bilingual)
# -----


def test_pydantic_models_bilingual_serialize_deserialize():
    """M3: Bilingual JobTrendReport serializes/deserializes correctly"""
    from src.agent.models import (
        JobTrendReport, JobTrend, JobZone,
        RequiredSkill, Source, MarketInsight,
    )

    report = JobTrendReport(
        report_date="2026-03-05",
        executive_summary="AI is reshaping the global job market. WEF projects 26M net new jobs by 2030.",
        executive_summary_zh="AI正在重塑全球就业市场。WEF预测到2030年将净增2600万个新岗位。",
        declining_jobs=[
            JobTrend(
                job_title_en="Data Entry Clerk",
                job_title_zh="数据录入员",
                zone=JobZone.RED,
                trend_description="WEF projects data entry roles to decline significantly by 2030",
                trend_description_zh="WEF预测数据录入岗位将在2030年前大幅下降",
                ai_impact="OCR + LLM automates document processing",
                ai_impact_zh="OCR + LLM自动化文档处理",
                required_skills=[
                    RequiredSkill(skill_name="Data Processing", skill_name_zh="数据处理", is_ai_related=False),
                ],
                demand_change="Demand projected to drop 20% by 2030 (WEF)",
                demand_change_zh="需求预计到2030年下降20%（WEF）",
                hiring_data="Broad market trend: declining (WEF, Indeed)",
                hiring_data_zh="宏观趋势：持续下降（WEF、Indeed）",
                sources=[Source(url="https://example.com/wef", name="WEF Future of Jobs 2025")],
            )
        ],
        evolving_jobs=[
            JobTrend(
                job_title_en="Software Engineer",
                job_title_zh="软件工程师",
                zone=JobZone.YELLOW,
                trend_description="AI coding assistants reshaping workflows",
                trend_description_zh="AI编码助手重塑工作流程",
                ai_impact="GitHub Copilot used by 77% of surveyed developers",
                ai_impact_zh="77%受访开发者使用GitHub Copilot",
                required_skills=[
                    RequiredSkill(skill_name="Python", skill_name_zh="Python", is_ai_related=False),
                    RequiredSkill(skill_name="Prompt Engineering", skill_name_zh="提示词工程", is_ai_related=True),
                ],
                demand_change="Stable overall, AI-augmented roles growing",
                demand_change_zh="整体稳定，AI增强型岗位增长",
                hiring_data="Broad market trend: stable with skill shift (McKinsey)",
                hiring_data_zh="宏观趋势：需求稳定但技能要求转变（McKinsey）",
                sources=[Source(url="https://example.com/mckinsey", name="McKinsey AI Report")],
            )
        ],
        emerging_jobs=[
            JobTrend(
                job_title_en="AI Prompt Engineer",
                job_title_zh="AI提示词工程师",
                zone=JobZone.GREEN,
                trend_description="Net-new role created by LLM adoption",
                trend_description_zh="由LLM广泛应用催生的全新岗位",
                ai_impact="LLM applications creating new profession category",
                ai_impact_zh="LLM应用创造全新职业类别",
                required_skills=[
                    RequiredSkill(skill_name="Prompt Engineering", skill_name_zh="提示词工程", is_ai_related=True),
                ],
                demand_change="Rapid growth, strong employer demand",
                demand_change_zh="快速增长，雇主需求强劲",
                hiring_data="Broad market trend: fast-growing (LinkedIn, surveys)",
                hiring_data_zh="宏观趋势：快速增长（LinkedIn、多项调查）",
                sources=[Source(url="https://example.com/linkedin", name="LinkedIn Jobs Report")],
            )
        ],
        market_insights=[
            MarketInsight(
                platform="LinkedIn",
                insight="AI-related postings growing significantly",
                insight_zh="AI相关岗位显著增长",
                data_point="AI Engineer postings grew 74% YoY",
                data_point_zh="AI工程师招聘同比增长74%",
                date_observed="2026-Q1",
            )
        ],
        key_reports_referenced=["WEF Future of Jobs 2025", "McKinsey AI Report"],
    )

    json_str = report.model_dump_json(ensure_ascii=False)
    parsed = json.loads(json_str)
    restored = JobTrendReport.model_validate(parsed)

    assert restored.report_date == "2026-03-05"
    assert len(restored.declining_jobs) == 1
    assert len(restored.evolving_jobs) == 1
    assert len(restored.emerging_jobs) == 1

    # Verify bilingual fields
    assert restored.executive_summary_zh
    assert "WEF" in restored.executive_summary_zh
    assert restored.declining_jobs[0].job_title_zh == "数据录入员"
    assert restored.declining_jobs[0].trend_description_zh
    assert restored.declining_jobs[0].demand_change_zh
    assert restored.declining_jobs[0].hiring_data_zh
    assert restored.evolving_jobs[0].job_title_zh == "软件工程师"
    assert restored.emerging_jobs[0].job_title_zh == "AI提示词工程师"
    assert restored.market_insights[0].insight_zh
    assert restored.market_insights[0].data_point_zh


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
            job_title_en="Test",
            job_title_zh="测试",
            zone=JobZone.RED,
            trend_description="test",
            trend_description_zh="测试",
            ai_impact="test",
            ai_impact_zh="测试",
            required_skills=[RequiredSkill(skill_name="test", is_ai_related=False)],
            demand_change="test",
            demand_change_zh="测试",
            hiring_data="test data",
            hiring_data_zh="测试数据",
            sources=[],
        )


def test_pydantic_job_trend_bilingual_fields():
    """M3: JobTrend includes all bilingual _zh fields"""
    from src.agent.models import JobTrend, JobZone, RequiredSkill, Source

    job = JobTrend(
        job_title_en="AI Engineer",
        job_title_zh="AI工程师",
        zone=JobZone.GREEN,
        trend_description="Growing rapidly according to LinkedIn data",
        trend_description_zh="根据LinkedIn数据快速增长",
        ai_impact="Core role building AI systems",
        ai_impact_zh="构建AI系统的核心角色",
        required_skills=[
            RequiredSkill(skill_name="PyTorch", skill_name_zh="PyTorch", is_ai_related=True),
        ],
        demand_change="Grew 74% YoY per LinkedIn data",
        demand_change_zh="根据LinkedIn数据同比增长74%",
        hiring_data="Strong demand across major platforms",
        hiring_data_zh="各大平台需求强劲",
        sources=[Source(url="https://linkedin.com/data", name="LinkedIn Economic Graph")],
    )
    assert job.job_title_zh == "AI工程师"
    assert job.trend_description_zh
    assert job.ai_impact_zh
    assert job.demand_change_zh
    assert job.hiring_data_zh

    schema = JobTrend.model_json_schema()
    for zh_field in [
        "job_title_zh", "trend_description_zh", "ai_impact_zh",
        "demand_change_zh", "hiring_data_zh",
    ]:
        assert zh_field in schema["properties"], f"Missing {zh_field}"


# -----
# M3: reduce_docs Reducer Tests
# -----


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


# -----
# Online Tests (require API Keys)
# Run with: uv run pytest tests/test_golden_cases.py -v --run-online
# -----

online = pytest.mark.skipif(
    "not config.getoption('--run-online', default=False)",
    reason="Requires --run-online flag and valid API Keys",
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
    """Case 1 - Basic search (online): bilingual structured report"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content":
            "Search for AI job market trends in 2026"
        }]},
        config={"recursion_limit": 50},
    )
    report = result.get("final_report")
    assert report is not None
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) >= 20
    assert report.executive_summary_zh


@online
@pytest.mark.asyncio
async def test_online_case2_paywall_fallback():
    """Case 2 - Paywall fallback (online)"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content":
            "Summarize the Gartner 2025 AI report with statistics"
        }]},
        config={"recursion_limit": 50},
    )
    report = result.get("final_report")
    assert report is not None
    assert isinstance(report, JobTrendReport)


@online
@pytest.mark.asyncio
async def test_online_case3_full_pipeline():
    """Case 3 - End-to-end (online): bilingual report"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content":
            "Comprehensive analysis of AI impact on jobs"
        }]},
        config={"recursion_limit": 50},
    )

    report = result.get("final_report")
    assert report is not None
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) >= 20
    assert report.executive_summary_zh

    total_jobs = len(report.declining_jobs) + len(report.evolving_jobs) + len(report.emerging_jobs)
    assert total_jobs > 0

    all_jobs = report.declining_jobs + report.evolving_jobs + report.emerging_jobs
    for job in all_jobs:
        assert len(job.sources) > 0
        assert job.job_title_zh
        assert job.trend_description_zh
