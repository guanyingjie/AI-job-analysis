from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from src.config import get_settings


@dataclass(kw_only=True)
class AgentState:
    """Agent 全局状态（后续 M2-M5 会逐步新增字段，在此基础上扩展即可）"""
    messages: Annotated[list[AnyMessage], add_messages]
    search_count: int = 0
    max_searches: int = field(default_factory=lambda: get_settings().max_searches)
    # ↑ 从 config.py 动态读取，支持通过环境变量 MAX_SEARCHES=10 覆盖


@dataclass(kw_only=True)
class InputState:
    """对外暴露的窄接口，用于 graph.ainvoke() 的输入。
    外部调用方只需传入 messages，无需了解内部 search_count 等字段。"""
    messages: Annotated[list[AnyMessage], add_messages]
