# Milestone 2：Prompt 调优 + 行为校准 + 错误处理

**目标：** 让 Agent 的搜索行为从"能跑"变成"跑得好"—— 搜索有策略、收敛有纪律、异常有兜底。这个阶段是 Agent 质量的分水岭。

**前置依赖：** Milestone 1 完成

**预估耗时：** 3-4 小时

---

## Task 2.1：设计研究计划 Prompt（Planning Prompt）

**文件：** `src/agent/prompts.py`（新建）

### 要做的事

Agent 不应该上来就"乱搜"，而应该先制定一个研究计划。参考你 `chat-langchain` 项目中 `create_research_plan` 的模式：

1. **新增 `create_research_plan` 节点**，在 Agent 开始搜索之前运行。

2. 定义 Planning Prompt（Python 常量，在 `prompts.py` 中）：

```python
PLANNING_PROMPT = """你是一个专业的 AI 与就业市场研究规划师。

根据用户的研究需求，请制定一个不超过 {max_searches} 步的搜索计划。每一步是一个具体的搜索查询。

你的搜索计划应该覆盖以下维度（按优先级排列）：
1. 权威报告搜索：搜索 WEF、McKinsey、BCG 等发布的关于 AI 对就业影响的报告
2. 招聘市场数据：搜索 LinkedIn、Indeed 等平台关于 AI 相关岗位增长的数据
3. 技术前沿动态：搜索近期 AI 创业公司融资、新产品发布等信号

注意事项：
- 每个搜索查询要精确、具体，避免过于宽泛
- 中英文查询各占一半（中文市场和全球市场都要覆盖）
- 优先搜索最近 3 个月内的信息
- 总共不超过 {max_searches} 个搜索步骤
- 每个搜索步骤请标注所属维度（macro / job_market / tech_frontier），方便后续分维度执行"""
```

> **注意：** 此处不要在 prompt 里要求"以 JSON 格式输出"，因为下面已使用 `with_structured_output` 强制 Schema 约束，两者同时指定会导致输出被 double-wrapped。

3. 使用 `with_structured_output` 确保输出为结构化的 `ResearchPlan` 对象。
   为避免跨文件循环导入，建议将 `ResearchStep` / `ResearchPlan` 放到共享类型模块（如 `src/agent/types.py`），由 `prompts.py`、`state.py`、`nodes.py` 共同引用：

```python
from typing import TypedDict


class ResearchStep(TypedDict):
    query: str           # 搜索查询
    dimension: str       # 所属维度：macro / job_market / tech_frontier

class ResearchPlan(TypedDict):
    steps: list[ResearchStep]
```

4. `create_research_plan` 节点需要将 LLM 输出的 `ResearchPlan.steps` 映射到 `AgentState.plan_steps`，并重置执行游标。

   > **⭐ nodes.py import 更新：** M2 在 M1 的 import 基础上新增以下依赖（`asyncio` 用于重试退避，`SYSTEM_PROMPT`/`PLANNING_PROMPT` 从新建的 `prompts.py` 导入，`ResearchPlan` 从 `types.py` 导入）：

   ```python
   # ---- src/agent/nodes.py M2 完整 import 列表 ----
   import asyncio                          # ⭐ M2 新增：指数退避重试 await asyncio.sleep()
   from typing import Literal
   from langchain_core.runnables import RunnableConfig
   from langchain_core.messages import AIMessage
   from langchain_google_genai import ChatGoogleGenerativeAI
   from src.config import get_settings
   from src.agent.state import AgentState
   from src.agent.tools import tools
   from src.agent.prompts import SYSTEM_PROMPT, PLANNING_PROMPT  # ⭐ M2 新增
   from src.agent.types import ResearchPlan                      # ⭐ M2 新增
   ```

```python
async def create_research_plan(state: AgentState, config: RunnableConfig) -> dict:
    """生成研究计划并写入 state.plan_steps"""
    settings = get_settings()
    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    ).with_structured_output(ResearchPlan)
    
    plan = await model.ainvoke([
        {"role": "system", "content": PLANNING_PROMPT.format(max_searches=state.max_searches)},
        *state.messages,
    ])
    
    # ⭐ 关键映射：LLM 输出的 plan["steps"] → AgentState.plan_steps
    # 同时重置执行游标，避免二次规划时沿用旧状态导致跳步/越界
    return {
        "plan_steps": plan["steps"],
        "step_index": 0,
        "current_step": None,
    }
```

### 设计说明

- 让 LLM 先规划再执行，能**大幅减少无效搜索**，节省 API 调用费用。
- 限制步骤数 = 限制 Token 消耗，双重保险。
- 每步标注 `dimension`，为 M3 子图分发做准备（M3 会按维度将 steps 分发到不同子图执行）。

### 验收标准

- [ ] Agent 能根据用户 query 生成 3-5 步的搜索计划
- [ ] 搜索计划覆盖中英文双语查询
- [ ] 搜索计划覆盖至少 2 个维度（报告、招聘数据、技术动态）
- [ ] 每个步骤都有 `dimension` 标注

---

## Task 2.2：调优 Agent 的搜索行为

**文件：** `src/agent/prompts.py`、`src/agent/nodes.py`

### 要做的事

1. **信源优先级指令**：在 System Prompt 中明确信源的可信度排序：

```
信源可信度排序（从高到低）：
- 一级来源：WEF、OECD、ILO 等国际组织的官方报告
- 二级来源：McKinsey、BCG、PwC、Gartner 等咨询公司的研究
- 三级来源：LinkedIn、Indeed 等招聘平台的官方数据洞察
- 四级来源：科技媒体（36氪、机器之心、TechCrunch）对上述报告的解读
- 五级来源：个人博客、社交媒体观点（仅作参考，不作为主要依据）

你应该优先使用更高级别的来源。在引用信息时，请始终标注信息来源。
```

2. **搜索收敛条件**：明确告诉 Agent 何时应该停止搜索：

```
当满足以下任一条件时，你应该停止当前步骤内的额外搜索并产出阶段性总结：
- 你已经从至少 3 个不同信源获取了有效信息
- 你已经用完所有搜索步骤
- 新搜索的结果与已有信息高度重复
- 你已经覆盖了"衰退区"、"进化区"、"新兴区"三个分类的基本信息
```

3. **付费墙应对策略**（强化 M1 的降级逻辑）：

```
如果调用 `read_page` 后返回的 JSON 中 `status` 为 paywalled/forbidden/timeout：
1. 不要再次尝试阅读同一个 URL
2. 使用 search_report_summary 工具搜索该报告的公开摘要
3. 如果仍然找不到，跳过此来源，继续下一个搜索步骤
```

4. **组装 `SYSTEM_PROMPT` 常量**（在 `src/agent/prompts.py` 中）：

将 M1 的 System Prompt 初稿与上面三段行为指令合并为一个 `SYSTEM_PROMPT` 字符串常量，使用 `{max_searches}` 模板变量：

```python
SYSTEM_PROMPT = """你是一个专业的 AI 与就业市场研究分析师。你的任务是：

1. 搜索并收集关于 AI 技术对全球就业市场影响的最新信息
2. 重点关注以下三类岗位变化：
   - 衰退区（Red Zone）：正在被 AI 替代的岗位
   - 进化区（Yellow Zone）：工作流被 AI 重塑但不会消失的岗位
   - 新兴区（Green Zone）：因 AI 而新诞生的岗位
3. 优先搜索权威来源（WEF、McKinsey、BCG、LinkedIn 等）
4. 如果某份报告无法直接阅读（付费墙），请搜索该报告的公开解读文章

信源可信度排序（从高到低）：
- 一级来源：WEF、OECD、ILO 等国际组织的官方报告
- 二级来源：McKinsey、BCG、PwC、Gartner 等咨询公司的研究
- 三级来源：LinkedIn、Indeed 等招聘平台的官方数据洞察
- 四级来源：科技媒体（36氪、机器之心、TechCrunch）对上述报告的解读
- 五级来源：个人博客、社交媒体观点（仅作参考，不作为主要依据）

你应该优先使用更高级别的来源。在引用信息时，请始终标注信息来源。

当满足以下任一条件时，你应该停止当前步骤内的额外搜索并产出阶段性总结：
- 你已经从至少 3 个不同信源获取了有效信息
- 你已经用完所有搜索步骤
- 新搜索的结果与已有信息高度重复
- 你已经覆盖了"衰退区"、"进化区"、"新兴区"三个分类的基本信息

如果调用 `read_page` 后返回的 JSON 中 `status` 为 paywalled/forbidden/timeout：
1. 不要再次尝试阅读同一个 URL
2. 使用 search_report_summary 工具搜索该报告的公开摘要
3. 如果仍然找不到，跳过此来源，继续下一个搜索步骤

注意：你最多可以搜索 {max_searches} 次。请合理规划搜索策略，不要浪费搜索次数。
当你认为已经收集到足够的信息，请直接给出你的分析总结，不要继续搜索。"""
```

### 验收标准

- [ ] Agent 不再无目的地重复搜索相似内容
- [ ] Agent 在遇到付费墙时能自动切换策略
- [ ] Agent 能在 3-5 轮搜索内收敛并给出有信息量的总结
- [ ] 每次搜索的 query 明显不同（覆盖不同维度）

---

## Task 2.3：修改图结构 — 引入 Planning 阶段

**文件：** `src/agent/nodes.py`（新增 `conduct_research`、`check_plan_finished`）、`src/agent/graph.py`（重构图结构）

### 要做的事

将图结构从 M1 的简单循环改为 **"计划 → 执行 → 总结"** 三阶段：

```
                    ┌──────────────────────┐
                    │       START          │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │  create_research_plan │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
              ┌────▶│  conduct_research    │
              │     │  (取出当前步骤,       │
              │     │   推进 step_index)    │
              │     └──────────┬───────────┘
              │                ▼
              │     ┌──────────────────────┐
              │     │  agent (call_model)   │
              │     └──────────┬───────────┘
              │                ▼
              │     ┌──────────────────────┐
              │     │  should_continue?    │
              │     └───┬─────────────┬────┘
              │  "tools"│             │"end"
              │         ▼             │
              │  ┌────────────┐       │
              │  │   tools    │       │
              │  └──────┬─────┘       │
              │         ▼             │
              │  ┌────────────────┐   │
              │  │count_search_   │   │
              │  │  calls         │   │
              │  └──────┬─────────┘   │
              │         │             │
              │         ▼             ▼
              │     ┌──────────────────────┐
              │     │  check_plan_finished │
              │     │  (步骤/预算检查)      │
              │     └───┬─────────────┬────┘
              │  (有步骤)│        (无 / │
              └─────────┘     预算耗尽) │
                                       ▼
                               ┌────────────┐
                               │    END     │
                               └────────────┘
```

> **图解说明：**
> - `should_continue` 有两条出边：`"tools"` → 调用工具 → 计数 → 检查计划；`"end"` → 直接检查计划
> - `count_search_calls` 统计所有 Tavily 搜索类调用（`search_web` + `search_report_summary`，复用 M1 逻辑）
> - `check_plan_finished` 同时判断 `step_index < len(plan_steps)` 和 `search_count < max_searches`，任一不满足即收敛到 END

### 关键逻辑

> **重点改动：** 用 `step_index` 替代 `steps.pop()`，避免 LangGraph 的 state mutation 问题。LangGraph 的 state 更新是通过返回 dict 合并实现的，不支持 in-place list mutation（如 pop/remove）。
> 并统一使用 `plan_steps: list[ResearchStep]` 作为计划字段，避免与 M3 的字段命名冲突。
> `check_plan_finished` 依赖 `search_count` 预算判断，因此图中必须显式加入计数节点链路：`tools -> count_search_calls -> check_plan_finished`（`count_search_calls` 可复用 M1 实现并统计 Tavily 搜索类调用）。

```python
def check_plan_finished(state: AgentState) -> Literal["conduct_research", "end"]:
    """检查研究计划是否还有未执行的步骤（同时考虑搜索预算）"""
    # ⭐ 搜索预算耗尽时，无论剩余步骤都直接收敛，避免 conduct_research ↔ check_plan 死循环
    if state.search_count >= state.max_searches:
        return "end"
    if state.step_index < len(state.plan_steps):
        return "conduct_research"
    return "end"


def conduct_research(state: AgentState) -> dict:
    """取出当前步骤的搜索查询，推进 step_index"""
    current_step = state.plan_steps[state.step_index]
    return {
        "current_step": current_step,
        "step_index": state.step_index + 1,
    }

# graph routing (required)
# ... -> tools -> count_search_calls -> check_plan_finished
```

### `call_model` 如何消费 `current_step`

`conduct_research` 将当前步骤写入 `state.current_step` 后，下游的 `call_model`（agent 节点）需要将其注入 prompt，让 LLM 知道"这一轮该搜什么"：

```python
async def call_model(state: AgentState, config: RunnableConfig) -> dict:
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    ).bind_tools(tools)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(max_searches=state.max_searches)},
        *state.messages,
    ]
    
    # ⭐ 如果有当前步骤，将搜索指令注入为用户消息，引导 LLM 调用对应工具
    if state.current_step:
        step_instruction = (
            f"请执行以下搜索步骤：{state.current_step['query']}\n"
            f"（维度：{state.current_step['dimension']}）"
        )
        messages.append({"role": "user", "content": step_instruction})
    
    # ... 重试逻辑同 Task 2.4
    response = await llm.ainvoke(messages)
    return {"messages": [response]}
```

### 图构建代码

> **注意：** M2 在 M1 的图基础上做了重大重构：入口改为 `create_research_plan`，执行阶段由 `conduct_research` 驱动。M1 的 `agent → should_continue → tools → count_search_calls → agent` 循环被嵌入到更大的 `conduct_research → agent → ... → check_plan_finished` 外层循环中。

```python
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from src.agent.state import AgentState, InputState
from src.agent.tools import tools   # [search_web, read_page, search_report_summary]
from src.agent.nodes import (
    create_research_plan,
    conduct_research,
    call_model,
    count_search_calls,
    should_continue,
    check_plan_finished,
)

# ⭐ check_plan_finished 是路由函数（返回 str），不是节点函数（返回 dict）。
# 因此需要一个空节点作为路由起点，再用 add_conditional_edges 挂载路由逻辑。
def _pass_through(state: AgentState) -> dict:
    """空节点：仅作为条件路由的挂载点，不修改 state。"""
    return {}

builder = StateGraph(AgentState, input=InputState)

# 注册节点
builder.add_node("create_research_plan", create_research_plan)
builder.add_node("conduct_research", conduct_research)
builder.add_node("agent", call_model)
builder.add_node("tools", ToolNode(tools))
builder.add_node("count_search_calls", count_search_calls)
builder.add_node("check_plan_finished", _pass_through)  # 空节点，路由逻辑在 conditional_edges 中

# 设置入口
builder.set_entry_point("create_research_plan")

# 计划 → 第一次执行
builder.add_edge("create_research_plan", "conduct_research")

# conduct_research → agent（注入当前步骤后调用 LLM）
builder.add_edge("conduct_research", "agent")

# agent → should_continue 条件路由
builder.add_conditional_edges("agent", should_continue, {
    "tools": "tools",
    "end": "check_plan_finished",       # LLM 未调用工具 → 检查是否还有下一步
})

# tools → count_search_calls → check_plan_finished
builder.add_edge("tools", "count_search_calls")
builder.add_edge("count_search_calls", "check_plan_finished")

# check_plan_finished → 条件路由：有剩余步骤则继续，否则结束
builder.add_conditional_edges("check_plan_finished", check_plan_finished, {
    "conduct_research": "conduct_research",
    "end": END,
})

graph = builder.compile()
# 运行时传入 config：{"recursion_limit": 50}
```

> **⚠️ LangGraph 条件路由要点：** `add_conditional_edges(source_node, routing_fn, path_map)` 中的 `source_node` 必须是已注册的节点（返回 `dict` 的函数），而 `routing_fn` 是路由决策函数（返回 `str`）。两者不能是同一个函数。因此 `check_plan_finished` 路由函数需要一个 `_pass_through` 空节点作为挂载点。

### 在 AgentState 中新增字段

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `plan_steps` | `list[ResearchStep]` | `[]` | 研究计划步骤（由 `create_research_plan` 填充，含 `query` + `dimension`） |
| `step_index` | `int` | `0` | 当前执行到第几步（通过递增推进，避免 list.pop 的 mutation 问题） |
| `current_step` | `ResearchStep \| None` | `None` | 当前正在执行的步骤 |

### M2 更新后的完整 `state.py`

> **⭐ 关键：** 以下是 M2 阶段 `src/agent/state.py` 的完整代码。在 M1 的基础上新增了 `plan_steps`、`step_index`、`current_step` 三个字段，并导入 `ResearchStep` 类型。

```python
from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from src.config import get_settings
from src.agent.types import ResearchStep  # ⭐ M2 新增


@dataclass(kw_only=True)
class AgentState:
    """Agent 全局状态（M2 版本：M1 基础 + 计划执行字段）"""

    # ── M1 基础字段 ──
    messages: Annotated[list[AnyMessage], add_messages]
    search_count: int = 0
    max_searches: int = field(default_factory=lambda: get_settings().max_searches)

    # ── M2 新增：研究计划执行字段 ──
    plan_steps: list[ResearchStep] = field(default_factory=list)  # create_research_plan 填充
    step_index: int = 0                                            # 当前执行到第几步
    current_step: ResearchStep | None = None                       # 当前正在执行的步骤


@dataclass(kw_only=True)
class InputState:
    """对外暴露的窄接口，用于 graph.ainvoke() 的输入"""
    messages: Annotated[list[AnyMessage], add_messages]
```

### 验收标准

- [ ] Agent 先输出研究计划，再按步骤执行搜索
- [ ] 每完成一步，`step_index` 自动递增
- [ ] 所有步骤执行完毕后，Agent 自动收敛到总结阶段
- [ ] 不使用 list.pop() 等 in-place mutation 操作
- [ ] 计划状态字段统一为 `plan_steps`，与 M3 保持一致
- [ ] 图中包含 `tools -> count_search_calls -> check_plan_finished` 链路，预算判断可生效

---

## Task 2.4：全局错误处理与容错机制

**文件：** `src/agent/tools.py`、`src/agent/nodes.py`

### 要做的事

1. **工具级别的错误处理**：

```python
@tool
async def search_web(query: str) -> str:
    """搜索网页获取最新信息。返回 JSON 格式的搜索结果列表，包含标题、URL、摘要和相关度评分。"""
    try:
        # 注意：Tavily SDK 是同步调用，M4 会用 asyncio.to_thread() 包装避免阻塞事件循环
        results = tavily_client.search(query=query, max_results=5)
        output = {
            "query": query,
            "results": [
                {"title": r["title"], "url": r["url"], "snippet": r["content"], "score": r.get("score")}
                for r in results.get("results", [])
            ],
            "result_count": len(results.get("results", [])),
            "error": None,
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"query": query, "results": [], "result_count": 0, "error": str(e)}, ensure_ascii=False)
```

2. **节点级别的错误处理**（指数退避重试）：

```python
async def call_model(state: AgentState, config: RunnableConfig) -> dict:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)
            return {"messages": [response]}
        except Exception as e:
            if attempt == max_retries - 1:
                # 最后一次失败时返回错误消息，而非让整个 Agent 崩溃
                error_msg = AIMessage(content=f"模型调用失败（已重试 {max_retries} 次）：{str(e)}")
                return {"messages": [error_msg]}
            # 指数退避：1s, 2s, 4s
            await asyncio.sleep(2 ** attempt)
```

3. **全局 fallback**：在 `run_agent.py` 中包裹顶层异常处理。

4. **超时控制**：为 `read_page` 设置 10 秒超时。

```python
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.get(url)
```

### 验收标准

- [ ] Tavily API 故障时工具返回结构化错误 JSON 而非崩溃
- [ ] `read_page` 超时时能优雅返回 `{"status": "timeout", ...}` 错误信息
- [ ] LLM API 偶发故障时使用指数退避重试
- [ ] 整个图的运行在任何异常情况下都不会抛未处理的异常

---

## Task 2.5：准备 Golden Test Cases

**文件：** `tests/test_golden_cases.py`（新建）

### 要做的事

准备 2-3 个标准测试用例，用于后续 Milestone 每次修改后的回归验证：

1. **Case 1 - 基础搜索能力**
   - 输入：`"搜索 2025 年 WEF 未来就业报告的关键发现"`
   - 预期：输出应包含至少 3 个具体的岗位名称，并附带来源

2. **Case 2 - 付费墙降级**
   - 输入：`"阅读并总结 Gartner 2025 年 AI 技术成熟度报告"`
   - 预期：Agent 应该在直接阅读失败后，转而搜索公开解读文章

3. **Case 3 - 搜索收敛**
   - 输入：`"全面分析 AI 对就业市场的影响，包括衰退、进化和新兴岗位"`
   - 预期：Agent 应该在 max_searches 次内完成，不出现 RecursionError

4. **Case 4 - 工具契约稳定性**
   - 输入：任意搜索 query
   - 预期：`search_web` 返回的 JSON 字符串可被 `json.loads()` 解析，包含 `results` 列表且元素含 `url` 字段；`read_page` 返回的 JSON 包含 `status` 和 `content/error` 字段

5. **Case 5 - 离线 Mock 测试（CI 友好）**
   - 使用 `unittest.mock.patch` 替换外部 API 调用，确保 CI 不依赖外部服务：

   ```python
   from unittest.mock import AsyncMock, patch
   import json

   @patch("src.agent.tools.tavily_client")
   async def test_search_web_offline(mock_tavily):
       """离线测试：mock Tavily API，验证工具输出格式"""
       mock_tavily.search.return_value = {
           "results": [
               {"title": "Test Report", "url": "https://example.com/report", "content": "AI impacts jobs...", "score": 0.95}
           ]
       }
       
       result_str = await search_web("AI job impact")
       result = json.loads(result_str)
       
       assert "results" in result
       assert len(result["results"]) > 0
       assert "url" in result["results"][0]
       assert result["error"] is None


   @patch("src.agent.tools.httpx.AsyncClient")
   async def test_read_page_timeout(mock_client):
       """离线测试：模拟超时场景"""
       mock_client.return_value.__aenter__.return_value.get.side_effect = httpx.TimeoutException("timeout")
       
       result_str = await read_page("https://example.com/paywalled")
       result = json.loads(result_str)
       
       assert result["status"] == "timeout"
       assert result["error"] is not None
   ```

### 验收标准

- [ ] 4 个在线测试用例全部通过
- [ ] 每个用例的运行时间 < 2 分钟
- [ ] Agent 的输出包含具体、有引用来源的信息
- [ ] 至少 2 个离线 mock 测试（mock Tavily/Jina/httpx），CI 不依赖外部 API

---

## Milestone 2 完成标志 ✅

- [ ] Agent 具备 "计划 → 执行 → 总结" 的三阶段工作流
- [ ] Prompt 已调优，搜索行为有策略、有纪律
- [ ] 所有工具和节点都有错误处理（指数退避重试）
- [ ] 付费墙降级策略经过实测验证
- [ ] Golden Test Cases 全部通过（含离线 mock 测试）
- [ ] State 更新全部通过返回 dict 实现，无 in-place mutation
