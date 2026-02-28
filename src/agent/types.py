from typing import TypedDict


class ResearchStep(TypedDict):
    query: str           # 搜索查询
    dimension: str       # 所属维度：macro / job_market / tech_frontier


class ResearchPlan(TypedDict):
    steps: list[ResearchStep]
