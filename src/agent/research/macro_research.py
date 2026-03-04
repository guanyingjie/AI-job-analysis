# src/agent/research/macro_research.py

from src.agent.research.state import build_research_subgraph

# MVP 阶段：三个模块完全一致，直接 re-export。
# 未来如需为不同维度定制子图节点逻辑（如 macro 维度增加报告下载节点），
# 可在此模块中定义独立的 build_research_subgraph 实现。
