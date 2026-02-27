# Milestone 1：骨架搭建 + Tool Calling + 基础循环

**目标：** 从零搭通一个可运行的 LangGraph Agent，具备联网搜索 + 网页阅读能力，能围绕"AI对就业市场的影响"进行多轮自主研究，并正确结束。

**预估耗时：** 2-3 小时

---

## 前置准备

### 0.1 项目初始化

```bash
# 在 AI-job-analysis 根目录下
uv init
uv add langchain-core langgraph langchain-google-genai tavily-python httpx beautifulsoup4 pydantic
```

### 0.2 环境变量配置

创建 `.env` 文件，填入以下 API Key：

```env
GOOGLE_API_KEY=xxx           # Gemini API Key
TAVILY_API_KEY=xxx           # Tavily Search API Key（免费版每月1000次）
JINA_API_KEY=xxx             # Jina Reader API Key（可选，免费额度充足）
```

### 0.3 项目目录结构

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

| 字段 | 类型 | 用途 |
|------|------|------|
| `messages` | `Annotated[list[AnyMessage], add_messages]` | 对话历史和工具调用记录 |
| `search_count` | `int` | 已执行搜索次数（用于 M1 的强制收敛控制） |
| `max_searches` | `int` | 最大搜索次数上限（默认 5） |

2. 定义 `InputState`（对外暴露的窄接口），只包含 `messages`。

### 设计说明

- `search_count` 是一个关键的安全阀 —— 防止 Agent 陷入"搜一下 → 发现新线索 → 再搜"的无限循环。
- 使用 `dataclass(kw_only=True)` 风格（和你的 `chat-langchain` 项目保持一致）。

### 验收标准

- [ ] `AgentState` 和 `InputState` 能正常实例化
- [ ] `messages` 字段的 `add_messages` reducer 能正确合并消息

---

## Task 1.2：封装搜索与读取工具 (@tool)

**文件：** `src/agent/tools.py`

### 要做的事

1. **`search_web(query: str) -> str`**
   - 使用 `TavilyClient.search()` 搜索，返回结果摘要。
   - 每次返回 **top 5** 结果，包含 title、url、snippet。
   - 在 `docstring` 中详细描述此工具的用途（LLM 依赖 docstring 决定是否调用工具）。

2. **`read_page(url: str) -> str`**
   - **优先方案：** 使用 Jina Reader API（`https://r.jina.ai/{url}`），它能处理 JS 渲染页面并输出干净的 Markdown。
   - **降级方案：** 如果 Jina 失败，使用 `httpx` + `BeautifulSoup` 做基础提取。
   - **关键限制：** 返回的内容**截断到前 8000 字符**，避免单个网页内容撑爆 context window。
   - **错误处理：** 付费墙/403/超时等情况返回友好提示（如 `"此页面需要付费订阅，无法读取正文。请尝试搜索该报告的公开解读文章。"`），引导 Agent 搜索替代信源。

3. **`search_report_summary(report_name: str) -> str`**
   - 专门用于搜索某份权威报告（如 WEF Future of Jobs Report）的公开解读和摘要。
   - 内部调用 `TavilyClient.search(query=f"{report_name} 摘要 解读 key findings")`。
   - 这是对付费墙数据源的**降级策略**。

### 设计说明

- 使用 LangChain 的 `@tool` 装饰器。
- 每个工具的 `docstring` 要写得非常清晰，因为 Gemini/GPT 依赖这些描述来决策何时调用哪个工具。
- `read_page` 的内容截断是防止 Token 爆炸的第一道防线。

### 验收标准

- [ ] 三个工具可以独立运行，返回预期格式
- [ ] `read_page` 遇到付费墙时能优雅降级而非报错崩溃
- [ ] `search_web` 返回结果包含 URL（后续 Milestone 需要用于去重）

---

## Task 1.3：编写核心节点 (Nodes)

**文件：** `src/agent/nodes.py`

### 要做的事

1. **`call_model(state: AgentState, config: RunnableConfig) -> dict`**
   - 加载 LLM（Gemini），使用 `llm.bind_tools(tools)` 绑定工具。
   - 使用 System Prompt 定义 Agent 角色（见下方 Prompt 模板）。
   - 调用 LLM，将返回的 `AIMessage` 追加到 `messages`。

2. **`increment_search_count(state: AgentState) -> dict`**
   - 简单的计数器节点，每次工具调用后 `search_count += 1`。
   - 可以直接内嵌到条件路由逻辑中，也可以作为独立节点。

### System Prompt 初稿（M2 会深度调优）

```
你是一个专业的 AI 与就业市场研究分析师。你的任务是：

1. 搜索并收集关于 AI 技术对全球就业市场影响的最新信息
2. 重点关注以下三类岗位变化：
   - 衰退区（Red Zone）：正在被 AI 替代的岗位
   - 进化区（Yellow Zone）：工作流被 AI 重塑但不会消失的岗位
   - 新兴区（Green Zone）：因 AI 而新诞生的岗位
3. 优先搜索权威来源（WEF、McKinsey、BCG、LinkedIn 等）
4. 如果某份报告无法直接阅读（付费墙），请搜索该报告的公开解读文章

注意：你最多可以搜索 {max_searches} 次。请合理规划搜索策略，不要浪费搜索次数。
当你认为已经收集到足够的信息，请直接给出你的分析总结，不要继续搜索。
```

### 验收标准

- [ ] `call_model` 能正确接收 state、调用 LLM、返回带/不带 tool_calls 的 AIMessage
- [ ] System Prompt 中的 `{max_searches}` 能被正确替换

---

## Task 1.4：构建并编译图 (Graph) — 含条件路由

**文件：** `src/agent/graph.py`

### 要做的事

1. 初始化 `StateGraph(AgentState, input=InputState)`
2. 添加节点：
   - `"agent"` → `call_model`
   - `"tools"` → `ToolNode(tools)` （LangGraph 内置的工具执行节点）
3. 配置条件路由 — 这是 LangGraph 的核心：

```
START → agent → [条件判断] → tools → agent → [条件判断] → ... → END
```

4. **条件路由函数 `should_continue(state)`：**

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

5. 编译图时配置 `recursion_limit`：

```python
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
              │         │ (search_count++)
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

- [ ] 项目结构搭建完成，依赖全部安装
- [ ] Agent 能自主搜索 → 阅读 → 总结，并在合理的轮次内停止
- [ ] 三个工具（search_web、read_page、search_report_summary）都能正常工作
- [ ] 付费墙降级策略验证通过
- [ ] 搜索次数安全阀工作正常
