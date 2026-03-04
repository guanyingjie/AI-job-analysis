# src/agent/research/tech_frontier_research.py

from src.agent.research.state import build_research_subgraph

# MVP 阶段：三个模块完全一致，直接 re-export。
# 未来如需为技术前沿维度定制子图节点逻辑（如增加融资信息聚合），
# 可在此模块中定义独立的 build_research_subgraph 实现。
