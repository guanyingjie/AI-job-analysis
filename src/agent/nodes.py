"""M3 节点函数

M3 替代了 M2 中主图的 conduct_research → agent → tools → count_search_calls → check_plan_finished
执行循环，改为 create_research_plan → dispatch_to_subgraphs → research_executor → summarize_findings
→ format_output_with_retry 的线性流水线。
"""

import asyncio
import json
import logging
from datetime import date
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage
from langchain_core.documents import Document
from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import get_settings
from src.agent.state import AgentState
from src.agent.tools import tools
from src.agent.prompts import SYSTEM_PROMPT, PLANNING_PROMPT, FORMAT_PROMPT
from src.agent.types import ResearchPlan
from src.agent.models import JobTrendReport
from src.agent.research.defaults import (
    get_default_macro_queries, get_default_job_market_queries, get_default_tech_queries,
)
from src.agent.research.macro_research import build_research_subgraph as build_macro
from src.agent.research.job_market_research import build_research_subgraph as build_job_market
from src.agent.research.tech_frontier_research import build_research_subgraph as build_tech

logger = logging.getLogger("agent")

# 编译三个子图（模块级别，只编译一次）
macro_subgraph = build_macro()
job_market_subgraph = build_job_market()
tech_subgraph = build_tech()

MAX_FORMAT_RETRIES = 3


# ─────────────────────────────────────────────
# M2 保留：研究计划生成节点
# ─────────────────────────────────────────────

async def create_research_plan(state: AgentState, config: RunnableConfig) -> dict:
    """生成研究计划并写入 state.plan_steps"""
    settings = get_settings()
    logger.info("📋 开始生成研究计划（最多 %d 步）", state.max_searches)

    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    ).with_structured_output(ResearchPlan)

    # ⭐ 注入当前日期到规划 Prompt，确保搜索聚焦最新数据
    today = date.today()
    planning_content = PLANNING_PROMPT.format(
        max_searches=state.max_searches,
        today=today.isoformat(),
        year=today.year,
        month=today.month,
        year_month=today.strftime("%Y-%m"),
    )

    # ⭐ 指数退避重试
    max_retries = 3
    for attempt in range(max_retries):
        try:
            plan = await model.ainvoke([
                {"role": "system", "content": planning_content},
                *state.messages,
            ])

            steps = plan["steps"]
            for i, step in enumerate(steps):
                logger.info("  📌 步骤 %d: [%s] %s", i + 1, step["dimension"], step["query"])

            return {
                "plan_steps": steps,
                "step_index": 0,
                "current_step": None,
            }
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error("❌ 研究计划生成失败（已重试 %d 次）: %s", max_retries, e)
                # 返回空计划，dispatch_to_subgraphs 会使用 fallback 查询
                return {
                    "plan_steps": [],
                    "step_index": 0,
                    "current_step": None,
                }
            wait_time = 2 ** attempt
            logger.warning("⚠️ 研究计划生成失败（第 %d 次），%ds 后重试: %s", attempt + 1, wait_time, e)
            await asyncio.sleep(wait_time)

    # 不应到达此处
    return {"plan_steps": [], "step_index": 0, "current_step": None}


# ─────────────────────────────────────────────
# M3 新增：分发节点
# ─────────────────────────────────────────────

def dispatch_to_subgraphs(state: AgentState) -> dict:
    """将 create_research_plan 生成的 steps 按 dimension 分发"""
    macro_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "macro"]
    job_market_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "job_market"]
    tech_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "tech_frontier"]

    logger.info("📦 分发查询: macro=%d, job_market=%d, tech=%d",
                len(macro_steps), len(job_market_steps), len(tech_steps))

    return {
        "macro_queries": macro_steps or get_default_macro_queries(),
        "job_market_queries": job_market_steps or get_default_job_market_queries(),
        "tech_queries": tech_steps or get_default_tech_queries(),
    }


# ─────────────────────────────────────────────
# M3 新增：子图执行器
# ─────────────────────────────────────────────

async def research_executor(state: AgentState) -> dict:
    """
    串行执行三个研究子图，收集所有 documents。

    从主图 state 中提取各维度的 queries，构造子图输入，
    运行子图，合并 documents 返回给主图（由 reduce_docs reducer 去重）。
    """
    all_documents: list[Document] = []
    current_search_count = state.search_count
    remaining_budget = max(state.max_searches - current_search_count, 0)

    # 子图 1：宏观报告（维度上限 3，受全局剩余预算约束）
    macro_budget = min(3, remaining_budget)
    if macro_budget > 0:
        logger.info("🌐 启动宏观报告子图（预算: %d）", macro_budget)
        macro_result = await macro_subgraph.ainvoke({
            "queries": state.macro_queries,
            "max_searches": macro_budget,
        })
        all_documents.extend(macro_result.get("documents", []))
        macro_used = macro_result.get("tavily_call_count", 0)
        current_search_count += macro_used
        remaining_budget = max(state.max_searches - current_search_count, 0)
        logger.info("🌐 宏观报告子图完成: 文档 %d 篇, Tavily 调用 %d 次",
                     len(macro_result.get("documents", [])), macro_used)

    # 子图 2：招聘市场（维度上限 3，受全局剩余预算约束）
    job_budget = min(3, remaining_budget)
    if job_budget > 0:
        logger.info("💼 启动招聘市场子图（预算: %d）", job_budget)
        job_result = await job_market_subgraph.ainvoke({
            "queries": state.job_market_queries,
            "max_searches": job_budget,
        })
        all_documents.extend(job_result.get("documents", []))
        job_used = job_result.get("tavily_call_count", 0)
        current_search_count += job_used
        remaining_budget = max(state.max_searches - current_search_count, 0)
        logger.info("💼 招聘市场子图完成: 文档 %d 篇, Tavily 调用 %d 次",
                     len(job_result.get("documents", [])), job_used)

    # 子图 3：技术前沿（维度上限 2，受全局剩余预算约束）
    tech_budget = min(2, remaining_budget)
    if tech_budget > 0:
        logger.info("🚀 启动技术前沿子图（预算: %d）", tech_budget)
        tech_result = await tech_subgraph.ainvoke({
            "queries": state.tech_queries,
            "max_searches": tech_budget,
        })
        all_documents.extend(tech_result.get("documents", []))
        tech_used = tech_result.get("tavily_call_count", 0)
        current_search_count += tech_used
        logger.info("🚀 技术前沿子图完成: 文档 %d 篇, Tavily 调用 %d 次",
                     len(tech_result.get("documents", [])), tech_used)

    logger.info("📊 所有子图执行完成: 总文档 %d 篇, 总 Tavily 调用 %d 次",
                len(all_documents), current_search_count)

    # 回写全局搜索计数，与 M1/M2 的预算字段保持一致
    return {
        "documents": all_documents,
        "search_count": current_search_count,
    }


# ─────────────────────────────────────────────
# M3 新增：摘要压缩节点
# ─────────────────────────────────────────────

async def summarize_findings(state: AgentState, config: RunnableConfig) -> dict:
    """
    将所有搜索到的原始文档压缩为一份结构化摘要。

    这是解决 Token 爆炸的关键节点：
    - 输入：可能包含数万字的原始网页内容
    - 输出：一份 3000-5000 字的结构化摘要

    压缩后再交给 format_output_with_retry，确保不会超过 context window。
    """
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    )

    # ⭐ 防御式保护：如果所有子图搜索结果为空
    if not state.documents:
        logger.warning("⚠️ 未收集到任何搜索结果，返回降级摘要")
        return {
            "summary": "未能收集到任何搜索结果。可能原因：网络故障、API 限额耗尽或所有 URL 已被去重过滤。"
        }

    # 将所有 documents 拼接，但每个截断到 2000 字
    combined = "\n\n---\n\n".join([
        f"来源: {doc.metadata.get('source', 'unknown')}\n标题: {doc.metadata.get('title', '')}\n{doc.page_content[:2000]}"
        for doc in state.documents
    ])

    today = date.today()
    logger.info("📝 开始压缩 %d 篇文档为结构化摘要（日期: %s）...", len(state.documents), today.isoformat())

    summary_prompt = f"""**当前日期：{today.isoformat()}**

请将以下搜索结果整理为一份结构化摘要，聚焦于 {today.year} 年的最新数据和趋势。
按以下分类组织：
1. 衰退区（Red Zone）岗位及原因
2. 进化区（Yellow Zone）岗位及变化
3. 新兴区（Green Zone）岗位及所需技能
4. 关键数据点和市场洞察

要求：
- 优先提取 {today.year} 年的数据，如有更早年份的数据请标注时间
- 保留具体数据、来源名称和 URL，以便后续引用
- 忽略明显过时（超过 1 年）的数据

搜索结果：
{combined}"""

    messages = [
        {"role": "system", "content": "你是一个信息整理专家。请精确提取关键信息，保留数据和来源。优先关注最新数据。"},
        {"role": "user", "content": summary_prompt}
    ]

    # ⭐ 指数退避重试
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)
            summary_text = _extract_text(response.content)
            logger.info("📝 摘要压缩完成（%d 字）", len(summary_text))
            return {"summary": summary_text}
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error("❌ 摘要生成失败（已重试 %d 次）: %s", max_retries, e)
                return {
                    "summary": f"摘要生成失败（已重试 {max_retries} 次）：{str(e)}。"
                    f"原始文档数量：{len(state.documents)}"
                }
            wait_time = 2 ** attempt
            logger.warning("⚠️ 摘要生成失败（第 %d 次），%ds 后重试: %s", attempt + 1, wait_time, e)
            await asyncio.sleep(wait_time)

    # 不应到达此处
    return {"summary": "摘要生成出现未知错误"}


# ─────────────────────────────────────────────
# M3 新增：结构化输出节点（带重试 + fallback）
# ─────────────────────────────────────────────

async def format_output_with_retry(state: AgentState, config: RunnableConfig) -> dict:
    """
    将压缩后的摘要转换为严格的 JobTrendReport JSON。

    注意：这里的输入是 summary（几千字），而非原始 messages（可能几万字）。
    """
    settings = get_settings()
    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=0,  # 结构化输出使用 temperature=0 确保稳定性
    ).with_structured_output(JobTrendReport)

    today = date.today()
    format_content = FORMAT_PROMPT.format(
        today=today.isoformat(),
        year=today.year,
    )
    logger.info("📊 开始结构化输出（摘要 %d 字, 日期: %s）", len(state.summary), today.isoformat())

    for attempt in range(MAX_FORMAT_RETRIES):
        try:
            result = await model.ainvoke([
                {"role": "system", "content": format_content},
                {"role": "user", "content": state.summary}
            ])
            # 验证必填字段不为空
            total_jobs = len(result.declining_jobs) + len(result.evolving_jobs) + len(result.emerging_jobs)
            assert total_jobs > 0, "报告必须至少包含一个岗位趋势"
            logger.info("✅ 结构化输出成功: %d 个衰退岗位, %d 个进化岗位, %d 个新兴岗位",
                         len(result.declining_jobs), len(result.evolving_jobs), len(result.emerging_jobs))
            return {"final_report": result}
        except Exception as e:
            logger.warning("⚠️ 结构化输出失败（第 %d 次）: %s", attempt + 1, e)
            if attempt == MAX_FORMAT_RETRIES - 1:
                # 最后一次重试也失败，返回一个最小化的报告
                logger.error("❌ 结构化输出最终失败，返回 fallback 报告")
                fallback_summary = f"结构化输出失败（重试 {MAX_FORMAT_RETRIES} 次），原始摘要：{state.summary[:500]}"
                if len(fallback_summary) < 20:
                    fallback_summary = fallback_summary.ljust(20, "。")
                fallback_report = JobTrendReport(
                    report_date=str(date.today()),
                    executive_summary=fallback_summary,
                    declining_jobs=[], evolving_jobs=[], emerging_jobs=[],
                    market_insights=[], key_reports_referenced=[]
                )
                return {"final_report": fallback_report}
            # 指数退避重试
            await asyncio.sleep(2 ** attempt)

    # 不应到达此处
    fallback_report = JobTrendReport(
        report_date=str(date.today()),
        executive_summary="结构化输出出现未知错误，请检查日志。",
        declining_jobs=[], evolving_jobs=[], emerging_jobs=[],
        market_insights=[], key_reports_referenced=[]
    )
    return {"final_report": fallback_report}


# ─────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────

def _extract_text(content) -> str:
    """从 LLM 返回的 content 中提取纯文本（兼容 str / list[dict] 格式）"""
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
