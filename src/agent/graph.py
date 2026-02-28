from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from src.agent.state import AgentState, InputState
from src.agent.tools import tools   # [search_web, read_page, search_report_summary]
from src.agent.nodes import call_model, count_search_calls, should_continue

builder = StateGraph(AgentState, input=InputState)

# 注册节点
builder.add_node("agent", call_model)
builder.add_node("tools", ToolNode(tools))
builder.add_node("count_search_calls", count_search_calls)

# 设置入口
builder.set_entry_point("agent")

# agent → should_continue 条件路由
builder.add_conditional_edges("agent", should_continue, {
    "tools": "tools",
    "end": END,
})

# tools → count_search_calls → agent（回到 agent 继续对话）
builder.add_edge("tools", "count_search_calls")
builder.add_edge("count_search_calls", "agent")

# 编译
graph = builder.compile()
