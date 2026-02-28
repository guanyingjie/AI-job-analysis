# Milestone 3 遗留事项

## 已完成 ✅

- [x] Task 3.1：Pydantic 数据模型（`src/agent/models.py`）— JobZone / Source / JobTrend / MarketInsight / JobTrendReport
- [x] Task 3.2：子图拆分 — `src/agent/research/` 目录，含 SubgraphState / build_research_subgraph / defaults.py / 三个维度模块
- [x] Task 3.3：主图重构为线性流水线 — create_research_plan → dispatch_to_subgraphs → research_executor → summarize_findings → format_output_with_retry → END
- [x] Task 3.3：AgentState 更新 — 新增 macro_queries / job_market_queries / tech_queries / documents / summary / final_report 字段 + reduce_docs reducer
- [x] Task 3.4：FORMAT_PROMPT + format_output_with_retry 带重试 & fallback
- [x] Task 3.5：run_agent.py 更新为 M3 结构化 JSON 输出
- [x] 离线测试全部通过（13 passed, 3 online skipped）

## 待验证（需要在线 API Key）🔶

- [ ] **在线端到端测试**：运行 `uv run pytest tests/test_golden_cases.py -v --run-online` 验证完整流水线
  - Case 1：基础搜索能力 → 生成 JobTrendReport
  - Case 2：付费墙降级 → 子图内 search_report_summary fallback
  - Case 3：完整流水线 → 报告覆盖 Red/Yellow/Green + Source 绑定
- [ ] **手动运行 `uv run python run_agent.py`**：验证端到端日志输出和结构化 JSON 质量

## 后续优化建议（非阻塞）📝

1. **子图并行执行**：当前三个子图串行运行（MVP），LangGraph 支持 `Send` API 做并行分发，可提升 2-3 倍速度
2. **M4 Tavily asyncio.to_thread()**：Tavily SDK 是同步调用，目前在 async 函数中直接调用，M4 应使用 `asyncio.to_thread()` 包装避免阻塞事件循环
3. **M4 工具去重**：子图 search_and_read 中应检查 URL 是否已被其他子图访问过（当前依赖主图 reduce_docs 做事后去重）
4. **M4 数据库持久化**：在 format_output_with_retry 之后插入 save_to_db 节点
5. **M5 通知渠道**：在 save_to_db 之后追加 send_notification 节点
6. **create_research_plan 的 PLANNING_PROMPT**：可根据在线测试效果进一步调优维度分配策略
