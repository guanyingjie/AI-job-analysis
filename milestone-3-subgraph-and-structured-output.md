# Milestone 3：子图拆分（多维度并行搜索）+ 结构化输出

**目标：** 将单一的研究流程拆分为多个专注维度的子图（SubGraph），并在最后将松散的搜索结果收敛为严格的 Pydantic JSON 结构，为入库做准备。

**前置依赖：** Milestone 2 完成

**预估耗时：** 3-4 小时

---

## 架构衔接说明（M2 → M3 的关系）

M2 引入了动态 `create_research_plan` 节点，LLM 会输出带 `dimension` 标注的 `ResearchStep` 列表。M3 不替代 M2 的规划逻辑，而是**在其基础上扩展**：

- **M2 的 `create_research_plan`** 生成动态的搜索 steps，每步标注所属维度（`macro` / `job_market` / `tech_frontier`）
- **M3 的子图** 接收对应维度的 steps，分别执行搜索
- 即：M2 规划 → M3 按维度分发执行 → M3 汇总输出
- `ResearchStep` / `ResearchPlan` 建议定义在共享类型模块（如 `src/agent/types.py`），避免在 `prompts.py` 和 `state.py` 之间产生循环导入

如果 LLM 生成的某个维度为空（例如没有 `tech_frontier` 的步骤），对应子图使用该维度的默认 fallback 查询继续执行，保证覆盖面。

---

## Task 3.1：定义 Pydantic 数据模型

**文件：** `src/agent/models.py`（新建）

### 要做的事

定义严格的输出 Schema。这些模型将同时服务于：
- LLM 的 `with_structured_output()`
- 后续 Milestone 4 的数据库 ORM 映射

```python
from pydantic import BaseModel, Field, field_validator
from enum import Enum
from datetime import date

class JobZone(str, Enum):
    RED = "red"        # 衰退区：正在被 AI 替代
    YELLOW = "yellow"  # 进化区：被 AI 重塑但不会消失
    GREEN = "green"    # 新兴区：因 AI 而新诞生

class RequiredSkill(BaseModel):
    """岗位所需的关键技能"""
    skill_name: str = Field(description="技能名称")
    is_ai_related: bool = Field(description="是否为 AI 相关技能")

class Source(BaseModel):
    """信息来源（URL + 名称绑定，避免平行列表对不齐问题）"""
    url: str = Field(description="来源 URL")
    name: str = Field(description="来源名称，如 'WEF Future of Jobs Report 2025'")

class JobTrend(BaseModel):
    """单个岗位的趋势分析"""
    job_title: str = Field(description="岗位名称（中文）")
    job_title_en: str = Field(description="岗位名称（英文）")
    zone: JobZone = Field(description="所属区域：red/yellow/green")
    trend_description: str = Field(description="趋势描述：为什么这个岗位在衰退/进化/增长")
    ai_impact: str = Field(description="AI 具体如何影响这个岗位")
    required_skills: list[RequiredSkill] = Field(description="该岗位需要的关键技能列表")
    demand_change: str = Field(description="需求变化趋势：如 '需求下降30%' 或 '年增长率50%'")
    sources: list[Source] = Field(description="信息来源列表（URL 和名称绑定）")

    @field_validator("sources")
    @classmethod
    def sources_not_empty(cls, v: list[Source]) -> list[Source]:
        if not v:
            raise ValueError("每个岗位趋势必须至少有一个信息来源")
        return v

class MarketInsight(BaseModel):
    """市场洞察（来自招聘平台数据）"""
    platform: str = Field(description="数据来源平台：如 LinkedIn、Boss直聘、Indeed")
    insight: str = Field(description="核心洞察")
    data_point: str = Field(description="关键数据点：如 'AI Engineer 岗位同比增长 74%'")
    date_observed: str = Field(description="数据观测日期或时间范围，如 '2025-Q4' 或 '2025-12'")

class JobTrendReport(BaseModel):
    """完整的 AI 就业趋势报告"""
    report_date: str = Field(description="报告生成日期，格式 YYYY-MM-DD")
    executive_summary: str = Field(
        description="执行摘要：200字以内的核心发现",
        min_length=20,
    )
    declining_jobs: list[JobTrend] = Field(description="衰退区（Red Zone）岗位列表")
    evolving_jobs: list[JobTrend] = Field(description="进化区（Yellow Zone）岗位列表")
    emerging_jobs: list[JobTrend] = Field(description="新兴区（Green Zone）岗位列表")
    market_insights: list[MarketInsight] = Field(description="市场微观洞察列表")
    key_reports_referenced: list[str] = Field(description="引用的关键报告名称列表")

    @field_validator("declining_jobs", "evolving_jobs", "emerging_jobs")
    @classmethod
    def at_least_some_jobs(cls, v: list[JobTrend], info) -> list[JobTrend]:
        """允许单个列表为空，但在 format_output_with_retry 时会校验总数 > 0"""
        return v
```

### 设计说明

- **`Source` 模型** 将 URL 和名称绑定为一个对象，取代之前的 `source_urls` + `source_names` 平行列表。这避免了 LLM 生成两个长度不一致的列表的风险。
- 每个 `JobTrend` 都必须包含 `sources`（通过 validator 强制），确保信息可追溯。
- `JobZone` 使用枚举类型，确保分类严格。
- `MarketInsight` 单独拎出来，因为它的数据结构和岗位趋势不同。
- `executive_summary` 添加了 `min_length` 验证，防止 LLM 生成空摘要。

### 验收标准

- [ ] 所有 Pydantic 模型能正常序列化/反序列化
- [ ] 模型的 JSON Schema 可以被 `with_structured_output()` 正确消费
- [ ] 手动构造一个 `JobTrendReport` 实例，确认字段完整
- [ ] `Source` 模型正确绑定 URL 和名称，无对不齐风险

---

## Task 3.2：拆分研究子图 (SubGraph)

**文件目录：** `src/agent/research/`（新建子目录）

```
src/agent/research/
├── __init__.py
├── defaults.py                # Fallback 查询常量（DEFAULT_MACRO_QUERIES 等）
├── macro_research.py          # 宏观报告搜索子图
├── job_market_research.py     # 招聘市场数据搜索子图
├── tech_frontier_research.py  # 技术前沿搜索子图
└── state.py                   # 子图共享状态
```

### 要做的事

将 M2 中的单一 Agent 拆分为 **3 个专注的研究子图**，子图接收 M2 `create_research_plan` 动态生成的搜索步骤：

#### 子图 1：`macro_research`（宏观报告搜索）

- **职责：** 搜索 WEF、McKinsey、BCG、PwC、Gartner 等机构的报告
- **搜索步骤来源：** M2 规划阶段中 `dimension == "macro"` 的 steps
- **Fallback 策略：** 如果 LLM 规划中没有 macro 维度的 steps，使用以下默认查询：
  - "WEF Future of Jobs Report 2025 2026 key findings"
  - "McKinsey GenAI impact on workforce"
  - "AI 就业影响 报告 2025 2026"
- **最大搜索次数：** 3 次
- **输出：** 搜索到的文档内容追加到共享的 `documents` 列表

#### 子图 2：`job_market_research`（招聘市场数据）

- **职责：** 搜索 LinkedIn、Indeed、Boss直聘等平台的就业数据
- **搜索步骤来源：** M2 规划阶段中 `dimension == "job_market"` 的 steps
- **Fallback 策略：** 默认查询：
  - "LinkedIn emerging jobs report 2025 2026 AI"
  - "AI related job growth statistics 2025 2026"
  - "AIGC 大模型 招聘 岗位增长 数据"
- **最大搜索次数：** 3 次
- **输出：** 搜索到的文档内容追加到共享的 `documents` 列表

#### 子图 3：`tech_frontier_research`（技术前沿动态）

- **职责：** 搜索 AI Agent、大模型应用等最新技术动态和融资信息
- **搜索步骤来源：** M2 规划阶段中 `dimension == "tech_frontier"` 的 steps
- **Fallback 策略：** 默认查询：
  - "AI agent startup funding 2025 2026"
  - "AI 创业公司 融资 招聘 扩张"
- **最大搜索次数：** 2 次
- **输出：** 搜索到的文档内容追加到共享的 `documents` 列表

### Fallback 查询常量

当 M2 动态规划中某维度无 steps 时，子图使用以下默认查询作为兜底：

```python
# src/agent/research/defaults.py

DEFAULT_MACRO_QUERIES = [
    "WEF Future of Jobs Report 2025 2026 key findings",
    "McKinsey GenAI impact on workforce",
    "AI 就业影响 报告 2025 2026",
]

DEFAULT_JOB_MARKET_QUERIES = [
    "LinkedIn emerging jobs report 2025 2026 AI",
    "AI related job growth statistics 2025 2026",
    "AIGC 大模型 招聘 岗位增长 数据",
]

DEFAULT_TECH_QUERIES = [
    "AI agent startup funding 2025 2026",
    "AI 创业公司 融资 招聘 扩张",
]
```

### 分发逻辑（主图 → 子图）

**文件：** `src/agent/nodes.py`（与其他主图节点函数放在一起）

```python
from src.agent.research.defaults import (
    DEFAULT_MACRO_QUERIES, DEFAULT_JOB_MARKET_QUERIES, DEFAULT_TECH_QUERIES,
)


def dispatch_to_subgraphs(state: AgentState) -> dict:
    """将 create_research_plan 生成的 steps 按 dimension 分发"""
    macro_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "macro"]
    job_market_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "job_market"]
    tech_steps = [s["query"] for s in state.plan_steps if s["dimension"] == "tech_frontier"]
    
    return {
        "macro_queries": macro_steps or DEFAULT_MACRO_QUERIES,
        "job_market_queries": job_market_steps or DEFAULT_JOB_MARKET_QUERIES,
        "tech_queries": tech_steps or DEFAULT_TECH_QUERIES,
    }
```

### 子图共享状态定义

**文件：** `src/agent/research/state.py`

```python
from dataclasses import dataclass, field
from typing import Annotated
from langchain_core.documents import Document


def append_docs(existing: list[Document] | None, new: list[Document] | None) -> list[Document]:
    """子图内部的 document 合并（简单追加，主图的 reduce_docs 负责去重）。
    与主图 reduce_docs 保持一致的 None 安全处理，避免 LangGraph 初始化时传入 None 导致 TypeError。"""
    return (existing or []) + (new or [])


@dataclass(kw_only=True)
class SubgraphState:
    """三个研究子图共享的内部状态"""
    queries: list[str] = field(default_factory=list)      # 该维度分配的搜索查询列表
    step_index: int = 0                                    # 当前执行到第几个 query
    max_searches: int = 3                                  # 该子图的 Tavily 调用上限（search_web + search_report_summary）
    tavily_call_count: int = 0                             # 子图内累计 Tavily 调用次数（search_web + search_report_summary）
    documents: Annotated[list[Document], append_docs] = field(default_factory=list)  # 搜索到的文档
```

> **说明：** 三个子图复用同一个 `SubgraphState`，在运行时传入不同的 `queries` 和 `max_searches`（`tech_frontier_research` 的维度上限为 2）。子图输出的 `documents` 最终由主图的 `reduce_docs` reducer 负责跨子图去重和截断。

### 每个子图的内部结构

```
START → check_has_queries → search_and_read → [条件：还有查询？] → search_and_read → ... → END
```

每个子图内部是一个简化的循环：
1. 从分配的搜索步骤中取出一个 query（通过 `step_index` 递增，不使用 list.pop）
2. 调用 `search_web` 搜索（返回 JSON 字符串，用 `json.loads()` 解析结构化 `results`）
3. 对搜索结果中最相关的 1-2 个 URL 调用 `read_page`（基于解析后的结构化字段选择）
4. 若 `read_page` 返回 `paywalled/forbidden/timeout`，调用 `search_report_summary` 执行降级检索（严格继承 M2 策略）
5. 将可用内容追加到 `documents`
6. 检查是否还有下一步，有则继续，无则结束

**子图构建代码**（同样位于 `src/agent/research/state.py`，与 `SubgraphState` 定义在同一文件中。三个子图复用此模板，`max_searches` 通过子图输入 state 传入）：

```python
# ---- 以下代码仍在 src/agent/research/state.py 中 ----
# SubgraphState、append_docs、Document 已在上方定义，无需重复 import

from langgraph.graph import StateGraph, END
from src.agent.tools import search_web, read_page, search_report_summary
import json


async def search_and_read(state: SubgraphState) -> dict:
    """执行一步搜索 + 阅读，将结果追加到 documents"""
    # 防御式保护：避免空 queries、step 越界、预算耗尽导致异常
    if (
        state.step_index >= len(state.queries)
        or state.tavily_call_count >= state.max_searches
    ):
        return {}
    query = state.queries[state.step_index]

    tavily_calls_this_step = 0

    # 1. 搜索（Tavily 调用）
    search_result_str = await search_web.ainvoke({"query": query})
    search_result = json.loads(search_result_str)

    # ⭐ 检查搜索是否成功（M5 预算耗尽、Tavily API 故障等均会返回 error）
    # 如果搜索失败，跳过本步骤的 read_page，直接推进到下一步
    if search_result.get("error"):
        return {
            "step_index": state.step_index + 1,
            # 不增加 tavily_call_count：搜索未实际执行（如 M5 预算耗尽时 search_web 会提前返回）
        }
    tavily_calls_this_step += 1  # 搜索成功才计数

    new_docs = []
    # 2. 对 top 2 结果调用 read_page；付费墙时回退到 search_report_summary（M2 策略）
    for item in search_result.get("results", [])[:2]:
        page_str = await read_page.ainvoke({"url": item["url"]})
        page_result = json.loads(page_str)
        if page_result.get("status") == "ok":
            new_docs.append(Document(  # Document 已在文件顶部 import
                page_content=page_result["content"],
                metadata={"source": item["url"], "title": item.get("title", "")},
            ))
        elif page_result.get("status") in {"paywalled", "forbidden", "timeout"}:
            # 预算保护：本步内如果 Tavily 预算已耗尽，则不再触发降级搜索
            if state.tavily_call_count + tavily_calls_this_step >= state.max_searches:
                continue
            # 降级：搜索该报告/页面的公开摘要（Tavily 调用）
            report_name = item.get("title") or query
            fallback_str = await search_report_summary.ainvoke({"report_name": report_name})
            tavily_calls_this_step += 1
            fallback_result = json.loads(fallback_str)
            # 将摘要搜索结果转为轻量文档，供 summarize_findings 汇总
            for r in fallback_result.get("results", [])[:2]:
                new_docs.append(Document(
                    page_content=r.get("snippet", ""),
                    metadata={"source": r.get("url", ""), "title": r.get("title", "")},
                ))
    
    return {
        "documents": new_docs,
        "step_index": state.step_index + 1,
        "tavily_call_count": state.tavily_call_count + tavily_calls_this_step,
    }


def has_more_queries(state: SubgraphState) -> str:
    """检查是否还有未执行的查询"""
    if state.step_index < len(state.queries) and state.tavily_call_count < state.max_searches:
        return "continue"
    return "done"


def check_has_queries(state: SubgraphState) -> str:
    """入口检查：无查询或预算为 0 时直接结束，避免进入 search_and_read 越界。"""
    if state.step_index < len(state.queries) and state.max_searches > 0:
        return "run"
    return "done"


def route_entry(state: SubgraphState) -> dict:
    """子图入口空节点，仅用于承接条件路由。"""
    return {}


def build_research_subgraph():
    """构建并编译研究子图。max_searches 通过子图输入 state 传入。返回 CompiledStateGraph。"""
    builder = StateGraph(SubgraphState)
    builder.add_node("route_entry", route_entry)
    builder.add_node("search_and_read", search_and_read)
    builder.set_entry_point("route_entry")
    builder.add_conditional_edges("route_entry", check_has_queries, {
        "run": "search_and_read",
        "done": END,
    })
    builder.add_conditional_edges("search_and_read", has_more_queries, {
        "continue": "search_and_read",
        "done": END,
    })
    return builder.compile()
```

### 三个研究模块的文件结构

三个子图模块（`macro_research.py`、`job_market_research.py`、`tech_frontier_research.py`）在 MVP 阶段共享完全相同的构建逻辑，只需从共享模块 re-export `build_research_subgraph`：

```python
# src/agent/research/macro_research.py（job_market_research.py、tech_frontier_research.py 同理）

from src.agent.research.state import build_research_subgraph

# MVP 阶段：三个模块完全一致，直接 re-export。
# 未来如需为不同维度定制子图节点逻辑（如 macro 维度增加报告下载节点），
# 可在各自模块中定义独立的 build_research_subgraph 实现。
```

> **设计考量：** 保留三个独立文件而非合并为一个，是为了未来扩展：例如 `macro_research` 可能增加 PDF 解析节点，`job_market_research` 可能接入招聘 API。MVP 阶段三个文件内容一致，不增加维护负担。

### 关于 `search_report_summary` 工具

M3 子图**必须继承 M2 的付费墙降级能力**：当 `read_page` 返回 `paywalled/forbidden/timeout` 时，子图立即调用 `search_report_summary` 检索公开摘要并继续流程，而不是静默跳过。

这保证了：
- M2 与 M3 行为一致（无策略回归）
- 权威付费报告场景仍可通过公开解读补足信息
- 预算统计口径一致（`search_web` + `search_report_summary` 都计入 Tavily 调用）

### 设计说明

- 子图接收 M2 动态规划的 steps，不再硬编码搜索查询（硬编码查询仅作为 fallback）。
- 子图之间**互相独立**，理论上可以并行执行（LangGraph 支持 `Send` API 做并行分发，但 MVP 阶段可以先串行）。
- 每个子图有独立的搜索次数上限，总体可控。
- 共享同一套 `tools`，只是搜索范围不同。

### 验收标准

- [ ] 三个子图可以独立运行并返回 `documents`
- [ ] 每个子图的 Tavily 调用次数（`search_web` + `search_report_summary`）不超过预设上限
- [ ] 子图的 `documents` 能正确合并到主图的 `AgentState` 中
- [ ] 子图优先使用 M2 规划的 steps，无 steps 时使用 fallback 查询

---

## Task 3.3：修改主图结构 — 编排子图

**文件：** `src/agent/graph.py`（修改）

### 要做的事

将主图改为 **"计划 → 分维度搜索 → 信息压缩 → 结构化输出"** 的四阶段流程：

```
                         ┌──────────────────┐
                         │      START       │
                         └────────┬─────────┘
                                  ▼
                    ┌───────────────────────────┐
                    │    create_research_plan    │
                    │  (M2 动态生成各维度搜索计划) │
                    └────────────┬──────────────┘
                                 ▼
                    ┌───────────────────────────┐
                    │    dispatch_to_subgraphs   │
                    │  (按 dimension 分发 steps)  │
                    └────────────┬──────────────┘
                                 ▼
              ┌──────────────────────────────────────┐
              │         research_executor             │
              │  ┌──────────┬──────────┬───────────┐ │
              │  │ 子图1    │  子图2    │  子图3     │ │
              │  │ 宏观报告  │ 招聘数据  │ 技术前沿   │ │
              │  └──────────┴──────────┴───────────┘ │
              │         (串行执行，MVP 阶段)          │
              └──────────────────┬───────────────────┘
                                 ▼
                    ┌───────────────────────────┐
                    │     summarize_findings     │
                    │  (压缩所有搜索结果到摘要)    │
                    └────────────┬──────────────┘
                                 ▼
                    ┌───────────────────────────┐
                    │ format_output_with_retry   │
                    │  (LLM + with_structured_   │
                    │   output → JobTrendReport  │
                    │   含重试和 fallback，见 3.4) │
                    └────────────┬──────────────┘
                                 ▼
                         ┌──────────────────┐
                         │       END        │
                         └──────────────────┘
```

### 关键节点实现

0. **`research_executor` 节点**（编排三个子图串行执行，**文件：** `src/agent/nodes.py`）

> **说明：** M3 的子图替代了 M2 中主图的 `conduct_research → agent → tools → count_search_calls → check_plan_finished` 执行循环。M2 的 `create_research_plan` 规划逻辑保留，但执行阶段由子图接管。因此 M2 中的 `conduct_research`、`check_plan_finished`、`should_continue` 等节点在主图中不再使用（子图内部有自己的循环控制）。
>
> **预算一致性（关键）：** 虽然 M3 不再复用 M2 的 `count_search_calls` 节点链路，但仍必须继承 M1/M2 的全局预算语义：`search_count` + `max_searches`，且口径为所有 Tavily 调用（`search_web` + `search_report_summary`）。实现上由 `research_executor` 聚合子图返回的 `tavily_call_count`，并在主图层维护全局剩余预算；任一时刻预算耗尽即停止后续子图执行，防止总搜索次数超额。

```python
from src.agent.research.macro_research import build_research_subgraph as build_macro
from src.agent.research.job_market_research import build_research_subgraph as build_job_market
from src.agent.research.tech_frontier_research import build_research_subgraph as build_tech

# 编译三个子图（模块级别，只编译一次）
macro_subgraph = build_macro()
job_market_subgraph = build_job_market()
tech_subgraph = build_tech()


async def research_executor(state: AgentState) -> dict:
    """
    串行执行三个研究子图，收集所有 documents。
    
    从主图 state 中提取各维度的 queries，构造子图输入，
    运行子图，合并 documents 返回给主图（由 reduce_docs reducer 去重）。
    """
    all_documents = []
    current_search_count = state.search_count
    remaining_budget = max(state.max_searches - current_search_count, 0)
    
    # 子图 1：宏观报告（维度上限 3，受全局剩余预算约束）
    macro_budget = min(3, remaining_budget)
    if macro_budget > 0:
        macro_result = await macro_subgraph.ainvoke({
            "queries": state.macro_queries,
            "max_searches": macro_budget,
        })
        all_documents.extend(macro_result.get("documents", []))
        macro_used = macro_result.get("tavily_call_count", 0)
        current_search_count += macro_used
        remaining_budget = max(state.max_searches - current_search_count, 0)
    
    # 子图 2：招聘市场（维度上限 3，受全局剩余预算约束）
    job_budget = min(3, remaining_budget)
    if job_budget > 0:
        job_result = await job_market_subgraph.ainvoke({
            "queries": state.job_market_queries,
            "max_searches": job_budget,
        })
        all_documents.extend(job_result.get("documents", []))
        job_used = job_result.get("tavily_call_count", 0)
        current_search_count += job_used
        remaining_budget = max(state.max_searches - current_search_count, 0)
    
    # 子图 3：技术前沿（维度上限 2，受全局剩余预算约束）
    tech_budget = min(2, remaining_budget)
    if tech_budget > 0:
        tech_result = await tech_subgraph.ainvoke({
            "queries": state.tech_queries,
            "max_searches": tech_budget,
        })
        all_documents.extend(tech_result.get("documents", []))
        tech_used = tech_result.get("tavily_call_count", 0)
        current_search_count += tech_used
    
    # 回写全局搜索计数，与 M1/M2 的预算字段保持一致
    return {
        "documents": all_documents,
        "search_count": current_search_count,
    }
```

1. **`summarize_findings` 节点**（核心！解决 Token 爆炸问题，**文件：** `src/agent/nodes.py`）

```python
import asyncio
from langchain_core.messages import AIMessage

async def summarize_findings(state: AgentState, config: RunnableConfig) -> dict:
    """
    将所有搜索到的原始文档压缩为一份结构化摘要。
    
    这是解决 Token 爆炸的关键节点：
    - 输入：可能包含数万字的原始网页内容
    - 输出：一份 3000-5000 字的结构化摘要
    
    压缩后再交给 format_output_with_retry，确保不会超过 context window。
    应用 M2 Task 2.4 的指数退避重试模式，避免 LLM API 偶发故障导致整个图崩溃。
    """
    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    )
    
    # ⭐ 防御式保护：如果所有子图搜索结果为空（网络故障、API 限额耗尽等），
    # 直接返回降级摘要，避免 LLM 基于空内容幻觉
    if not state.documents:
        return {
            "summary": "未能收集到任何搜索结果。可能原因：网络故障、API 限额耗尽或所有 URL 已被去重过滤。"
        }

    # 将所有 documents 拼接，但每个截断到 2000 字
    combined = "\n\n---\n\n".join([
        f"来源: {doc.metadata.get('source', 'unknown')}\n{doc.page_content[:2000]}"
        for doc in state.documents
    ])
    
    summary_prompt = f"""
    请将以下搜索结果整理为一份结构化摘要，按以下分类组织：
    1. 衰退区（Red Zone）岗位及原因
    2. 进化区（Yellow Zone）岗位及变化
    3. 新兴区（Green Zone）岗位及所需技能
    4. 关键数据点和市场洞察
    
    搜索结果：
    {combined}
    """
    
    messages = [
        {"role": "system", "content": "你是一个信息整理专家。请精确提取关键信息，保留数据和来源。"},
        {"role": "user", "content": summary_prompt}
    ]
    
    # ⭐ 指数退避重试（与 M2 Task 2.4 的 call_model 保持一致）
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)
            return {"summary": response.content}
        except Exception as e:
            if attempt == max_retries - 1:
                # 最后一次也失败：返回降级摘要而非崩溃，让 format_output_with_retry 的 fallback 兜底
                return {
                    "summary": f"摘要生成失败（已重试 {max_retries} 次）：{str(e)}。"
                    f"原始文档数量：{len(state.documents)}"
                }
            await asyncio.sleep(2 ** attempt)
```

> **注意：** `create_research_plan` 和 `summarize_findings` 中的 LLM 调用同样需要应用 M2 Task 2.4 的指数退避重试模式（`try/except + asyncio.sleep(2 ** attempt)`），确保 LLM API 偶发故障时不会导致整个图崩溃。`format_output_with_retry` 已在 Task 3.4 内置了重试逻辑。

2. **`format_output_with_retry` 节点**（具体重试实现见 Task 3.4，**文件：** `src/agent/nodes.py`）

```python
async def format_output_with_retry(state: AgentState, config: RunnableConfig) -> dict:
    """
    将压缩后的摘要转换为严格的 JobTrendReport JSON。
    
    注意：这里的输入是 summary（几千字），而非原始 messages（可能几万字）。
    使用 config 中的 LLM 配置，不硬编码模型名称。
    """
    settings = get_settings()
    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=0,  # 结构化输出使用 temperature=0 确保稳定性
    ).with_structured_output(JobTrendReport)
    
    result = await model.ainvoke([
        {"role": "system", "content": "根据以下研究摘要，生成一份结构化的 AI 就业趋势报告。"},
        {"role": "user", "content": state.summary}
    ])
    
    return {"final_report": result}
```

### `reduce_docs` reducer 实现

**文件：** `src/agent/state.py`（与 `AgentState` 定义在同一文件中，作为 `documents` 字段的 reducer）

```python
from langchain_core.documents import Document


def reduce_docs(existing: list[Document] | None, new: list[Document] | None) -> list[Document]:
    """
    合并文档列表的 reducer：
    - 按 metadata["source"]（URL）去重
    - 同 URL 文档保留更长的版本
    - 每个文档截断到 4000 字符，避免 reducer 持续膨胀
    - 保留来源元数据（source/title）供后续引用
    """
    if existing is None:
        existing = []
    if new is None:
        return existing

    by_url: dict[str, Document] = {}
    for doc in existing:
        url = doc.metadata.get("source", f"unknown_{id(doc)}")
        by_url[url] = doc

    for doc in new:
        url = doc.metadata.get("source", f"unknown_{id(doc)}")
        if url not in by_url or len(doc.page_content) > len(by_url[url].page_content):
            by_url[url] = doc

    return [
        Document(
            page_content=d.page_content[:4000],
            metadata=d.metadata,
        )
        for d in by_url.values()
    ]
```

### 主图构建代码

> **注意：** M3 的主图是一条线性流水线，取代了 M2 中的 `conduct_research → agent → tools → count_search_calls → check_plan_finished` 执行循环。M2 的 `create_research_plan` 保留作为流水线入口。

```python
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
```

### M3 更新后的完整 `state.py`

> **⭐ 关键：** 以下是 M3 阶段 `src/agent/state.py` 的完整代码。包含 M1 基础字段 + M2 规划字段 + M3 的子图分发/搜索结果/输出字段，以及 `reduce_docs` reducer 函数。开发者可直接复制此文件。

```python
from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AnyMessage
from langchain_core.documents import Document  # ⭐ M3 新增
from langgraph.graph.message import add_messages

from src.config import get_settings
from src.agent.types import ResearchStep        # ⭐ M2 新增
from src.agent.models import JobTrendReport      # ⭐ M3 新增


def reduce_docs(existing: list[Document] | None, new: list[Document] | None) -> list[Document]:
    """
    合并文档列表的 reducer：
    - 按 metadata["source"]（URL）去重
    - 同 URL 文档保留更长的版本
    - 每个文档截断到 4000 字符，避免 reducer 持续膨胀
    """
    if existing is None:
        existing = []
    if new is None:
        return existing

    by_url: dict[str, Document] = {}
    for doc in existing:
        url = doc.metadata.get("source", f"unknown_{id(doc)}")
        by_url[url] = doc
    for doc in new:
        url = doc.metadata.get("source", f"unknown_{id(doc)}")
        if url not in by_url or len(doc.page_content) > len(by_url[url].page_content):
            by_url[url] = doc

    return [
        Document(page_content=d.page_content[:4000], metadata=d.metadata)
        for d in by_url.values()
    ]


@dataclass(kw_only=True)
class AgentState:
    """Agent 全局状态（M3 完整版：M1 基础 + M2 规划 + M3 子图/输出）"""

    # ── M1 基础字段 ──
    messages: Annotated[list[AnyMessage], add_messages]
    search_count: int = 0
    max_searches: int = field(default_factory=lambda: get_settings().max_searches)

    # ── M2 规划字段 ──
    plan_steps: list[ResearchStep] = field(default_factory=list)
    step_index: int = 0
    current_step: ResearchStep | None = None

    # ── M3 子图分发字段 ──
    macro_queries: list[str] = field(default_factory=list)
    job_market_queries: list[str] = field(default_factory=list)
    tech_queries: list[str] = field(default_factory=list)

    # ── M3 搜索结果与输出字段 ──
    documents: Annotated[list[Document], reduce_docs] = field(default_factory=list)
    summary: str = ""
    final_report: JobTrendReport | None = None


@dataclass(kw_only=True)
class InputState:
    """对外暴露的窄接口，用于 graph.ainvoke() 的输入"""
    messages: Annotated[list[AnyMessage], add_messages]
```

### M3 更新后的 `nodes.py` import 列表

> **⭐ 关键：** M3 的 `nodes.py` 在 M2 import 基础上新增以下依赖。以下是 M3 阶段 `nodes.py` 顶部的完整 import 区域：

```python
# ---- src/agent/nodes.py M3 完整 import 列表 ----
import asyncio
import json                                    # ⭐ M3 新增：子图 search_and_read 中 json.loads()
from typing import Literal
from datetime import date                      # ⭐ M3 新增：fallback 中 date.today()
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage
from langchain_core.documents import Document  # ⭐ M3 新增：summarize_findings 中使用
from langchain_google_genai import ChatGoogleGenerativeAI
from src.config import get_settings
from src.agent.state import AgentState
from src.agent.tools import tools
from src.agent.prompts import SYSTEM_PROMPT, PLANNING_PROMPT, FORMAT_PROMPT  # ⭐ M3 更新：加入 FORMAT_PROMPT
from src.agent.types import ResearchPlan
from src.agent.models import JobTrendReport    # ⭐ M3 新增：format_output_with_retry 使用
from src.agent.research.defaults import (      # ⭐ M3 新增
    DEFAULT_MACRO_QUERIES, DEFAULT_JOB_MARKET_QUERIES, DEFAULT_TECH_QUERIES,
)
from src.agent.research.macro_research import build_research_subgraph as build_macro          # ⭐ M3 新增
from src.agent.research.job_market_research import build_research_subgraph as build_job_market  # ⭐ M3 新增
from src.agent.research.tech_frontier_research import build_research_subgraph as build_tech     # ⭐ M3 新增
```

### 验收标准

- [ ] `reduce_docs` 能正确按 URL 去重并截断
- [ ] `summarize_findings` 能将超长内容压缩到 5000 字以内
- [ ] `format_output_with_retry` 能稳定输出合法的 `JobTrendReport` JSON
- [ ] 端到端运行：搜索 → 压缩 → 格式化，不出现 Token 超限错误
- [ ] 子图在空 `queries` 输入时能安全结束（不出现 `IndexError`）
- [ ] 全图总搜索步数不超过 `max_searches`（M1/M2 全局预算语义在 M3 继续成立）
- [ ] `read_page` 遇到 `paywalled/forbidden/timeout` 时，子图会调用 `search_report_summary` 进行降级而非静默跳过

---

## Task 3.4：结构化输出的重试机制

**文件：** `src/agent/nodes.py`

### 要做的事

`with_structured_output` 在实践中并非 100% 成功（尤其是字段多、嵌套深的 Schema）。需要增加重试逻辑：

> **注意：** LangGraph 节点函数签名只能是 `(state)` 或 `(state, config)`，不能有额外参数。`max_retries` 放在函数体内作为常量。

**`FORMAT_PROMPT` 常量**（建议放在 `src/agent/prompts.py` 中）：

```python
FORMAT_PROMPT = """根据以下研究摘要，生成一份结构化的 AI 就业趋势报告。

要求：
1. 将岗位明确分类到 Red（衰退）、Yellow（进化）、Green（新兴）三个区域
2. 每个岗位必须包含至少一个信息来源（Source），包含 URL 和来源名称
3. 每个岗位的趋势描述要具体，包含数据支撑
4. executive_summary 控制在 200 字以内，概括核心发现
5. 如果信息不足以支撑某个分类，该列表可以为空，但三个区域总计至少包含一个岗位
"""
```

**带重试的结构化输出节点：**

```python
from datetime import date  # ⭐ fallback 中 date.today() 需要此 import（nodes.py 新增）

MAX_FORMAT_RETRIES = 3


async def format_output_with_retry(state: AgentState, config: RunnableConfig) -> dict:
    """带重试的结构化输出"""
    settings = get_settings()
    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=0,
    ).with_structured_output(JobTrendReport)
    
    for attempt in range(MAX_FORMAT_RETRIES):
        try:
            result = await model.ainvoke([
                {"role": "system", "content": FORMAT_PROMPT},
                {"role": "user", "content": state.summary}
            ])
            # 验证必填字段不为空
            total_jobs = len(result.declining_jobs) + len(result.evolving_jobs) + len(result.emerging_jobs)
            assert total_jobs > 0, "报告必须至少包含一个岗位趋势"
            return {"final_report": result}
        except Exception as e:
            if attempt == MAX_FORMAT_RETRIES - 1:
                # 最后一次重试也失败，返回一个最小化的报告
                # ⭐ 注意：executive_summary 有 min_length=20 的 Pydantic 约束，
                # 必须确保 fallback 文本长度 ≥ 20 字符，否则 fallback 自身也会抛 ValidationError
                fallback_summary = f"结构化输出失败（重试 {MAX_FORMAT_RETRIES} 次），原始摘要：{state.summary[:500]}"
                if len(fallback_summary) < 20:
                    fallback_summary = fallback_summary.ljust(20, "。")
                fallback_report = JobTrendReport(
                    report_date=str(date.today()),
                    executive_summary=fallback_summary,
                    declining_jobs=[], evolving_jobs=[], emerging_jobs=[],
                    market_insights=[], key_reports_referenced=[]
                )
                return {"final_report": fallback_report}
            # 指数退避重试
            await asyncio.sleep(2 ** attempt)
```

### 验收标准

- [ ] 结构化输出失败时能自动重试（指数退避）
- [ ] 3 次重试全部失败时能返回 fallback 报告而非崩溃
- [ ] 正常情况下第一次就成功（验证 Schema 设计的合理性）

---

## Task 3.5：端到端集成测试

### 要做的事

运行完整的图：`规划 → 搜索 → 压缩 → 格式化`，验证输出的 JSON 质量。

```python
async def test_full_pipeline():
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "全面分析 AI 对就业市场的影响"}]},
        config={"recursion_limit": 50}
    )
    
    report = result["final_report"]
    
    # 基础质量检查
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) >= 20
    total_jobs = len(report.declining_jobs) + len(report.evolving_jobs) + len(report.emerging_jobs)
    assert total_jobs > 0
    
    # 来源检查（使用新的 Source 模型）
    all_jobs = report.declining_jobs + report.evolving_jobs + report.emerging_jobs
    for job in all_jobs:
        assert len(job.sources) > 0, f"{job.job_title} 缺少来源"
        for source in job.sources:
            assert source.url, f"{job.job_title} 的来源缺少 URL"
            assert source.name, f"{job.job_title} 的来源缺少名称"
    
    # 打印报告
    print(report.model_dump_json(indent=2, ensure_ascii=False))
```

### 验收标准

- [ ] `report.model_dump_json()` 的输出可被 `json.loads()` 正确解析
- [ ] 报告至少覆盖 Red/Yellow/Green 三个区域中的两个
- [ ] 每个岗位趋势的 `sources` 列表非空，且每个 Source 都有 URL 和名称

---

## Milestone 3 完成标志 ✅

- [ ] Pydantic 数据模型定义完成且通过验证（`Source` 模型绑定 URL 和名称）
- [ ] 三个研究子图能独立运行，且接收 M2 动态规划的 steps
- [ ] `reduce_docs` reducer 实现完成，能按 URL 去重并截断
- [ ] `summarize_findings` 节点有效解决 Token 爆炸问题
- [ ] `format_output_with_retry` 节点能稳定输出 `JobTrendReport` JSON（函数签名合法）
- [ ] 结构化输出有重试和 fallback 机制
- [ ] 端到端测试通过，输出质量达标
