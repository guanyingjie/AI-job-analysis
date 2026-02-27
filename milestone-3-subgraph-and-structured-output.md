# Milestone 3：子图拆分（多维度并行搜索）+ 结构化输出

**目标：** 将单一的研究流程拆分为多个专注维度的子图（SubGraph），并在最后将松散的搜索结果收敛为严格的 Pydantic JSON 结构，为入库做准备。

**前置依赖：** Milestone 2 完成

**预估耗时：** 3-4 小时

---

## Task 3.1：定义 Pydantic 数据模型

**文件：** `src/agent/models.py`（新建）

### 要做的事

定义严格的输出 Schema。这些模型将同时服务于：
- LLM 的 `with_structured_output()`
- 后续 Milestone 4 的数据库 ORM 映射

```python
from pydantic import BaseModel, Field
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

class JobTrend(BaseModel):
    """单个岗位的趋势分析"""
    job_title: str = Field(description="岗位名称（中文）")
    job_title_en: str = Field(description="岗位名称（英文）")
    zone: JobZone = Field(description="所属区域：red/yellow/green")
    trend_description: str = Field(description="趋势描述：为什么这个岗位在衰退/进化/增长")
    ai_impact: str = Field(description="AI 具体如何影响这个岗位")
    required_skills: list[RequiredSkill] = Field(description="该岗位需要的关键技能列表")
    demand_change: str = Field(description="需求变化趋势：如 '需求下降30%' 或 '年增长率50%'")
    source_urls: list[str] = Field(description="信息来源 URL 列表")
    source_names: list[str] = Field(description="信息来源名称列表，如 'WEF Future of Jobs Report 2025'")

class MarketInsight(BaseModel):
    """市场洞察（来自招聘平台数据）"""
    platform: str = Field(description="数据来源平台：如 LinkedIn、Boss直聘、Indeed")
    insight: str = Field(description="核心洞察")
    data_point: str = Field(description="关键数据点：如 'AI Engineer 岗位同比增长 74%'")
    date_observed: str = Field(description="数据观测日期或时间范围")

class JobTrendReport(BaseModel):
    """完整的 AI 就业趋势报告"""
    report_date: str = Field(description="报告生成日期")
    executive_summary: str = Field(description="执行摘要：200字以内的核心发现")
    declining_jobs: list[JobTrend] = Field(description="衰退区（Red Zone）岗位列表")
    evolving_jobs: list[JobTrend] = Field(description="进化区（Yellow Zone）岗位列表")
    emerging_jobs: list[JobTrend] = Field(description="新兴区（Green Zone）岗位列表")
    market_insights: list[MarketInsight] = Field(description="市场微观洞察列表")
    key_reports_referenced: list[str] = Field(description="引用的关键报告名称列表")
```

### 设计说明

- 每个 `JobTrend` 都必须包含 `source_urls`，确保信息可追溯。
- `JobZone` 使用枚举类型，确保分类严格。
- `MarketInsight` 单独拎出来，因为它的数据结构和岗位趋势不同。

### 验收标准

- [ ] 所有 Pydantic 模型能正常序列化/反序列化
- [ ] 模型的 JSON Schema 可以被 `with_structured_output()` 正确消费
- [ ] 手动构造一个 `JobTrendReport` 实例，确认字段完整

---

## Task 3.2：拆分研究子图 (SubGraph)

**文件目录：** `src/agent/research/`（新建子目录）

```
src/agent/research/
├── __init__.py
├── macro_research.py       # 宏观报告搜索子图
├── job_market_research.py  # 招聘市场数据搜索子图
├── tech_frontier_research.py  # 技术前沿搜索子图
└── state.py                # 子图共享状态
```

### 要做的事

将 M2 中的单一 Agent 拆分为 **3 个专注的研究子图**：

#### 子图 1：`macro_research`（宏观报告搜索）

- **职责：** 搜索 WEF、McKinsey、BCG、PwC、Gartner 等机构的报告
- **搜索策略：**
  - 搜索 "WEF Future of Jobs Report 2025 2026 key findings"
  - 搜索 "McKinsey GenAI impact on workforce"
  - 搜索 "AI 就业影响 报告 2025 2026"
- **最大搜索次数：** 3 次
- **输出：** 搜索到的文档内容追加到共享的 `documents` 列表

#### 子图 2：`job_market_research`（招聘市场数据）

- **职责：** 搜索 LinkedIn、Indeed、Boss直聘等平台的就业数据
- **搜索策略：**
  - 搜索 "LinkedIn emerging jobs report 2025 2026 AI"
  - 搜索 "AI related job growth statistics 2025 2026"
  - 搜索 "AIGC 大模型 招聘 岗位增长 数据"
- **最大搜索次数：** 3 次
- **输出：** 搜索到的文档内容追加到共享的 `documents` 列表

#### 子图 3：`tech_frontier_research`（技术前沿动态）

- **职责：** 搜索 AI Agent、大模型应用等最新技术动态和融资信息
- **搜索策略：**
  - 搜索 "AI agent startup funding 2025 2026"
  - 搜索 "AI 创业公司 融资 招聘 扩张"
- **最大搜索次数：** 2 次
- **输出：** 搜索到的文档内容追加到共享的 `documents` 列表

### 每个子图的内部结构

```
START → search_and_read → [条件：还有查询？] → search_and_read → ... → END
```

每个子图内部是一个简化的循环：
1. 从预定义的搜索步骤中取出一个 query
2. 调用 `search_web` 搜索
3. 对搜索结果中最相关的 1-2 个 URL 调用 `read_page`
4. 将内容追加到 `documents`
5. 检查是否还有下一步，有则继续，无则结束

### 设计说明

- 子图之间**互相独立**，理论上可以并行执行（LangGraph 支持 `Send` API 做并行分发，但 MVP 阶段可以先串行）。
- 每个子图有独立的搜索次数上限，总体可控。
- 共享同一套 `tools`，只是 Prompt 和搜索策略不同。

### 验收标准

- [ ] 三个子图可以独立运行并返回 `documents`
- [ ] 每个子图的搜索次数不超过预设上限
- [ ] 子图的 `documents` 能正确合并到主图的 `AgentState` 中

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
                    │  (生成各维度搜索计划)       │
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
                    │     format_output          │
                    │  (LLM + with_structured_   │
                    │   output → JobTrendReport) │
                    └────────────┬──────────────┘
                                 ▼
                         ┌──────────────────┐
                         │       END        │
                         └──────────────────┘
```

### 关键节点实现

1. **`summarize_findings` 节点**（核心！解决 Token 爆炸问题）

```python
async def summarize_findings(state: AgentState, config: RunnableConfig) -> dict:
    """
    将所有搜索到的原始文档压缩为一份结构化摘要。
    
    这是解决 Token 爆炸的关键节点：
    - 输入：可能包含数万字的原始网页内容
    - 输出：一份 3000-5000 字的结构化摘要
    
    压缩后再交给 format_output，确保不会超过 context window。
    """
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
    
    response = await llm.ainvoke([
        {"role": "system", "content": "你是一个信息整理专家。请精确提取关键信息，保留数据和来源。"},
        {"role": "user", "content": summary_prompt}
    ])
    
    return {"summary": response.content}
```

2. **`format_output` 节点**

```python
async def format_output(state: AgentState, config: RunnableConfig) -> dict:
    """
    将压缩后的摘要转换为严格的 JobTrendReport JSON。
    
    注意：这里的输入是 summary（几千字），而非原始 messages（可能几万字）。
    """
    model = load_chat_model(config).with_structured_output(JobTrendReport)
    
    result = await model.ainvoke([
        {"role": "system", "content": "根据以下研究摘要，生成一份结构化的 AI 就业趋势报告。"},
        {"role": "user", "content": state.summary}
    ])
    
    return {"final_report": result}
```

### 在 AgentState 中新增字段

| 字段 | 类型 | 用途 |
|------|------|------|
| `documents` | `Annotated[list[Document], reduce_docs]` | 所有子图搜索到的原始文档 |
| `summary` | `str` | `summarize_findings` 节点生成的压缩摘要 |
| `final_report` | `JobTrendReport \| None` | `format_output` 节点生成的结构化报告 |

### 验收标准

- [ ] `summarize_findings` 能将超长内容压缩到 5000 字以内
- [ ] `format_output` 能稳定输出合法的 `JobTrendReport` JSON
- [ ] 端到端运行：搜索 → 压缩 → 格式化，不出现 Token 超限错误

---

## Task 3.4：结构化输出的重试机制

**文件：** `src/agent/nodes.py`

### 要做的事

`with_structured_output` 在实践中并非 100% 成功（尤其是字段多、嵌套深的 Schema）。需要增加重试逻辑：

```python
async def format_output_with_retry(state: AgentState, config: RunnableConfig, max_retries: int = 3) -> dict:
    """带重试的结构化输出"""
    model = load_chat_model(config).with_structured_output(JobTrendReport)
    
    for attempt in range(max_retries):
        try:
            result = await model.ainvoke([
                {"role": "system", "content": FORMAT_PROMPT},
                {"role": "user", "content": state.summary}
            ])
            # 验证必填字段不为空
            assert len(result.declining_jobs) > 0 or len(result.emerging_jobs) > 0, "报告不能为空"
            return {"final_report": result}
        except Exception as e:
            if attempt == max_retries - 1:
                # 最后一次重试也失败，返回一个最小化的报告
                fallback_report = JobTrendReport(
                    report_date=str(date.today()),
                    executive_summary=f"结构化输出失败，原始摘要：{state.summary[:500]}...",
                    declining_jobs=[], evolving_jobs=[], emerging_jobs=[],
                    market_insights=[], key_reports_referenced=[]
                )
                return {"final_report": fallback_report}
            # 非最后一次，等待后重试
            await asyncio.sleep(1)
```

### 验收标准

- [ ] 结构化输出失败时能自动重试
- [ ] 3 次重试全部失败时能返回 fallback 报告而非崩溃
- [ ] 正常情况下第一次就成功（验证 Schema 设计的合理性）

---

## Task 3.5：端到端集成测试

### 要做的事

运行完整的图：`搜索 → 压缩 → 格式化`，验证输出的 JSON 质量。

```python
async def test_full_pipeline():
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "全面分析 AI 对就业市场的影响"}]},
        config={"recursion_limit": 50}
    )
    
    report = result["final_report"]
    
    # 基础质量检查
    assert isinstance(report, JobTrendReport)
    assert len(report.executive_summary) > 50
    assert len(report.declining_jobs) + len(report.evolving_jobs) + len(report.emerging_jobs) > 0
    
    # 来源检查
    all_jobs = report.declining_jobs + report.evolving_jobs + report.emerging_jobs
    for job in all_jobs:
        assert len(job.source_urls) > 0, f"{job.job_title} 缺少来源"
    
    # 打印报告
    print(report.model_dump_json(indent=2, ensure_ascii=False))
```

### 验收标准

- [ ] 输出的 JSON 可以被 `json.loads()` 正确解析
- [ ] 报告至少覆盖 Red/Yellow/Green 三个区域中的两个
- [ ] 每个岗位趋势都有来源 URL

---

## Milestone 3 完成标志 ✅

- [ ] Pydantic 数据模型定义完成且通过验证
- [ ] 三个研究子图能独立运行
- [ ] `summarize_findings` 节点有效解决 Token 爆炸问题
- [ ] `format_output` 节点能稳定输出 `JobTrendReport` JSON
- [ ] 结构化输出有重试和 fallback 机制
- [ ] 端到端测试通过，输出质量达标
