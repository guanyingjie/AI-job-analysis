from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AnyMessage
from langchain_core.documents import Document  # ⭐ M3 新增
from langgraph.graph.message import add_messages

from src.config import get_settings
from src.agent.types import ResearchStep        # ⭐ M2 新增
from src.agent.models import JobTrendReport      # ⭐ M3 新增


def reduce_docs(existing: list[Document] | None, new: list[Document] | None) -> list[Document]:
    """
    合并文档列表的 reducer：
    - 按 metadata["source"]（URL）去重
    - 同 URL 文档保留更长的版本
    - 每个文档截断到 4000 字符，避免 reducer 持续膨胀
    """
    if existing is None:
        existing = []
    if new is None:
        return existing

    by_url: dict[str, Document] = {}
    for doc in existing:
        url = doc.metadata.get("source", f"unknown_{id(doc)}")
        by_url[url] = doc
    for doc in new:
        url = doc.metadata.get("source", f"unknown_{id(doc)}")
        if url not in by_url or len(doc.page_content) > len(by_url[url].page_content):
            by_url[url] = doc

    return [
        Document(page_content=d.page_content[:4000], metadata=d.metadata)
        for d in by_url.values()
    ]


@dataclass(kw_only=True)
class AgentState:
    """Agent 全局状态（M3 完整版：M1 基础 + M2 规划 + M3 子图/输出）"""

    # ── M1 基础字段 ──
    messages: Annotated[list[AnyMessage], add_messages]
    search_count: int = 0
    max_searches: int = field(default_factory=lambda: get_settings().max_searches)

    # ── M2 规划字段 ──
    plan_steps: list[ResearchStep] = field(default_factory=list)
    step_index: int = 0
    current_step: ResearchStep | None = None

    # ── M3 子图分发字段 ──
    macro_queries: list[str] = field(default_factory=list)
    job_market_queries: list[str] = field(default_factory=list)
    tech_queries: list[str] = field(default_factory=list)

    # ── M3 搜索结果与输出字段 ──
    documents: Annotated[list[Document], reduce_docs] = field(default_factory=list)
    summary: str = ""
    final_report: JobTrendReport | None = None


@dataclass(kw_only=True)
class InputState:
    """对外暴露的窄接口，用于 graph.ainvoke() 的输入"""
    messages: Annotated[list[AnyMessage], add_messages]
