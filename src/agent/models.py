"""M3 Pydantic Data Models — Structured Output Schema (Bilingual EN/ZH)

Serves both LLM's with_structured_output() and future M4 database ORM mapping.
Every user-facing text field has an English version and a Chinese (_zh) version.
"""

from pydantic import BaseModel, Field, field_validator
from enum import Enum


class JobZone(str, Enum):
    RED = "red"        # Declining: being replaced by AI / 红区：被AI取代
    YELLOW = "yellow"  # Evolving: reshaped by AI but not disappearing / 黄区：被AI重塑但不会消失
    GREEN = "green"    # Emerging: newly created due to AI / 绿区：因AI而新兴


class RequiredSkill(BaseModel):
    """A key skill required for a job position"""
    skill_name: str = Field(description="Name of the skill in English")
    skill_name_zh: str = Field(default="", description="技能名称（中文），例如 '提示词工程'")
    is_ai_related: bool = Field(description="Whether this is an AI-related skill")


class Source(BaseModel):
    """Information source (URL + name bound together to avoid misalignment)"""
    url: str = Field(description="Source URL")
    name: str = Field(description="Source name, e.g., 'WEF Future of Jobs Report 2025'")


class JobTrend(BaseModel):
    """Trend analysis for a single job position — bilingual (EN + ZH)"""
    job_title_en: str = Field(description="Job title in English, e.g. 'Data Entry Clerk'")
    job_title_zh: str = Field(description="职位名称（中文），例如 '数据录入员'")
    zone: JobZone = Field(description="Zone classification: red/yellow/green")

    trend_description: str = Field(
        description="[English] Trend description backed by data: cite numbers, percentages, sources"
    )
    trend_description_zh: str = Field(
        description="[中文] 趋势描述，需引用具体数据、百分比和来源"
    )

    ai_impact: str = Field(
        description="[English] How AI specifically impacts this job, with concrete examples"
    )
    ai_impact_zh: str = Field(
        description="[中文] AI如何具体影响该岗位，附具体案例"
    )

    required_skills: list[RequiredSkill] = Field(description="List of key skills required")

    demand_change: str = Field(
        description="[English] Demand change trend with source. "
                    "E.g. 'Demand projected to drop 20% by 2030 (WEF)'"
    )
    demand_change_zh: str = Field(
        description="[中文] 需求变化趋势及来源，例如 '需求预计到2030年下降20%（WEF）'"
    )

    hiring_data: str = Field(
        description="[English] Hiring statistics or macro trend summary. "
                    "If exact counts unavailable, write 'Broad market trend based on [Source]'"
    )
    hiring_data_zh: str = Field(
        description="[中文] 招聘数据或宏观趋势概述。"
                    "若无精确数据，写 '基于[来源]的宏观市场趋势'"
    )

    sources: list[Source] = Field(description="List of information sources (URL and name bound together)")

    @field_validator("sources")
    @classmethod
    def sources_not_empty(cls, v: list[Source]) -> list[Source]:
        if not v:
            raise ValueError("Each job trend must have at least one information source")
        return v


class MarketInsight(BaseModel):
    """Market insight from job platforms and data sources — bilingual"""
    platform: str = Field(description="Data source platform, e.g., LinkedIn, Indeed, Glassdoor")
    insight: str = Field(description="[English] Core insight")
    insight_zh: str = Field(description="[中文] 核心洞察")
    data_point: str = Field(description="[English] Key data point from the source")
    data_point_zh: str = Field(description="[中文] 关键数据点")
    date_observed: str = Field(description="Data observation date or range, e.g., '2025-Q4'")


class JobTrendReport(BaseModel):
    """Complete AI job trend analysis report — bilingual (EN + ZH)"""
    report_date: str = Field(description="Report generation date in YYYY-MM-DD format")

    executive_summary: str = Field(
        description="[English] Executive summary: key findings in under 300 words",
        min_length=20,
    )
    executive_summary_zh: str = Field(
        description="[中文] 执行摘要：300字以内的核心发现",
        min_length=10,
    )

    declining_jobs: list[JobTrend] = Field(description="Red Zone / 红区 — declining due to AI")
    evolving_jobs: list[JobTrend] = Field(description="Yellow Zone / 黄区 — evolving with AI")
    emerging_jobs: list[JobTrend] = Field(description="Green Zone / 绿区 — emerging because of AI")
    market_insights: list[MarketInsight] = Field(description="Market insights with data points")
    key_reports_referenced: list[str] = Field(description="List of key report names referenced")

    @field_validator("declining_jobs", "evolving_jobs", "emerging_jobs")
    @classmethod
    def at_least_some_jobs(cls, v: list[JobTrend], info) -> list[JobTrend]:
        """Allow individual lists to be empty; format_output_with_retry validates total > 0"""
        return v
