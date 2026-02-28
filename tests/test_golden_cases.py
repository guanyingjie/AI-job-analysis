"""Milestone 3 Golden Test Cases

包含离线 Mock 测试（CI 友好，不依赖外部 API）和在线测试标记。
运行方式：
  uv run pytest tests/test_golden_cases.py -v          # 仅离线测试
  uv run pytest tests/test_golden_cases.py -v -m online # 仅在线测试（需要 API Key）
"""

import json
import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock

from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────
# Case 5：离线 Mock 测试（CI 友好）
# ─────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_search_web_returns_valid_json(mock_tavily):
    """离线测试：mock Tavily API，验证 search_web 输出格式"""
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

    # 验证顶层结构
    assert "results" in result
    assert "query" in result
    assert "result_count" in result
    assert "error" in result
    assert result["error"] is None

    # 验证结果列表
    assert len(result["results"]) == 2
    assert result["result_count"] == 2

    # 验证每条结果包含必需字段
    for r in result["results"]:
        assert "title" in r
        assert "url" in r
        assert "snippet" in r


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_search_web_handles_api_error(mock_tavily):
    """离线测试：Tavily API 故障时返回结构化错误 JSON"""
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
    """离线测试：mock Tavily API，验证 search_report_summary 输出格式"""
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
    """离线测试：模拟 Jina 和 httpx 双重超时场景"""
    from src.agent.tools import read_page

    # 同时 mock Jina 和 httpx 的 AsyncClient，让两者都超时
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
    """离线测试：模拟 403 Forbidden 响应"""
    from src.agent.tools import read_page

    with patch("src.agent.tools.httpx.AsyncClient") as mock_client_cls:
        # Jina 请求：返回 403
        mock_resp_jina = MagicMock()
        mock_resp_jina.status_code = 403
        mock_resp_jina.text = ""

        # httpx 直接请求：也返回 403
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
# Case 4：工具契约稳定性（纯结构验证）
# ─────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agent.tools.tavily_client")
async def test_tool_contract_search_web(mock_tavily):
    """工具契约：search_web 返回可 json.loads 的字符串，含 results 列表且元素含 url"""
    from src.agent.tools import search_web

    mock_tavily.search.return_value = {
        "results": [
            {"title": "Test", "url": "https://example.com", "content": "Test content", "score": 0.9}
        ]
    }

    result_str = await search_web.ainvoke({"query": "test query"})

    # 必须是有效 JSON
    result = json.loads(result_str)
    assert isinstance(result, dict)
    assert "results" in result
    assert isinstance(result["results"], list)
    if result["results"]:
        assert "url" in result["results"][0]


@pytest.mark.asyncio
async def test_tool_contract_read_page():
    """工具契约：read_page 返回可 json.loads 的字符串，含 status 和 content/error"""
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
        # ok 状态应有 content，非 ok 状态应有 error
        if result["status"] == "ok":
            assert "content" in result
        else:
            assert "error" in result


# ─────────────────────────────────────────────
# M3 新增：Pydantic 模型测试
# ─────────────────────────────────────────────


def test_pydantic_models_serialize_deserialize():
    """M3：JobTrendReport 能正常序列化/反序列化"""
    from src.agent.models import JobTrendReport, JobTrend, JobZone, RequiredSkill, Source, MarketInsight

    report = JobTrendReport(
        report_date="2026-02-28",
        executive_summary="AI 正在深刻重塑全球就业市场，衰退区岗位加速缩减，新兴区岗位快速增长。",
        declining_jobs=[
            JobTrend(
                job_title="数据录入员",
                job_title_en="Data Entry Clerk",
                zone=JobZone.RED,
                trend_description="大量重复性数据处理工作被 AI 自动化取代",
                ai_impact="OCR + LLM 使文档处理效率提升 10 倍",
                required_skills=[RequiredSkill(skill_name="数据处理", is_ai_related=False)],
                demand_change="需求下降 40%",
                sources=[Source(url="https://example.com/wef", name="WEF Future of Jobs 2025")],
            )
        ],
        evolving_jobs=[
            JobTrend(
                job_title="软件工程师",
                job_title_en="Software Engineer",
                zone=JobZone.YELLOW,
                trend_description="AI 辅助编程工具改变工作流",
                ai_impact="GitHub Copilot 等工具使编码效率提升 30%",
                required_skills=[
                    RequiredSkill(skill_name="Python", is_ai_related=False),
                    RequiredSkill(skill_name="Prompt Engineering", is_ai_related=True),
                ],
                demand_change="需求稳定，技能要求升级",
                sources=[Source(url="https://example.com/mckinsey", name="McKinsey AI Report")],
            )
        ],
        emerging_jobs=[
            JobTrend(
                job_title="AI 提示工程师",
                job_title_en="AI Prompt Engineer",
                zone=JobZone.GREEN,
                trend_description="新兴岗位，需求快速增长",
                ai_impact="大模型应用催生新职业",
                required_skills=[RequiredSkill(skill_name="Prompt Engineering", is_ai_related=True)],
                demand_change="年增长率 200%",
                sources=[Source(url="https://example.com/linkedin", name="LinkedIn Jobs Report")],
            )
        ],
        market_insights=[
            MarketInsight(
                platform="LinkedIn",
                insight="AI 相关岗位显著增长",
                data_point="AI Engineer 岗位同比增长 74%",
                date_observed="2025-Q4",
            )
        ],
        key_reports_referenced=["WEF Future of Jobs 2025", "McKinsey AI Report"],
    )

    # 序列化
    json_str = report.model_dump_json(ensure_ascii=False)
    parsed = json.loads(json_str)

    # 反序列化
    restored = JobTrendReport.model_validate(parsed)

    assert restored.report_date == "2026-02-28"
    assert len(restored.declining_jobs) == 1
    assert len(restored.evolving_jobs) == 1
    assert len(restored.emerging_jobs) == 1
    assert len(restored.market_insights) == 1
    assert restored.declining_jobs[0].zone == JobZone.RED
    assert restored.declining_jobs[0].sources[0].url == "https://example.com/wef"


def test_pydantic_source_binding():
    """M3：Source 模型正确绑定 URL 和名称，无对不齐风险"""
    from src.agent.models import Source

    s = Source(url="https://example.com", name="Test Report")
    assert s.url == "https://example.com"
    assert s.name == "Test Report"

    # JSON Schema 可被 with_structured_output 消费
    schema = Source.model_json_schema()
    assert "url" in schema["properties"]
    assert "name" in schema["properties"]


def test_pydantic_job_trend_requires_sources():
    """M3：JobTrend 的 sources 不能为空"""
    from src.agent.models import JobTrend, JobZone, RequiredSkill

    with pytest.raises(Exception):
        JobTrend(
            job_title="测试",
            job_title_en="Test",
            zone=JobZone.RED,
            trend_description="test",
            ai_impact="test",
            required_skills=[RequiredSkill(skill_name="test", is_ai_related=False)],
            demand_change="test",
            sources=[],  # 空列表应触发 validator
        )


# ─────────────────────────────────────────────
# M3 新增：reduce_docs reducer 测试
# ─────────────────────────────────────────────


def test_reduce_docs_dedup_by_url():
    """M3：reduce_docs 按 URL 去重，保留更长版本"""
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

    # a.com 应保留更长版本
    a_doc = next(d for d in result if d.metadata["source"] == "https://a.com")
    assert a_doc.page_content == "this is a longer version of the content"


def test_reduce_docs_truncation():
    """M3：reduce_docs 截断到 4000 字符"""
    from langchain_core.documents import Document
    from src.agent.state import reduce_docs

    long_content = "x" * 5000
    result = reduce_docs(None, [Document(page_content=long_content, metadata={"source": "https://c.com"})])
    assert len(result) == 1
    assert len(result[0].page_content) == 4000


def test_reduce_docs_none_safety():
    """M3：reduce_docs 处理 None 输入"""
    from src.agent.state import reduce_docs

    assert reduce_docs(None, None) == []
    assert reduce_docs(None, []) == []
    assert reduce_docs([], None) == []


# ─────────────────────────────────────────────
# 在线测试（需要 API Key，标记 @pytest.mark.online）
# 运行方式：uv run pytest tests/test_golden_cases.py -v --run-online
# ─────────────────────────────────────────────

online = pytest.mark.skipif(
    "not config.getoption('--run-online', default=False)",
    reason="需要 --run-online 标志和有效 API Key 才能运行在线测试",
)


def pytest_addoption(parser):
    """注册 --run-online 命令行选项"""
    parser.addoption(
        "--run-online", action="store_true", default=False,
        help="运行需要外部 API 的在线测试",
    )


@online
@pytest.mark.asyncio
async def test_online_case1_basic_search():
    """Case 1 - 基础搜索能力（在线）：Agent 能搜索并生成结构化报告"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "搜索 2025 年 WEF 未来就业报告的关键发现"}]},
        config={"recursion_limit": 50},
    )
    report = result.get("final_report")
    assert report is not None, "应生成 final_report"
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) >= 20


@online
@pytest.mark.asyncio
async def test_online_case2_paywall_fallback():
    """Case 2 - 付费墙降级（在线）：Agent 应在直接阅读失败后搜索公开解读"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "阅读并总结 Gartner 2025 年 AI 技术成熟度报告"}]},
        config={"recursion_limit": 50},
    )
    report = result.get("final_report")
    assert report is not None
    assert isinstance(report, JobTrendReport)


@online
@pytest.mark.asyncio
async def test_online_case3_full_pipeline():
    """Case 3 - 端到端集成测试（在线）：完整流水线输出结构化报告"""
    from src.agent.graph import graph
    from src.agent.models import JobTrendReport

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "全面分析 AI 对就业市场的影响，包括衰退、进化和新兴岗位"}]},
        config={"recursion_limit": 50},
    )

    report = result.get("final_report")
    assert report is not None
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) >= 20

    total_jobs = len(report.declining_jobs) + len(report.evolving_jobs) + len(report.emerging_jobs)
    assert total_jobs > 0, "报告应至少包含一个岗位趋势"

    # 来源检查（使用 Source 模型）
    all_jobs = report.declining_jobs + report.evolving_jobs + report.emerging_jobs
    for job in all_jobs:
        assert len(job.sources) > 0, f"{job.job_title} 缺少来源"
        for source in job.sources:
            assert source.url, f"{job.job_title} 的来源缺少 URL"
            assert source.name, f"{job.job_title} 的来源缺少名称"

    # JSON 可序列化
    json_str = report.model_dump_json(indent=2, ensure_ascii=False)
    parsed = json.loads(json_str)
    assert "executive_summary" in parsed
