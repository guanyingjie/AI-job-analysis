"""M3 主图：计划 → 分发 → 子图执行 → 摘要压缩 → 结构化输出

M3 的主图是一条线性流水线，取代了 M2 中的
conduct_research → agent → tools → count_search_calls → check_plan_finished 执行循环。
M2 的 create_research_plan 保留作为流水线入口。
"""

from langgraph.graph import StateGraph, END
from src.agent.state import AgentState, InputState

# 导入所有节点函数
from src.agent.nodes import (
    create_research_plan,
    dispatch_to_subgraphs,
    research_executor,
    summarize_findings,
    format_output_with_retry,
)

builder = StateGraph(AgentState, input=InputState)

# 注册节点
builder.add_node("create_research_plan", create_research_plan)
builder.add_node("dispatch_to_subgraphs", dispatch_to_subgraphs)
builder.add_node("research_executor", research_executor)
builder.add_node("summarize_findings", summarize_findings)
builder.add_node("format_output_with_retry", format_output_with_retry)

# 设置入口
builder.set_entry_point("create_research_plan")

# 线性流水线：计划 → 分发 → 子图执行 → 摘要压缩 → 结构化输出
builder.add_edge("create_research_plan", "dispatch_to_subgraphs")
builder.add_edge("dispatch_to_subgraphs", "research_executor")
builder.add_edge("research_executor", "summarize_findings")
builder.add_edge("summarize_findings", "format_output_with_retry")
builder.add_edge("format_output_with_retry", END)

# 编译图（M4 会在 format_output_with_retry 之后插入 save_to_db，M5 再追加 send_notification）
graph = builder.compile()
# 运行时传入 config：{"recursion_limit": 50}
