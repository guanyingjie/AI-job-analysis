# Milestone 5：消息推送 + 定时自动化 + 成本监控

**目标：** 将 Agent 部署为全自动后台服务，定时运行并推送报告摘要到你的手机/电脑，同时监控 API 调用成本，防止费用失控。

**前置依赖：** Milestone 4 完成

**预估耗时：** 2-3 小时

---

## Task 5.1：成本追踪机制（贯穿全图）

**文件：** `src/agent/cost_tracker.py`（新建）、`src/agent/nodes.py`（修改）

### 为什么要做这个？

这个 Agent 每次运行会消耗：
- **Tavily API：** 免费版每月 1000 次搜索。3 个子图 × 每个 2-3 次搜索 = 每次运行约 6-9 次搜索调用。如果每天跑一次，一个月消耗 180-270 次。
- **LLM API（Gemini/OpenAI）：** 多轮工具调用 + 摘要压缩 + 结构化输出，每次运行预估消耗 30,000-80,000 tokens。
- **Jina Reader API：** 免费版每天 100 次。

如果不做监控，费用可能在你不知情的情况下快速累积。

### 要做的事

> **关键设计决策：** `CostTracker` 不作为 `AgentState` 字段（避免序列化问题和 in-place mutation 反模式），而是通过 `contextvars.ContextVar` 实现 run-scoped 共享。每次图运行创建一个新的 `CostTracker` 实例，同一次运行内的所有节点和工具共享同一个实例。

1. **定义 `CostTracker` 类：**

```python
from dataclasses import dataclass, field
from datetime import datetime
from contextvars import ContextVar
from src.config import get_settings

# run-scoped context variable，每次图运行创建新实例
_cost_tracker_var: ContextVar["CostTracker"] = ContextVar("cost_tracker")


def get_cost_tracker() -> "CostTracker":
    """获取当前运行的 CostTracker 实例"""
    try:
        return _cost_tracker_var.get()
    except LookupError:
        # 如果没有设置，创建一个新的（fallback，不应该发生）
        tracker = CostTracker()
        _cost_tracker_var.set(tracker)
        return tracker


def init_cost_tracker() -> "CostTracker":
    """在每次图运行开始时调用，创建新的 CostTracker 并绑定到当前 context。
    预算上限从 config.py 的 Settings 读取，支持通过环境变量覆盖。"""
    settings = get_settings()
    tracker = CostTracker(
        max_budget_usd=settings.max_budget_usd,
        max_tavily_calls=settings.max_tavily_calls,
    )
    _cost_tracker_var.set(tracker)
    return tracker


@dataclass
class CostTracker:
    """追踪单次运行的 API 消耗"""
    
    # Token 消耗
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    
    # API 调用次数
    tavily_search_count: int = 0
    jina_read_count: int = 0
    llm_call_count: int = 0
    
    # 运行时间
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    
    # 费用预估（美元）
    estimated_cost_usd: float = 0.0
    
    # 预算上限（默认值仅用于 fallback，正式运行由 init_cost_tracker 从 Settings 注入）
    max_budget_usd: float = 0.50  # 单次运行预算上限 $0.50
    max_tavily_calls: int = 15     # 单次运行最大搜索次数（Tavily 安全上限）
    
    def add_llm_usage(self, input_tokens: int, output_tokens: int):
        """记录一次 LLM 调用的 token 消耗"""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.llm_call_count += 1
        self._update_cost()
    
    def add_tavily_call(self):
        self.tavily_search_count += 1
    
    def add_jina_call(self):
        self.jina_read_count += 1
    
    def is_budget_exceeded(self) -> bool:
        """检查是否超出预算"""
        return (
            self.estimated_cost_usd >= self.max_budget_usd or
            self.tavily_search_count >= self.max_tavily_calls
        )
    
    def _update_cost(self):
        """基于 Gemini 2.0 Flash 定价估算费用"""
        # Gemini 2.0 Flash: $0.10/1M input tokens, $0.40/1M output tokens (估算)
        input_cost = self.total_input_tokens * 0.10 / 1_000_000
        output_cost = self.total_output_tokens * 0.40 / 1_000_000
        self.estimated_cost_usd = input_cost + output_cost
    
    def get_duration_seconds(self) -> float:
        """获取运行时长（秒）"""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()
    
    def get_summary(self) -> str:
        """生成消耗摘要"""
        self.end_time = datetime.now()
        duration = self.get_duration_seconds()
        return (
            f"📊 运行统计\n"
            f"- 耗时: {duration:.1f}秒\n"
            f"- LLM 调用: {self.llm_call_count}次\n"
            f"- Token 消耗: {self.total_input_tokens:,} input + {self.total_output_tokens:,} output\n"
            f"- Tavily 搜索: {self.tavily_search_count}次\n"
            f"- Jina 阅读: {self.jina_read_count}次\n"
            f"- 预估费用: ${self.estimated_cost_usd:.4f}\n"
        )
```

2. **在工具和节点中使用 `get_cost_tracker()`：**

```python
# ---- search_web 工具（M4 去重逻辑之前插入预算检查）----
@tool
async def search_web(query: str) -> str:
    """搜索网页获取最新信息。"""
    tracker = get_cost_tracker()
    # ⭐ 先检查预算，再计数。避免"计数+1 但搜索未执行"导致 total_search_count 虚高
    if tracker.is_budget_exceeded():
        return json.dumps({
            "query": query, "results": [], "result_count": 0,
            "error": "⚠️ 已达到本次运行的 API 调用预算上限，停止搜索。"
        }, ensure_ascii=False)
    tracker.add_tavily_call()  # 预算充足，确认本次搜索会执行后再计数
    # ... M4 的去重 + Tavily 搜索逻辑


# ---- search_report_summary 工具（与 search_web 相同的预算检查策略）----
@tool
async def search_report_summary(report_name: str) -> str:
    """搜索报告公开摘要。"""
    tracker = get_cost_tracker()
    if tracker.is_budget_exceeded():
        return json.dumps({
            "query": report_name, "results": [], "result_count": 0,
            "error": "⚠️ 已达到本次运行的 API 调用预算上限，停止搜索。"
        }, ensure_ascii=False)
    tracker.add_tavily_call()
    # ... M4 的去重 + Tavily 搜索逻辑


# ---- read_page 工具（追踪 Jina 调用次数）----
@tool
async def read_page(url: str) -> str:
    """阅读网页内容。"""
    # ⭐ 先执行 M4 去重检查（不消耗任何 API 调用）
    repo = SourceRepository()
    normalized = repo.normalize_url(url)
    existing = repo.get_processed_source(normalized)
    if existing:
        # 已处理过的 URL 直接返回，不计入 Jina 调用次数
        return json.dumps({
            "url": url, "status": "already_processed", "content": None,
            "error": f"此 URL 已在 {existing.last_processed_date} 处理过。",
            "truncated": False,
        }, ensure_ascii=False)

    # 去重检查通过，确认需要实际获取页面后，才追踪 Jina 调用
    tracker = get_cost_tracker()
    tracker.add_jina_call()
    # ... 调用 _fetch_page_content + 标记已处理（M4 逻辑）


# ---- summarize_findings 节点（M3 架构中的 LLM 调用节点，追踪 Token 消耗 + 指数退避重试）----
# 注意：M3 架构中 call_model 已退役，LLM 调用分布在以下三个节点中。
async def summarize_findings(state: AgentState, config: RunnableConfig) -> dict:
    # ... M3 的 summary_prompt / combined 构造逻辑 ...

    # ⭐ 指数退避重试 + Token 追踪（在 M3 重试基础上增加 usage_metadata 记录）
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)

            # 记录 token 消耗
            tracker = get_cost_tracker()
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                tracker.add_llm_usage(
                    response.usage_metadata.get("input_tokens", 0),
                    response.usage_metadata.get("output_tokens", 0)
                )

            return {"summary": response.content}
        except Exception as e:
            if attempt == max_retries - 1:
                return {
                    "summary": f"摘要生成失败（已重试 {max_retries} 次）：{str(e)}。"
                    f"原始文档数量：{len(state.documents)}"
                }
            await asyncio.sleep(2 ** attempt)


# ---- create_research_plan / format_output_with_retry（使用 with_structured_output 时追踪 Token）----
# with_structured_output 默认只返回 Pydantic 对象（或 TypedDict），丢失原始 AIMessage 中的 usage_metadata。
# 使用 include_raw=True 可同时获取结构化输出和原始消息。
#
# ⚠️ 重大变更：include_raw=True 改变了返回类型！
# - 不加 include_raw：model.ainvoke() 直接返回 Pydantic 对象/TypedDict
# - 加 include_raw=True：model.ainvoke() 返回 {"raw": AIMessage, "parsed": ..., "parsing_error": ...}
#
# 因此 M2 的 create_research_plan 和 M3 的 format_output_with_retry 需要同步修改：

# ---- create_research_plan 需要修改（M2 原始代码使用 plan["steps"]，加入 include_raw + 重试后改为）：
async def create_research_plan(state: AgentState, config: RunnableConfig) -> dict:
    settings = get_settings()
    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=settings.llm_temperature,
    ).with_structured_output(ResearchPlan, include_raw=True)  # ⭐ 加入 include_raw

    messages = [
        {"role": "system", "content": PLANNING_PROMPT.format(max_searches=state.max_searches)},
        *state.messages,
    ]

    # ⭐ 指数退避重试（与 call_model / format_output_with_retry 保持一致）
    max_retries = 3
    for attempt in range(max_retries):
        try:
            raw_result = await model.ainvoke(messages)

            plan = raw_result["parsed"]    # ⭐ 之前是 plan = await model.ainvoke(...)
            raw_msg = raw_result["raw"]    # 原始 AIMessage，含 usage_metadata

            # 防御性检查：with_structured_output(include_raw=True) 解析失败时 parsed 为 None
            if plan is None:
                raise ValueError(f"结构化输出解析失败: {raw_result.get('parsing_error', 'unknown')}")

            # Token 追踪
            tracker = get_cost_tracker()
            if hasattr(raw_msg, 'usage_metadata') and raw_msg.usage_metadata:
                tracker.add_llm_usage(
                    raw_msg.usage_metadata.get("input_tokens", 0),
                    raw_msg.usage_metadata.get("output_tokens", 0)
                )

            return {
                "plan_steps": plan["steps"],  # plan 现在是 parsed 结果
                "step_index": 0,
                "current_step": None,
            }
        except Exception as e:
            if attempt == max_retries - 1:
                # 规划失败时返回空计划，让 dispatch_to_subgraphs 使用 fallback 默认查询
                return {
                    "plan_steps": [],
                    "step_index": 0,
                    "current_step": None,
                }
            await asyncio.sleep(2 ** attempt)


# ---- format_output_with_retry 需要修改（M3 原始代码使用 result.declining_jobs，加入 include_raw 后改为）：
async def format_output_with_retry(state: AgentState, config: RunnableConfig) -> dict:
    settings = get_settings()
    model = ChatGoogleGenerativeAI(
        model=settings.llm_model_name,
        temperature=0,
    ).with_structured_output(JobTrendReport, include_raw=True)  # ⭐ 加入 include_raw

    for attempt in range(MAX_FORMAT_RETRIES):
        try:
            raw_result = await model.ainvoke([
                {"role": "system", "content": FORMAT_PROMPT},
                {"role": "user", "content": state.summary}
            ])
            result = raw_result["parsed"]   # ⭐ 之前是 result = await model.ainvoke(...)
            raw_msg = raw_result["raw"]

            # 防御性检查：include_raw=True 解析失败时 parsed 为 None
            if result is None:
                raise ValueError(f"结构化输出解析失败: {raw_result.get('parsing_error', 'unknown')}")

            # Token 追踪
            tracker = get_cost_tracker()
            if hasattr(raw_msg, 'usage_metadata') and raw_msg.usage_metadata:
                tracker.add_llm_usage(
                    raw_msg.usage_metadata.get("input_tokens", 0),
                    raw_msg.usage_metadata.get("output_tokens", 0)
                )

            total_jobs = len(result.declining_jobs) + len(result.evolving_jobs) + len(result.emerging_jobs)
            assert total_jobs > 0, "报告必须至少包含一个岗位趋势"
            return {"final_report": result}
        except Exception as e:
            if attempt == MAX_FORMAT_RETRIES - 1:
                # ⭐ 与 M3 Task 3.4 一致的 min_length 安全处理
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
            await asyncio.sleep(2 ** attempt)
```

3. **在 `save_to_db` 中记录运行成本到 `reports` 表：**

> **重要：** 以下代码必须放在 M4 `save_to_db` 的 `with get_db_session() as session:` 块**内部**，在写入市场洞察之后、`return` 之前，确保成本数据与报告数据在同一事务中提交。

```python
async def save_to_db(state: AgentState) -> dict:
    """M5 版本：在 M4 基础上加入 update_report_cost 回填运行成本。
    ⭐ 继承 M4 的 try-except 保护，数据库错误不阻塞主流程。"""
    report = state.final_report
    if report is None:
        return {"db_save_status": "skipped - no report"}
    
    try:
        with get_db_session() as session:
            repo = ReportRepository(session=session)
            
            # ... M4 已有的报告写入逻辑（create_report, create_job_trend, create_market_insight）
            
            # ⭐ M5 新增：回填运行成本（必须在同一个 with 块内）
            tracker = get_cost_tracker()
            repo.update_report_cost(
                report_id=report_record.id,
                total_tokens=tracker.total_input_tokens + tracker.total_output_tokens,
                total_search_count=tracker.tavily_search_count,
                run_duration=tracker.get_duration_seconds(),
            )
            
            # 在 session 活跃时捕获 id，避免 with 块外访问 detached 对象
            saved_report_id = report_record.id
        
        return {"db_save_status": "success", "report_id": saved_report_id}

    except Exception as e:
        return {"db_save_status": f"error: {str(e)}"}
```

### 验收标准

- [ ] 每次运行结束后能打印完整的消耗摘要
- [ ] `CostTracker` 通过 `contextvars` 实现 run-scoped 共享，不污染 AgentState
- [ ] 超出预算时 `search_web` 和 `search_report_summary` 均返回错误 JSON，Agent 能优雅停止（不崩溃）
- [ ] `read_page` 的 Jina 调用次数被正确追踪（`add_jina_call()`）
- [ ] LLM Token 消耗在 `summarize_findings` 等节点中被追踪（`with_structured_output` 节点使用 `include_raw=True`）
- [ ] `CostTracker` 的预算上限从 `config.py` Settings 读取，支持环境变量覆盖
- [ ] 运行成本被记录到数据库 `reports` 表中
- [ ] 可以通过 SQL 查询历史运行的成本趋势

### M5 最终版 `state.py`（M1-M5 所有字段合并）

> **⭐ 关键：** 以下是项目完成后 `src/agent/state.py` 的最终完整代码。包含 M1-M5 所有字段，开发者可直接使用。

```python
from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AnyMessage
from langchain_core.documents import Document
from langgraph.graph.message import add_messages

from src.config import get_settings
from src.agent.types import ResearchStep
from src.agent.models import JobTrendReport


def reduce_docs(existing: list[Document] | None, new: list[Document] | None) -> list[Document]:
    """合并文档列表的 reducer：按 URL 去重，保留更长版本，每个截断到 4000 字符"""
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
    """Agent 全局状态（最终版：M1-M5 所有字段）"""

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

    # ── M4 持久化字段 ──
    db_save_status: str = ""
    report_id: int | None = None

    # ── M5 通知字段 ──
    notification_status: str = ""


@dataclass(kw_only=True)
class InputState:
    """对外暴露的窄接口，用于 graph.ainvoke() 的输入"""
    messages: Annotated[list[AnyMessage], add_messages]
```

### M5 最终版 `nodes.py` import 列表

> **⭐ 关键：** 以下是 M5 阶段 `nodes.py` 顶部的完整 import 列表，合并了 M1-M5 所有依赖。

```python
# ---- src/agent/nodes.py M5 最终版完整 import 列表 ----
import asyncio
import json
from typing import Literal
from datetime import date
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage
from langchain_core.documents import Document
from langchain_google_genai import ChatGoogleGenerativeAI
from src.config import get_settings
from src.agent.state import AgentState
from src.agent.tools import tools
from src.agent.prompts import SYSTEM_PROMPT, PLANNING_PROMPT, FORMAT_PROMPT
from src.agent.types import ResearchPlan
from src.agent.models import JobTrendReport
from src.agent.cost_tracker import get_cost_tracker                             # ⭐ M5 新增
from src.agent.research.defaults import (
    DEFAULT_MACRO_QUERIES, DEFAULT_JOB_MARKET_QUERIES, DEFAULT_TECH_QUERIES,
)
from src.agent.research.macro_research import build_research_subgraph as build_macro
from src.agent.research.job_market_research import build_research_subgraph as build_job_market
from src.agent.research.tech_frontier_research import build_research_subgraph as build_tech
from src.db.session import get_db_session                                       # ⭐ M4 新增
from src.db.repository import ReportRepository                                  # ⭐ M4 新增
from src.notification.base import get_notifier, format_report_for_notification  # ⭐ M5 新增
```

### M5 最终版 `tools.py` import 列表

> **⭐ 关键：** 以下是 M5 阶段 `tools.py` 顶部的完整 import 列表，在 M4 基础上新增 `get_cost_tracker`。

```python
# ---- src/agent/tools.py M5 最终版完整 import 列表 ----
import json
import hashlib
import asyncio
import httpx
from langchain_core.tools import tool
from src.config import get_settings
from src.db.repository import SourceRepository
from src.agent.cost_tracker import get_cost_tracker  # ⭐ M5 新增
from tavily import TavilyClient

settings = get_settings()
tavily_client = TavilyClient(api_key=settings.tavily_api_key)

JINA_READER_PREFIX = "https://r.jina.ai/"
JINA_HEADERS = {"Authorization": f"Bearer {settings.jina_api_key}"} if settings.jina_api_key else {}
```

---

## Task 5.2：编写通知模块

**文件：** `src/notification/`（新建子目录）

```
src/notification/
├── __init__.py
├── base.py           # 通知接口抽象 + 工厂函数
├── feishu.py         # 飞书 Webhook
├── telegram.py       # Telegram Bot
└── console.py        # 控制台输出（开发用 fallback）
```

### 要做的事

1. **定义通知接口 + 工厂函数：**

```python
# base.py
from abc import ABC, abstractmethod
from src.config import get_settings


class Notifier(ABC):
    @abstractmethod
    async def send(self, title: str, content: str) -> bool:
        """发送通知，返回是否成功"""
        pass


def get_notifier() -> Notifier:
    """
    根据 config.py 的 notification_channel 设置返回对应的通知器。
    支持的渠道：feishu / telegram / console
    """
    settings = get_settings()
    channel = settings.notification_channel.lower()
    
    if channel == "feishu":
        from src.notification.feishu import FeishuNotifier
        if not settings.feishu_webhook_url:
            print("⚠️ FEISHU_WEBHOOK_URL 未配置，降级到控制台通知")
            from src.notification.console import ConsoleNotifier
            return ConsoleNotifier()
        return FeishuNotifier(webhook_url=settings.feishu_webhook_url)
    
    elif channel == "telegram":
        from src.notification.telegram import TelegramNotifier
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            print("⚠️ Telegram 配置不完整，降级到控制台通知")
            from src.notification.console import ConsoleNotifier
            return ConsoleNotifier()
        return TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    
    else:
        from src.notification.console import ConsoleNotifier
        return ConsoleNotifier()
```

2. **飞书 Webhook 实现（推荐，中国区最方便）：**

```python
# feishu.py
import httpx
from src.notification.base import Notifier


class FeishuNotifier(Notifier):
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    async def send(self, title: str, content: str) -> bool:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue"
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content
                    }
                ]
            }
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            print(f"⚠️ 飞书通知发送失败: {e}")
            return False
```

3. **Telegram Bot 实现（国际区推荐）：**

```python
# telegram.py
import httpx
from src.notification.base import Notifier


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
    
    async def send(self, title: str, content: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": f"**{title}**\n\n{content}",
            "parse_mode": "Markdown"
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            print(f"⚠️ Telegram 通知发送失败: {e}")
            return False
```

4. **控制台 Fallback（开发期）：**

```python
# console.py
from src.notification.base import Notifier


class ConsoleNotifier(Notifier):
    async def send(self, title: str, content: str) -> bool:
        print(f"\n{'='*60}")
        print(f"📢 {title}")
        print(f"{'='*60}")
        print(content)
        return True
```

5. **格式化报告为推送消息：**

**文件：** `src/notification/base.py`（与 `Notifier` 和 `get_notifier()` 放在同一文件中）

```python
from src.agent.models import JobTrendReport


def format_report_for_notification(report: JobTrendReport, cost_summary: str) -> str:
    """将 JobTrendReport 格式化为适合推送的 Markdown"""
    
    lines = [f"📅 报告日期: {report.report_date}\n"]
    lines.append(f"📋 **摘要**: {report.executive_summary}\n")
    
    if report.declining_jobs:
        lines.append("🔴 **衰退岗位 (Red Zone)**")
        for job in report.declining_jobs:
            lines.append(f"  - {job.job_title} ({job.job_title_en}): {job.demand_change}")
    
    if report.evolving_jobs:
        lines.append("\n🟡 **进化岗位 (Yellow Zone)**")
        for job in report.evolving_jobs:
            lines.append(f"  - {job.job_title} ({job.job_title_en}): {job.ai_impact[:50]}...")
    
    if report.emerging_jobs:
        lines.append("\n🟢 **新兴岗位 (Green Zone)**")
        for job in report.emerging_jobs:
            skills = ", ".join([s.skill_name for s in job.required_skills[:3]])
            lines.append(f"  - {job.job_title} ({job.job_title_en}): 需要 [{skills}]")
    
    lines.append(f"\n{cost_summary}")
    
    return "\n".join(lines)
```

### 验收标准

- [ ] 飞书 Webhook 能成功发送格式化的 Markdown 消息
- [ ] Telegram Bot 能成功发送消息
- [ ] 未配置通知渠道时自动 fallback 到控制台输出
- [ ] 通知发送失败时不抛异常，返回 `False` 并打印警告
- [ ] `get_notifier()` 工厂函数根据 `config.py` 配置正确返回通知器
- [ ] 通知内容包含报告摘要 + 运行成本统计

---

## Task 5.3：新增"通知推送"节点

**文件：** `src/agent/nodes.py`（修改）、`src/agent/graph.py`（修改）

### 要做的事

1. **新增 `send_notification` 节点：**

```python
from src.notification.base import get_notifier, format_report_for_notification
from src.agent.cost_tracker import get_cost_tracker

async def send_notification(state: AgentState) -> dict:
    """推送报告摘要到配置的通知渠道"""
    notifier = get_notifier()
    tracker = get_cost_tracker()
    
    report = state.final_report
    cost_summary = tracker.get_summary()
    
    if report:
        title = f"🤖 AI 就业趋势报告 - {report.report_date}"
        content = format_report_for_notification(report, cost_summary)
    else:
        title = "⚠️ AI 就业趋势分析 - 运行异常"
        content = f"本次运行未能生成有效报告。\n\n{cost_summary}"
    
    success = await notifier.send(title, content)
    
    return {"notification_status": "sent" if success else "failed"}
```

2. **修改图的末端流程：**

```
... → format_output_with_retry → save_to_db → send_notification → END
```

```python
# 在 M4 的 builder 基础上，将 save_to_db → END 改为 save_to_db → send_notification → END
from src.agent.nodes import send_notification

builder.add_node("send_notification", send_notification)

# 移除 M4 的 builder.add_edge("save_to_db", END)，替换为：
builder.add_edge("save_to_db", "send_notification")
builder.add_edge("send_notification", END)
```

### 在 AgentState 中新增字段

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `notification_status` | `str` | `""` | 通知发送状态 |

### 验收标准

- [ ] 图运行完成后自动发送通知
- [ ] 通知内容可读、信息量足够
- [ ] 通知失败不影响主流程（不抛异常，返回 `"failed"` 状态）

---

## Task 5.4：编写入口执行脚本 (Main Script)

**文件：** `run_agent.py`（重构，从 M1 的测试脚本升级为正式入口）

### 要做的事

```python
#!/usr/bin/env python3
"""AI 就业趋势分析 Agent 入口脚本"""

import asyncio
import sys
from datetime import date
from dotenv import load_dotenv

from src.agent.graph import graph
from src.agent.cost_tracker import init_cost_tracker, get_cost_tracker
from src.db.init_db import init_database
from src.notification.base import get_notifier

load_dotenv()


async def main():
    """主运行函数"""
    # 1. 初始化数据库（幂等操作）
    init_database()
    
    # 2. 初始化成本追踪器（run-scoped，通过 contextvars 共享）
    init_cost_tracker()
    
    # 3. 构造当天的搜索 Prompt
    today = date.today().isoformat()
    prompt = (
        f"今天是 {today}。请全面搜索和分析 AI 技术对全球就业市场的最新影响。\n"
        f"重点关注：\n"
        f"1. 近一个月内权威机构（WEF、McKinsey、BCG 等）发布的相关报告\n"
        f"2. 招聘平台（LinkedIn、Indeed 等）的最新就业数据变化\n"
        f"3. AI 领域的最新技术动态和创业融资信号\n"
        f"请识别衰退、进化和新兴三类岗位，并给出具体的岗位名称和分析。"
    )
    
    # 4. 运行 Agent
    print(f"🚀 启动 AI 就业趋势分析 Agent... ({today})")
    print(f"{'='*60}")
    
    try:
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"recursion_limit": 50}
        )
        
        # 5. 打印运行结果
        status = result.get("db_save_status", "unknown")
        notification = result.get("notification_status", "unknown")
        print(f"\n{'='*60}")
        print(f"✅ 运行完成")
        print(f"   数据库写入: {status}")
        print(f"   通知推送: {notification}")
        
        # 6. 打印成本摘要
        tracker = get_cost_tracker()
        print(tracker.get_summary())
        
    except Exception as e:
        print(f"\n❌ Agent 运行失败: {e}")
        # 尝试发送错误通知
        try:
            notifier = get_notifier()
            await notifier.send(
                "❌ AI 就业分析 Agent 运行失败",
                f"错误信息: {str(e)}\n日期: {today}"
            )
        except Exception:
            pass  # 通知也失败了，静默处理
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
```

### 验收标准

- [ ] `python run_agent.py` 能端到端运行完成
- [ ] 运行结束后数据库有新数据、通知已发送
- [ ] 运行出错时能发送错误通知
- [ ] 控制台输出清晰展示运行状态和成本摘要

---

## Task 5.5：Docker 化与定时部署

**文件：** `Dockerfile`、`docker-compose.yml`、`.github/workflows/run-agent.yml`

### 要做的事

#### 方案 A：Docker + Cron（自有服务器/NAS）

1. **Dockerfile：**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen

# 复制代码
COPY src/ src/
COPY run_agent.py .

# 创建数据目录
RUN mkdir -p /app/data

# 运行入口
CMD ["uv", "run", "python", "run_agent.py"]
```

2. **docker-compose.yml：**

```yaml
version: '3.8'
services:
  agent:
    build: .
    env_file: .env
    volumes:
      - ./data:/app/data  # SQLite 数据库持久化
    # 不设 restart，由 cron 触发
```

3. **Crontab 配置：**

```bash
# 每周一和周四早上 8 点运行（一周跑两次，平衡信息新鲜度和 API 成本）
0 8 * * 1,4 cd /path/to/AI-job-analysis && docker compose run --rm agent >> /var/log/job-agent.log 2>&1
```

#### 方案 B：GitHub Actions（无服务器，推荐起步方案）

> **⚠️ SQLite 持久化限制：** GitHub Actions 每次运行是全新的环境，SQLite 文件无法跨次运行保留。
> 以下方案通过 `actions/download-artifact` 恢复上次的 DB 文件实现"伪持久化"，但有以下局限：
> - Artifact 默认保留 90 天，过期后 DB 丢失
> - 并发运行可能导致 DB 冲突
> - **推荐长期方案：** 使用外部 PostgreSQL（如 Supabase 免费版）替代 SQLite

```yaml
# .github/workflows/run-agent.yml
name: Run AI Job Analysis Agent

on:
  schedule:
    # UTC 时间：每周一和周四 00:00（北京时间 08:00）
    - cron: '0 0 * * 1,4'
  workflow_dispatch:  # 支持手动触发

jobs:
  analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 10  # 硬性超时，防止 Agent 卡住
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: |
          pip install uv
          uv sync --frozen
      
      - name: Ensure data directory exists
        run: mkdir -p data
      
      - name: Restore previous database
        uses: dawidd6/action-download-artifact@v3
        with:
          name: job-analysis-db
          path: data/
          search_artifacts: true
          if_no_artifact_found: warn
        continue-on-error: true  # 首次运行时没有历史 artifact
      
      - name: Run Agent
        env:
          GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
          TAVILY_API_KEY: ${{ secrets.TAVILY_API_KEY }}
          JINA_API_KEY: ${{ secrets.JINA_API_KEY }}
          FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
          NOTIFICATION_CHANNEL: feishu
          DATABASE_URL: sqlite:///data/job_analysis.db
        run: uv run python run_agent.py
      
      - name: Upload database artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: job-analysis-db
          path: data/job_analysis.db
          retention-days: 90
          overwrite: true
```

#### 方案 C：Google Cloud Run Jobs + Cloud Scheduler（生产级）

- 考虑到你的 `talent-marketplace-agent` 已经使用了 Pulumi + Cloud Run 的部署方式，这个方案最符合你的技术栈。
- 使用 Cloud Run Jobs（而非 Cloud Run Services），因为这是一次性运行任务。
- Cloud Scheduler 触发 Cloud Run Job，和 crontab 等效但更可靠。
- 搭配 Cloud SQL（PostgreSQL）实现真正的持久化和跨次去重。
- 此方案的实现细节可在后续需要时展开。

### 推荐

**起步用方案 B（GitHub Actions）**，零运维成本。等需求稳定后再迁移到方案 C。

### 验收标准

- [ ] Docker 镜像能成功构建并运行
- [ ] GitHub Actions workflow 能手动触发并成功运行
- [ ] 数据库文件通过 artifact 实现跨次运行恢复（含 `download-artifact` 步骤）
- [ ] 定时触发配置正确（通过 `workflow_dispatch` 手动测试验证）

---

## 最终项目目录结构

```
AI-job-analysis/
├── .env                          # 环境变量（.gitignore）
├── .env.example                  # 环境变量模板
├── .github/
│   └── workflows/
│       └── run-agent.yml         # GitHub Actions 定时任务
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
├── run_agent.py                  # 入口脚本
├── data/
│   ├── .gitkeep
│   └── job_analysis.db           # SQLite 数据库（.gitignore）
├── src/
│   ├── __init__.py
│   ├── config.py                 # 配置管理（pydantic-settings，统一环境变量）
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── state.py              # 全局状态定义
│   │   ├── types.py              # 共享类型（ResearchStep, ResearchPlan 等，避免循环导入）
│   │   ├── tools.py              # 工具函数（search_web, read_page 等，返回 JSON str）
│   │   ├── nodes.py              # 节点函数（create_research_plan, summarize_findings, save_to_db 等）
│   │   ├── graph.py              # 主图构建与编译
│   │   ├── prompts.py            # 所有 Prompt 模板
│   │   ├── models.py             # Pydantic 数据模型（Source 绑定 URL+名称）
│   │   ├── cost_tracker.py       # 成本追踪（contextvars run-scoped）
│   │   └── research/             # 研究子图（接收 M2 动态规划的 steps）
│   │       ├── __init__.py
│   │       ├── defaults.py       # Fallback 查询常量
│   │       ├── state.py
│   │       ├── macro_research.py
│   │       ├── job_market_research.py
│   │       └── tech_frontier_research.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py             # SQLAlchemy ORM 模型
│   │   ├── repository.py         # 数据访问层（SourceRepository + ReportRepository）
│   │   ├── session.py            # 数据库会话管理（get_db_session context manager）
│   │   └── init_db.py            # 数据库初始化
│   └── notification/
│       ├── __init__.py
│       ├── base.py               # 通知接口抽象 + get_notifier() 工厂函数
│       ├── feishu.py             # 飞书通知
│       ├── telegram.py           # Telegram 通知
│       └── console.py            # 控制台通知（开发用）
├── tests/
│   ├── __init__.py
│   └── test_golden_cases.py      # Golden Test Cases（含离线 mock 测试）
└── milestone-*.md                # 里程碑计划文档
```

---

## Milestone 5 完成标志 ✅

- [ ] `CostTracker` 通过 `contextvars` 实现 run-scoped 共享，不污染 AgentState
- [ ] 成本追踪贯穿整个 Agent 运行过程，每次运行有详细的消耗报告
- [ ] 至少实现一个通知渠道（飞书/Telegram/控制台），`get_notifier()` 工厂函数完整
- [ ] 通知发送失败不抛异常，优雅降级
- [ ] `run_agent.py` 入口脚本完善，支持端到端自动运行
- [ ] Docker 镜像构建成功
- [ ] GitHub Actions 定时任务配置完成（含 DB artifact 恢复），手动触发测试通过
- [ ] 超出 API 预算时工具返回错误 JSON，Agent 能优雅停止
- [ ] 运行成本持久化到数据库，可查询历史趋势

---

## 🎉 全部里程碑完成后的系统能力

| 能力 | 描述 |
|------|------|
| 🔍 自主搜索 | 多维度并行搜索权威报告、招聘数据、技术前沿 |
| 🧠 智能收敛 | 有研究计划、有搜索上限、遇到付费墙能自动降级 |
| 📊 结构化输出 | 输出严格的 JSON，可直接入库和展示（Source 模型绑定 URL+名称） |
| 💾 持久化存储 | 所有数据入库，支持历史趋势查询（ON DELETE SET NULL 保护数据） |
| 🔄 去重机制 | 跨次运行的 URL 去重（含 URL 规范化），不浪费 API 调用 |
| 📱 自动推送 | 报告摘要自动推送到手机（get_notifier 工厂函数） |
| ⏰ 定时运行 | 全自动，零人工干预（GitHub Actions + DB artifact 恢复） |
| 💰 成本可控 | 每次运行有预算上限，费用可追踪（contextvars run-scoped） |
