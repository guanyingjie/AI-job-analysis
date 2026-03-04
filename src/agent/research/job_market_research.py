# src/agent/research/job_market_research.py

from src.agent.research.state import build_research_subgraph

# MVP 阶段：三个模块完全一致，直接 re-export。
# 未来如需为招聘市场维度定制子图节点逻辑（如接入招聘 API），
# 可在此模块中定义独立的 build_research_subgraph 实现。
