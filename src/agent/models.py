"""M3 Pydantic Data Models — Structured Output Schema

Serves both LLM's with_structured_output() and future M4 database ORM mapping.
"""

from pydantic import BaseModel, Field, field_validator
from enum import Enum


class JobZone(str, Enum):
    RED = "red"        # Declining: being replaced by AI
    YELLOW = "yellow"  # Evolving: reshaped by AI but not disappearing
    GREEN = "green"    # Emerging: newly created due to AI


class RequiredSkill(BaseModel):
    """A key skill required for a job position"""
    skill_name: str = Field(description="Name of the skill")
    is_ai_related: bool = Field(description="Whether this is an AI-related skill")


class Source(BaseModel):
    """Information source (URL + name bound together to avoid misalignment)"""
    url: str = Field(description="Source URL")
    name: str = Field(description="Source name, e.g., 'WEF Future of Jobs Report 2025'")


class JobTrend(BaseModel):
    """Trend analysis for a single job position — must include quantitative data"""
    job_title: str = Field(description="Job title in the original language of the source")
    job_title_en: str = Field(description="Job title in English")
    zone: JobZone = Field(description="Zone classification: red/yellow/green")
    trend_description: str = Field(
        description="Trend description backed by SPECIFIC DATA: cite numbers, percentages, "
                    "and sources. E.g., 'WEF projects 26M net new jobs by 2030 in AI-related fields'"
    )
    ai_impact: str = Field(
        description="How AI specifically impacts this job, with concrete examples and data"
    )
    required_skills: list[RequiredSkill] = Field(description="List of key skills required for this job")
    demand_change: str = Field(
        description="Quantitative demand change with source. "
                    "E.g., 'Job postings down 30% YoY per LinkedIn data' or 'Grew 50% YoY per Indeed'"
    )
    hiring_data: str = Field(
        description="Current hiring statistics: approximate active job posting count, salary range, "
                    "growth rate, and data source. "
                    "E.g., '~15,000 active listings on LinkedIn, $120K-$180K avg salary (Glassdoor), +25% YoY'. "
                    "If exact data not found, state 'Exact data not available; estimated based on [source]'"
    )
    sources: list[Source] = Field(description="List of information sources (URL and name bound together)")

    @field_validator("sources")
    @classmethod
    def sources_not_empty(cls, v: list[Source]) -> list[Source]:
        if not v:
            raise ValueError("Each job trend must have at least one information source")
        return v


class MarketInsight(BaseModel):
    """Market insight from job platforms and data sources"""
    platform: str = Field(description="Data source platform, e.g., LinkedIn, Indeed, Glassdoor")
    insight: str = Field(description="Core insight with specific numbers")
    data_point: str = Field(
        description="Key quantitative data point, e.g., 'AI Engineer postings grew 74% YoY, "
                    "~25,000 active listings on LinkedIn'"
    )
    date_observed: str = Field(description="Data observation date or range, e.g., '2025-Q4' or '2025-12'")


class JobTrendReport(BaseModel):
    """Complete AI job trend analysis report — data-driven with quantitative evidence"""
    report_date: str = Field(description="Report generation date in YYYY-MM-DD format")
    executive_summary: str = Field(
        description="Executive summary: key findings in under 300 words, must include specific numbers",
        min_length=20,
    )
    declining_jobs: list[JobTrend] = Field(description="Red Zone jobs list — declining due to AI, with hiring data")
    evolving_jobs: list[JobTrend] = Field(description="Yellow Zone jobs list — evolving with AI, with hiring data")
    emerging_jobs: list[JobTrend] = Field(description="Green Zone jobs list — emerging because of AI, with hiring data")
    market_insights: list[MarketInsight] = Field(description="Market micro-insights with quantitative data points")
    key_reports_referenced: list[str] = Field(description="List of key report names referenced")

    @field_validator("declining_jobs", "evolving_jobs", "emerging_jobs")
    @classmethod
    def at_least_some_jobs(cls, v: list[JobTrend], info) -> list[JobTrend]:
        """Allow individual lists to be empty; format_output_with_retry validates total > 0"""
        return v
