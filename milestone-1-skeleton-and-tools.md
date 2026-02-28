# Milestone 1：骨架搭建 + Tool Calling + 基础循环

**目标：** 从零搭通一个可运行的 LangGraph Agent，具备联网搜索 + 网页阅读能力，能围绕"AI对就业市场的影响"进行多轮自主研究，并正确结束。

**预估耗时：** 2-3 小时

---

## 前置准备

### 0.1 项目初始化

```bash
# 在 AI-job-analysis 根目录下
uv init
uv add langchain-core langgraph langchain-google-genai tavily-python httpx beautifulsoup4 pydantic pydantic-settings python-dotenv
```

### 0.2 环境变量配置

创建 `.env` 文件，填入以下 API Key：

```env
GOOGLE_API_KEY=xxx           # Gemini API Key
TAVILY_API_KEY=xxx           # Tavily Search API Key（免费版每月1000次）
JINA_API_KEY=xxx             # Jina Reader API Key（可选，免费额度充足）
```

### 0.3 配置管理模块

**文件：** `src/config.py`

使用 `pydantic-settings` 统一管理所有环境变量，避免 `os.getenv()` 散落各处：

```python
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """全局配置，从 .env 文件或环境变量加载"""

    # API Keys
    google_api_key: str
    tavily_api_key: str
    jina_api_key: str = ""  # 可选

    # LLM 配置
    llm_model_name: str = "gemini-2.0-flash"
    llm_temperature: float = 0.2

    # Agent 配置
    max_searches: int = 5

    # 成本控制（M5 使用，提前预留）
    max_tavily_calls: int = 15    # 单次运行 Tavily 安全上限（高于 max_searches，作为兜底）
    max_budget_usd: float = 0.50  # 单次运行费用上限（美元）

    # 数据库（M4 使用，提前预留）
    database_url: str = "sqlite:///data/job_analysis.db"

    # 通知渠道（M5 使用，提前预留）
    notification_channel: str = "console"
    feishu_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
```

### 0.4 项目目录结构

```
AI-job-analysis/
├── .env
├── pyproject.toml
├── src/
│   ├── __init__.py
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── state.py          # Task 1.1 - 全局状态定义
│   │   ├── tools.py          # Task 1.2 - 工具函数
│   │   ├── nodes.py          # Task 1.3 - 节点函数
│   │   └── graph.py          # Task 1.4 - 图构建与编译
│   └── config.py             # 配置管理（环境变量加载）
├── run_agent.py              # 临时入口脚本（用于测试）
└── milestone-*.md            # 里程碑计划文档
```

---

## Task 1.1：定义全局状态 (State)

**文件：** `src/agent/state.py`

### 要做的事

1. 使用 `dataclass` + `Annotated` 定义 `AgentState`，包含以下字段：

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `messages` | `Annotated[list[AnyMessage], add_messages]` | （必填） | 对话历史和工具调用记录 |
| `search_count` | `int` | `0` | 已执行 Tavily 搜索类调用次数（`search_web` + `search_report_summary`，用于 M1 的强制收敛控制） |
| `max_searches` | `int` | `5` | 最大搜索次数上限（从 `config.py` 读取默认值） |

完整代码（后续 Milestone 会在此基础上新增字段）：

```python
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
```

### 设计说明

- `search_count` 是一个关键的安全阀 —— 防止 Agent 陷入"搜一下 → 发现新线索 → 再搜"的无限循环。
- **`search_count` 统计所有 Tavily 搜索类调用**（`search_web` + `search_report_summary`），不统计 `read_page`（阅读网页不应消耗搜索配额）。
- 使用 `dataclass(kw_only=True)` 风格（和你的 `chat-langchain` 项目保持一致）。

### 验收标准

- [ ] `AgentState` 和 `InputState` 能正常实例化
- [ ] `messages` 字段的 `add_messages` reducer 能正确合并消息
- [ ] `search_count` 默认为 0，`max_searches` 默认为 5

---

## Task 1.2：封装搜索与读取工具 (@tool)

**文件：** `src/agent/tools.py`

### 要做的事

> **关键设计决策：** 所有 `@tool` 函数的返回类型为 `-> str`，因为 LangChain 的 `@tool` 最终会将返回值包装为 `ToolMessage.content`（字符串）。工具内部构造 dict 后通过 `json.dumps()` 序列化为 JSON 字符串返回，下游节点需要结构化数据时用 `json.loads()` 反序列化。

**工具模块顶部初始化**（在 `tools.py` 开头统一初始化 Tavily 客户端和 Jina 配置）：

```python
import json
import httpx
from langchain_core.tools import tool
from src.config import get_settings
from tavily import TavilyClient

settings = get_settings()
tavily_client = TavilyClient(api_key=settings.tavily_api_key)

# Jina Reader API 前缀（免费额度充足，优先使用）
JINA_READER_PREFIX = "https://r.jina.ai/"
JINA_HEADERS = {"Authorization": f"Bearer {settings.jina_api_key}"} if settings.jina_api_key else {}
```

1. **`search_web(query: str) -> str`**
   - 使用 `TavilyClient.search()` 搜索，返回 JSON 字符串。
   - 每次返回 **top 5** 结果，包含 `title`、`url`、`snippet`、`score`（如有）。
   - 建议返回格式（序列化为 JSON 字符串）：
     ```python
     {
       "query": "...",
       "results": [
         {"title": "...", "url": "...", "snippet": "...", "score": 0.91}
       ],
       "result_count": 5,
       "error": None
     }
     ```
   - 在 `docstring` 中详细描述此工具的用途（LLM 依赖 docstring 决定是否调用工具）。
   - 使用 `async def`，与 `read_page` 保持异步一致性。

   完整实现（M2 Task 2.4 会确认此错误处理模式，M4 会增加去重 + `asyncio.to_thread()` 包装）：

   ```python
   @tool
   async def search_web(query: str) -> str:
       """搜索网页获取最新信息。返回 JSON 格式的搜索结果列表，包含标题、URL、摘要和相关度评分。
       适用于搜索关于 AI 对就业市场影响的报告、数据和新闻。"""
       try:
           # 注意：Tavily SDK 是同步调用，M4 会用 asyncio.to_thread() 包装避免阻塞事件循环
           results = tavily_client.search(query=query, max_results=5)
           output = {
               "query": query,
               "results": [
                   {"title": r["title"], "url": r["url"], "snippet": r.get("content", ""), "score": r.get("score")}
                   for r in results.get("results", [])
               ],
               "result_count": len(results.get("results", [])),
               "error": None,
           }
           return json.dumps(output, ensure_ascii=False)
       except Exception as e:
           return json.dumps({"query": query, "results": [], "result_count": 0, "error": str(e)}, ensure_ascii=False)
   ```

2. **`read_page(url: str) -> str`**
   - **优先方案：** 使用 Jina Reader API（`https://r.jina.ai/{url}`），它能处理 JS 渲染页面并输出干净的 Markdown。
   - **降级方案：** 如果 Jina 失败，使用 `httpx` + `BeautifulSoup` 做基础提取。
   - **关键限制：** 返回的内容**截断到前 8000 字符**，避免单个网页内容撑爆 context window。
   - **错误处理：** 付费墙/403/超时等情况返回结构化状态 JSON，而不是直接抛异常。
   - **必须使用 `async def`**（做 HTTP 请求），搭配 `httpx.AsyncClient`。
   - 建议返回格式（序列化为 JSON 字符串）：
     ```python
     {
       "url": "...",
       "status": "ok|paywalled|timeout|forbidden|error",
       "content": "...",   # status=ok 时提供，且已截断
       "error": "...",     # status!=ok 时提供
       "truncated": True
     }
     ```

   完整实现（Jina 优先 → httpx+BS4 降级 → 结构化错误返回）：

   ```python
   @tool
   async def read_page(url: str) -> str:
       """阅读指定 URL 的网页内容。优先使用 Jina Reader API 获取干净的 Markdown，
       如果 Jina 失败则降级为 httpx + BeautifulSoup 基础提取。
       返回 JSON 字符串，包含 status 和 content 字段。内容截断到前 8000 字符。"""
       # ── 方案 1：Jina Reader API（处理 JS 渲染，输出干净 Markdown）──
       try:
           async with httpx.AsyncClient(timeout=10.0) as client:
               jina_url = f"{JINA_READER_PREFIX}{url}"
               resp = await client.get(jina_url, headers=JINA_HEADERS)
               if resp.status_code == 200 and resp.text.strip():
                   content = resp.text[:8000]
                   return json.dumps({
                       "url": url, "status": "ok", "content": content,
                       "error": None, "truncated": len(resp.text) > 8000,
                   }, ensure_ascii=False)
       except httpx.TimeoutException:
           pass  # Jina 超时，降级到方案 2
       except Exception:
           pass  # Jina 其他错误，降级

       # ── 方案 2：httpx + BeautifulSoup 基础提取 ──
       try:
           from bs4 import BeautifulSoup

           async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
               resp = await client.get(url)

               if resp.status_code == 403:
                   return json.dumps({
                       "url": url, "status": "forbidden",
                       "content": None, "error": "Access forbidden (403)",
                       "truncated": False,
                   }, ensure_ascii=False)

               if resp.status_code in {401, 402}:
                   return json.dumps({
                       "url": url, "status": "paywalled",
                       "content": None, "error": f"Paywalled ({resp.status_code})",
                       "truncated": False,
                   }, ensure_ascii=False)

               resp.raise_for_status()

               soup = BeautifulSoup(resp.text, "html.parser")
               # 移除干扰元素
               for tag in soup(["script", "style", "nav", "footer", "header"]):
                   tag.decompose()
               text = soup.get_text(separator="\n", strip=True)
               content = text[:8000]
               return json.dumps({
                   "url": url, "status": "ok", "content": content,
                   "error": None, "truncated": len(text) > 8000,
               }, ensure_ascii=False)

       except httpx.TimeoutException:
           return json.dumps({
               "url": url, "status": "timeout",
               "content": None, "error": "Request timed out (10s)",
               "truncated": False,
           }, ensure_ascii=False)
       except Exception as e:
           return json.dumps({
               "url": url, "status": "error",
               "content": None, "error": str(e),
               "truncated": False,
           }, ensure_ascii=False)
   ```

   > **M4 重构提示：** M4 会将上述核心获取逻辑提取为 `_fetch_page_content(url) -> dict` 内部 helper（返回 dict 而非 JSON 字符串），外层 `read_page` 增加去重检查后再调用 helper，最后 `json.dumps(result)` 返回。

3. **`search_report_summary(report_name: str) -> str`**
   - 专门用于搜索某份权威报告（如 WEF Future of Jobs Report）的公开解读和摘要。
   - 内部调用 `TavilyClient.search(query=f"{report_name} 摘要 解读 key findings")`。
   - 这是对付费墙数据源的**降级策略**。
   - 使用 `async def`，与 `search_web` / `read_page` 保持异步一致性（M4 会用 `asyncio.to_thread()` 包装同步 Tavily 调用）。

   完整实现（M4 会增加去重 + `asyncio.to_thread()` 包装）：

   ```python
   @tool
   async def search_report_summary(report_name: str) -> str:
       """搜索某份权威报告的公开解读和摘要。当报告原文无法直接阅读（付费墙）时，
       使用此工具搜索该报告的公开解读文章。返回 JSON 格式的搜索结果。"""
       try:
           query = f"{report_name} 摘要 解读 key findings"
           results = tavily_client.search(query=query, max_results=5)
           output = {
               "query": query,
               "results": [
                   {"title": r["title"], "url": r["url"], "snippet": r.get("content", ""), "score": r.get("score")}
                   for r in results.get("results", [])
               ],
               "result_count": len(results.get("results", [])),
               "error": None,
           }
           return json.dumps(output, ensure_ascii=False)
       except Exception as e:
           return json.dumps({"query": report_name, "results": [], "result_count": 0, "error": str(e)}, ensure_ascii=False)
   ```

4. **导出工具列表**（供 `graph.py` 中的 `ToolNode` 和后续 Milestone 使用）：

```python
# 在 tools.py 末尾导出工具列表
tools = [search_web, read_page, search_report_summary]
```

### 设计说明

- 使用 LangChain 的 `@tool` 装饰器。
- 每个工具的 `docstring` 要写得非常清晰，因为 Gemini/GPT 依赖这些描述来决策何时调用哪个工具。
- `read_page` 的内容截断是防止 Token 爆炸的第一道防线。
- 工具返回 JSON 字符串，下游节点 `json.loads()` 即可程序化消费，无需正则解析。

### 验收标准

- [ ] 三个工具可以独立运行，返回预期格式的 JSON 字符串
- [ ] `read_page` 遇到付费墙时能优雅降级而非报错崩溃
- [ ] `search_web` 返回结果包含 URL（后续 Milestone 需要用于去重）
- [ ] 三个工具输出可通过 `json.loads()` 解析为 dict，无需正则解析

---

## Task 1.3：编写核心节点 (Nodes)

**文件：** `src/agent/nodes.py`

### 要做的事

1. **`call_model(state: AgentState, config: RunnableConfig) -> dict`**
   - 从 `config.py` 加载 LLM 配置，使用 `llm.bind_tools(tools)` 绑定工具。
   - 使用 System Prompt 定义 Agent 角色（见下方 Prompt 模板）。
   - 调用 LLM，将返回的 `AIMessage` 追加到 `messages`。

   ```python
   from typing import Literal
   from langchain_core.runnables import RunnableConfig
   from langchain_core.messages import AIMessage
   from langchain_google_genai import ChatGoogleGenerativeAI
   from src.config import get_settings
   from src.agent.state import AgentState
   from src.agent.tools import tools

   # System Prompt 初稿（M2 会提取到 prompts.py 并深度调优）
   _SYSTEM_PROMPT = """你是一个专业的 AI 与就业市场研究分析师。你的任务是：

   1. 搜索并收集关于 AI 技术对全球就业市场影响的最新信息
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
       llm = ChatGoogleGenerativeAI(
           model=settings.llm_model_name,
           temperature=settings.llm_temperature,
       ).bind_tools(tools)

       messages = [
           {"role": "system", "content": _SYSTEM_PROMPT.format(max_searches=state.max_searches)},
           *state.messages,
       ]

       response = await llm.ainvoke(messages)
       return {"messages": [response]}
   ```

2. **`count_search_calls(state: AgentState) -> dict`**
   - 检查最近一次 `ToolMessage` 列表中有多少个 Tavily 搜索类调用（`search_web` / `search_report_summary`）。
   - `read_page` **不计入搜索次数**，因为阅读网页不应消耗搜索配额。

   ```python
   def count_search_calls(state: AgentState) -> dict:
       """对 Tavily 搜索类工具调用计数，read_page 不消耗搜索配额"""
       # 从最新的 AIMessage 中统计搜索类工具的调用次数
       last_ai_msg = next(
           (m for m in reversed(state.messages) if isinstance(m, AIMessage) and m.tool_calls),
           None
       )
       if last_ai_msg is None:
           return {}
       
       search_calls = sum(
           1 for tc in last_ai_msg.tool_calls
           if tc["name"] in {"search_web", "search_report_summary"}
       )
       return {"search_count": state.search_count + search_calls}
   ```

3. **`should_continue(state: AgentState) -> Literal["tools", "end"]`**
   - 条件路由函数：决定 Agent 是继续调用工具还是结束。
   - 检查搜索次数是否超出上限（安全阀），以及 LLM 是否请求了工具调用。
   - 虽然本质是路由函数（返回字符串），但因操作 `AgentState` 逻辑较重，统一放在 `nodes.py` 管理（M2 图构建时直接 `from src.agent.nodes import should_continue`）。

   ```python
   def should_continue(state: AgentState) -> Literal["tools", "end"]:
       last_message = state.messages[-1]
       
       # 安全阀：超过最大搜索次数，强制结束
       if state.search_count >= state.max_searches:
           return "end"
       
       # 模型决定调用工具
       if hasattr(last_message, "tool_calls") and last_message.tool_calls:
           return "tools"
       
       # 模型没有调用工具，自然结束
       return "end"
   ```

> **注意：** System Prompt 初稿已嵌入到上面的 `call_model` 代码中作为 `_SYSTEM_PROMPT` 常量。M2 会将其提取到 `src/agent/prompts.py` 并深度调优（增加信源排序、收敛条件、付费墙策略等）。

### 验收标准

- [ ] `call_model` 能正确接收 state、调用 LLM、返回带/不带 tool_calls 的 AIMessage
- [ ] System Prompt 中的 `{max_searches}` 能被正确替换
- [ ] `count_search_calls` 只统计 `search_web` / `search_report_summary`，`read_page` 不计入
- [ ] `should_continue` 能正确区分工具调用 / 自然结束 / 搜索预算耗尽三种情况

---

## Task 1.4：构建并编译图 (Graph) — 含条件路由

**文件：** `src/agent/graph.py`

### 要做的事

1. 初始化 `StateGraph(AgentState, input=InputState)`
2. 添加节点：
   - `"agent"` → `call_model`
   - `"tools"` → `ToolNode(tools)` （LangGraph 内置的工具执行节点）
   - `"count_search_calls"` → `count_search_calls`
3. 配置条件路由 — 这是 LangGraph 的核心：

```
START → agent → [条件判断] → tools → count_search_calls → agent → ... → END
```

4. **条件路由函数 `should_continue`**：已在 Task 1.3 的 `nodes.py` 中定义，此处直接导入使用。

5. 完整的 builder 代码：

```python
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
# 运行时传入 config：{"recursion_limit": 30}
```

### 完整的图结构（文字描述）

```
                    ┌──────────────────────┐
                    │       START          │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
              ┌────▶│    agent (LLM)       │
              │     └──────────┬───────────┘
              │                ▼
              │     ┌──────────────────────┐
              │     │   should_continue?   │
              │     └───┬──────────────┬───┘
              │         ▼              ▼
              │  ┌────────────┐  ┌──────────┐
              │  │   tools    │  │   END    │
              │  └──────┬─────┘  └──────────┘
              │         ▼
              │  ┌─────────────────┐
              │  │ count_search_   │
              │  │ calls           │
              │  └──────┬──────────┘
              └─────────┘
```

### 验收标准

- [ ] 输入一个简单的 Prompt（如"搜索最新的 AI 就业影响报告"），Agent 能自主搜索 1-3 次后停止并输出总结
- [ ] 当 `search_count` 达到上限时，Agent 能被强制收敛而非报错
- [ ] `recursion_limit` 被正确配置，不会出现 `GraphRecursionError`

---

## Task 1.5：编写测试入口脚本

**文件：** `run_agent.py`

### 要做的事

```python
# 简单的入口脚本
import asyncio
from dotenv import load_dotenv
from src.agent.graph import graph

load_dotenv()

async def main():
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "搜索近一周内关于 AI 替代和新增岗位的中英文报告，给我一个简要总结。"}]},
        config={"recursion_limit": 30}
    )
    # 打印最终消息
    print(result["messages"][-1].content)

asyncio.run(main())
```

### 验收标准

- [ ] 脚本端到端运行通过，能看到 Agent 的搜索过程和最终输出
- [ ] 控制台输出中能看到 Agent 调用了 `search_web` 和/或 `read_page`

---

## Milestone 1 完成标志 ✅

- [ ] 项目结构搭建完成，依赖全部安装，`config.py` 能正确加载环境变量
- [ ] Agent 能自主搜索 → 阅读 → 总结，并在合理的轮次内停止
- [ ] 三个工具（search_web、read_page、search_report_summary）都能正常工作
- [ ] 付费墙降级策略验证通过
- [ ] 搜索次数安全阀工作正常（统计所有 Tavily 搜索类调用）
