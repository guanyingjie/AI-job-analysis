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

2. 定义 Planning Prompt：

```
你是一个专业的 AI 与就业市场研究规划师。

根据用户的研究需求，请制定一个不超过 5 步的搜索计划。每一步是一个具体的搜索查询。

你的搜索计划应该覆盖以下维度（按优先级排列）：
1. 权威报告搜索：搜索 WEF、McKinsey、BCG 等发布的关于 AI 对就业影响的报告
2. 招聘市场数据：搜索 LinkedIn、Indeed 等平台关于 AI 相关岗位增长的数据
3. 技术前沿动态：搜索近期 AI 创业公司融资、新产品发布等信号

注意事项：
- 每个搜索查询要精确、具体，避免过于宽泛
- 中英文查询各占一半（中文市场和全球市场都要覆盖）
- 优先搜索最近 3 个月内的信息
- 总共不超过 {max_searches} 个搜索步骤

请以 JSON 格式输出你的计划：
{{"steps": ["搜索查询1", "搜索查询2", ...]}}
```

3. 使用 `with_structured_output` 确保输出为结构化的 `Plan` 对象：

```python
class ResearchPlan(TypedDict):
    steps: list[str]
```

### 设计说明

- 让 LLM 先规划再执行，能**大幅减少无效搜索**，节省 API 调用费用。
- 限制步骤数 = 限制 Token 消耗，双重保险。

### 验收标准

- [ ] Agent 能根据用户 query 生成 3-5 步的搜索计划
- [ ] 搜索计划覆盖中英文双语查询
- [ ] 搜索计划覆盖至少 2 个维度（报告、招聘数据、技术动态）

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
当满足以下任一条件时，你应该停止搜索并开始总结：
- 你已经从至少 3 个不同信源获取了有效信息
- 你已经用完所有搜索步骤
- 新搜索的结果与已有信息高度重复
- 你已经覆盖了"衰退区"、"进化区"、"新兴区"三个分类的基本信息
```

3. **付费墙应对策略**（强化 M1 的降级逻辑）：

```
如果尝试阅读某篇文章时发现内容为空或提示需要付费：
1. 不要再次尝试阅读同一个 URL
2. 使用 search_report_summary 工具搜索该报告的公开摘要
3. 如果仍然找不到，跳过此来源，继续下一个搜索步骤
```

### 验收标准

- [ ] Agent 不再无目的地重复搜索相似内容
- [ ] Agent 在遇到付费墙时能自动切换策略
- [ ] Agent 能在 3-5 轮搜索内收敛并给出有信息量的总结
- [ ] 每次搜索的 query 明显不同（覆盖不同维度）

---

## Task 2.3：修改图结构 — 引入 Planning 阶段

**文件：** `src/agent/graph.py`

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
              ┌────▶│  conduct_research    │◀─────┐
              │     │  (执行计划中的一步)    │      │
              │     └──────────┬───────────┘      │
              │                ▼                   │
              │     ┌──────────────────────┐      │
              │     │  agent (LLM 分析结果) │      │
              │     └──────────┬───────────┘      │
              │                ▼                   │
              │     ┌──────────────────────┐      │
              │     │  should_continue?    │      │
              │     └───┬──────────────┬───┘      │
              │         ▼              ▼           │
              │  ┌────────────┐  ┌──────────────┐ │
              │  │   tools    │  │ check_plan   │ │
              │  └──────┬─────┘  │ (还有步骤？)  │ │
              │         │        └───┬────────┬──┘ │
              └─────────┘            ▼        ▼    │
                               ┌────────┐         │
                               │  END   │    (有) ─┘
                               └────────┘
```

### 关键逻辑

```python
def check_plan_finished(state: AgentState) -> Literal["conduct_research", "end"]:
    """检查研究计划是否还有未执行的步骤"""
    if len(state.steps) > 0:
        return "conduct_research"
    return "end"
```

### 在 AgentState 中新增字段

| 字段 | 类型 | 用途 |
|------|------|------|
| `steps` | `list[str]` | 研究计划的步骤列表 |
| `current_step` | `str` | 当前正在执行的搜索步骤 |

### 验收标准

- [ ] Agent 先输出研究计划，再按步骤执行搜索
- [ ] 每完成一步，`steps` 列表自动弹出该步骤
- [ ] 所有步骤执行完毕后，Agent 自动收敛到总结阶段

---

## Task 2.4：全局错误处理与容错机制

**文件：** `src/agent/tools.py`、`src/agent/nodes.py`

### 要做的事

1. **工具级别的错误处理**：

```python
@tool
def search_web(query: str) -> str:
    """搜索网页获取信息"""
    try:
        results = tavily_client.search(query=query, max_results=5)
        # ... 格式化结果
    except Exception as e:
        return f"搜索失败：{str(e)}。请尝试换一个搜索关键词重试。"
```

2. **节点级别的错误处理**：

```python
async def call_model(state: AgentState, config: RunnableConfig) -> dict:
    try:
        response = await llm.ainvoke(messages)
        return {"messages": [response]}
    except Exception as e:
        # LLM 调用失败时，返回一个提示消息，而非让整个 Agent 崩溃
        error_msg = AIMessage(content=f"模型调用出错，正在重试... 错误信息: {str(e)}")
        return {"messages": [error_msg]}
```

3. **全局 fallback**：在 `run_agent.py` 中包裹顶层异常处理。

4. **超时控制**：为 `read_page` 设置 10 秒超时。

```python
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.get(url)
```

### 验收标准

- [ ] Tavily API 故障时 Agent 不崩溃，能给出有意义的提示
- [ ] `read_page` 超时时能优雅返回错误信息
- [ ] LLM API 偶发故障时能重试或跳过
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

### 验收标准

- [ ] 三个测试用例全部通过
- [ ] 每个用例的运行时间 < 2 分钟
- [ ] Agent 的输出包含具体、有引用来源的信息

---

## Milestone 2 完成标志 ✅

- [ ] Agent 具备 "计划 → 执行 → 总结" 的三阶段工作流
- [ ] Prompt 已调优，搜索行为有策略、有纪律
- [ ] 所有工具和节点都有错误处理
- [ ] 付费墙降级策略经过实测验证
- [ ] Golden Test Cases 全部通过
