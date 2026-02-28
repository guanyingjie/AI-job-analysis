import logging
from datetime import date
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import get_settings
from src.agent.state import AgentState
from src.agent.tools import tools

logger = logging.getLogger("agent")

# System Prompt 初稿（M2 会提取到 prompts.py 并深度调优）
_SYSTEM_PROMPT = """你是一个专业的 AI 与就业市场研究分析师。

**今天的日期是 {today}。** 你必须搜索和关注这个时间点附近（最近一个月内）的最新信息，
不要搜索过时的旧数据。在搜索时请在关键词中包含当前的年份和月份（如 "{year}" 或 "{year_month}"）。

你的任务是：

1. 搜索并收集关于 AI 技术对全球就业市场影响的**最新**信息
2. 重点关注以下三类岗位变化：
   - 衰退区（Red Zone）：正在被 AI 替代的岗位
   - 进化区（Yellow Zone）：工作流被 AI 重塑但不会消失的岗位
   - 新兴区（Green Zone）：因 AI 而新诞生的岗位
3. 优先搜索权威来源（WEF、McKinsey、BCG、LinkedIn 等）
4. 如果某份报告无法直接阅读（付费墙），请搜索该报告的公开解读文章

注意：你最多可以搜索 {max_searches} 次。请合理规划搜索策略，不要浪费搜索次数。
当你认为已经收集到足够的信息，请直接给出你的分析总结，不要继续搜索。"""


async def call_model(state: AgentState, config: RunnableConfig) -> dict:
    """调用 LLM（带工具绑定），M2 会增加 current_step 注入和重试逻辑"""
    settings = get_settings()
    logger.info("🤖 调用 LLM（模型: %s）| 搜索次数: %d/%d",
                settings.llm_model_name, state.search_count, state.max_searches)

    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    ).bind_tools(tools)

    today = date.today()
    system_content = _SYSTEM_PROMPT.format(
        today=today.isoformat(),
        year=today.year,
        year_month=today.strftime("%Y-%m"),
        max_searches=state.max_searches,
    )

    messages = [
        {"role": "system", "content": system_content},
        *state.messages,
    ]

    response = await llm.ainvoke(messages)

    # 日志：LLM 返回了什么
    if response.tool_calls:
        for tc in response.tool_calls:
            logger.info("🔧 LLM 请求调用工具: %s(%s)", tc["name"], tc["args"])
    else:
        # 提取纯文本内容用于日志预览
        text = _extract_text(response.content)
        preview = text[:200] + "..." if len(text) > 200 else text
        logger.info("💬 LLM 返回最终回复（前200字）: %s", preview)

    return {"messages": [response]}


def count_search_calls(state: AgentState) -> dict:
    """对 Tavily 搜索类工具调用计数，read_page 不消耗搜索配额"""
    # 从最新的 AIMessage 中统计搜索类工具的调用次数
    last_ai_msg = next(
        (m for m in reversed(state.messages) if isinstance(m, AIMessage) and m.tool_calls),
        None
    )
    if last_ai_msg is None:
        return {}

    search_tool_names = {"search_web", "search_report_summary"}
    search_calls = sum(
        1 for tc in last_ai_msg.tool_calls
        if tc["name"] in search_tool_names
    )
    new_count = state.search_count + search_calls
    if search_calls > 0:
        logger.info("📊 搜索计数更新: %d → %d（本轮 +%d）| 上限: %d",
                     state.search_count, new_count, search_calls, state.max_searches)
    return {"search_count": new_count}


def should_continue(state: AgentState) -> Literal["tools", "end"]:
    """条件路由函数：决定 Agent 是继续调用工具还是结束"""
    last_message = state.messages[-1]

    # 安全阀：超过最大搜索次数，强制结束
    if state.search_count >= state.max_searches:
        logger.warning("🛑 搜索次数已达上限（%d/%d），强制结束",
                        state.search_count, state.max_searches)
        return "end"

    # 模型决定调用工具
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        tool_names = [tc["name"] for tc in last_message.tool_calls]
        logger.info("➡️  路由决策: 继续调用工具 %s", tool_names)
        return "tools"

    # 模型没有调用工具，自然结束
    logger.info("✅ 路由决策: LLM 自然结束（无工具调用）")
    return "end"


def _extract_text(content) -> str:
    """从 LLM 返回的 content 中提取纯文本（兼容 str / list[dict] 格式）"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)
