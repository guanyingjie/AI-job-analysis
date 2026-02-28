# Milestone 1 TODO — 遗留事项

## ⚠️ 运行前必须完成

- [ ] **配置 `.env` 文件**：项目根目录下创建 `.env` 文件，填入真实 API Key：
  ```env
  GOOGLE_API_KEY=你的Gemini_API_Key
  TAVILY_API_KEY=你的Tavily_API_Key
  JINA_API_KEY=你的Jina_API_Key（可选，留空也可以）
  ```
  > `.env` 文件被 gitignore 屏蔽，无法通过编辑器自动创建，请手动创建。

- [ ] **端到端运行验证**：填入 API Key 后运行 `uv run python run_agent.py`，确认：
  - Agent 能自主搜索 → 阅读 → 总结
  - 控制台可看到 `search_web` / `read_page` 工具调用过程
  - Agent 在合理轮次内（≤5 次搜索）停止并输出总结
  - 搜索次数安全阀（`search_count >= max_searches` 强制结束）工作正常

## ✅ 已完成

- [x] `uv init` + 安装所有依赖（langchain-core, langgraph, langchain-google-genai, tavily-python, httpx, beautifulsoup4, pydantic, pydantic-settings, python-dotenv）
- [x] `src/config.py` — pydantic-settings 配置管理，支持 `.env` 文件和环境变量
- [x] `src/agent/state.py` — `AgentState` + `InputState` 定义，含 `messages`、`search_count`、`max_searches`
- [x] `src/agent/tools.py` — 3 个工具：`search_web`、`read_page`（Jina 优先 + BS4 降级）、`search_report_summary`
- [x] `src/agent/nodes.py` — `call_model`（LLM 调用 + System Prompt）、`count_search_calls`（搜索计数）、`should_continue`（条件路由）
- [x] `src/agent/graph.py` — StateGraph 构建：`START → agent → should_continue → tools → count_search_calls → agent → ... → END`
- [x] `run_agent.py` — 测试入口脚本
- [x] 全部模块导入验证通过，无 lint 错误

## 📝 已知限制 / 后续 Milestone 改进点

- `search_web` / `search_report_summary` 中的 Tavily SDK 是同步调用，M4 会用 `asyncio.to_thread()` 包装
- System Prompt 为初稿，M2 会提取到 `src/agent/prompts.py` 并深度调优
- `read_page` 的核心获取逻辑 M4 会提取为 `_fetch_page_content()` 内部 helper
- 工具去重逻辑将在 M4 添加
- `.gitignore` 文件尚未创建（当前依赖上层 git 配置或全局 gitignore）
