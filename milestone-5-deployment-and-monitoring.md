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

1. **定义 `CostTracker` 类：**

```python
from dataclasses import dataclass, field
from datetime import datetime

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
    
    # 预算上限
    max_budget_usd: float = 0.50  # 单次运行预算上限 $0.50
    max_tavily_calls: int = 15     # 单次运行最大搜索次数
    
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
    
    def get_summary(self) -> str:
        """生成消耗摘要"""
        self.end_time = datetime.now()
        duration = (self.end_time - self.start_time).total_seconds()
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

2. **将 `CostTracker` 集成到 `AgentState`：**

```python
@dataclass(kw_only=True)
class AgentState(InputState):
    # ... 其他字段
    cost_tracker: CostTracker = field(default_factory=CostTracker)
```

3. **在工具和节点中记录消耗：**

```python
# 在 search_web 中
@tool
def search_web(query: str, state: AgentState) -> str:
    # 注意：工具函数无法直接访问 state，
    # 需要在 ToolNode 的前/后钩子中更新 cost_tracker，
    # 或使用全局的 CostTracker 单例。
    cost_tracker.add_tavily_call()
    if cost_tracker.is_budget_exceeded():
        return "⚠️ 已达到本次运行的 API 调用预算上限，停止搜索。"
    # ... 正常搜索逻辑

# 在 call_model 中
async def call_model(state: AgentState, config: RunnableConfig) -> dict:
    response = await llm.ainvoke(messages)
    # 记录 token 消耗
    if hasattr(response, 'usage_metadata'):
        state.cost_tracker.add_llm_usage(
            response.usage_metadata.get("input_tokens", 0),
            response.usage_metadata.get("output_tokens", 0)
        )
    return {"messages": [response]}
```

4. **在 `save_to_db` 中记录运行成本到 `reports` 表：**

```python
async def save_to_db(state: AgentState) -> dict:
    # ... 保存报告数据
    
    # 记录运行成本
    repo.update_report_cost(
        report_id=report_record.id,
        total_tokens=state.cost_tracker.total_input_tokens + state.cost_tracker.total_output_tokens,
        total_search_count=state.cost_tracker.tavily_search_count,
        run_duration=state.cost_tracker.get_duration(),
    )
```

### 验收标准

- [ ] 每次运行结束后能打印完整的消耗摘要
- [ ] 超出预算时 Agent 能优雅停止（不崩溃）
- [ ] 运行成本被记录到数据库 `reports` 表中
- [ ] 可以通过 SQL 查询历史运行的成本趋势

---

## Task 5.2：编写通知模块

**文件：** `src/notification/`（新建子目录）

```
src/notification/
├── __init__.py
├── base.py           # 通知接口抽象
├── feishu.py         # 飞书 Webhook
├── telegram.py       # Telegram Bot
└── console.py        # 控制台输出（开发用 fallback）
```

### 要做的事

1. **定义通知接口：**

```python
# base.py
from abc import ABC, abstractmethod

class Notifier(ABC):
    @abstractmethod
    async def send(self, title: str, content: str) -> bool:
        """发送通知，返回是否成功"""
        pass
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
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.webhook_url, json=payload)
            return resp.status_code == 200
```

3. **Telegram Bot 实现（国际区推荐）：**

```python
# telegram.py
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
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
```

4. **控制台 Fallback（开发期）：**

```python
# console.py
class ConsoleNotifier(Notifier):
    async def send(self, title: str, content: str) -> bool:
        print(f"\n{'='*60}")
        print(f"📢 {title}")
        print(f"{'='*60}")
        print(content)
        return True
```

5. **格式化报告为推送消息：**

```python
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

### 在 .env 中新增配置

```env
# 通知渠道（feishu / telegram / console）
NOTIFICATION_CHANNEL=console

# 飞书配置
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# Telegram 配置
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
```

### 验收标准

- [ ] 飞书 Webhook 能成功发送格式化的 Markdown 消息
- [ ] Telegram Bot 能成功发送消息
- [ ] 未配置通知渠道时自动 fallback 到控制台输出
- [ ] 通知内容包含报告摘要 + 运行成本统计

---

## Task 5.3：新增"通知推送"节点

**文件：** `src/agent/nodes.py`（修改）、`src/agent/graph.py`（修改）

### 要做的事

1. **新增 `send_notification` 节点：**

```python
async def send_notification(state: AgentState) -> dict:
    """推送报告摘要到配置的通知渠道"""
    notifier = get_notifier_from_config()  # 根据环境变量选择通知渠道
    
    report = state.final_report
    cost_summary = state.cost_tracker.get_summary()
    
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
... → format_output → save_to_db → send_notification → END
```

### 在 AgentState 中新增字段

| 字段 | 类型 | 用途 |
|------|------|------|
| `notification_status` | `str` | 通知发送状态 |

### 验收标准

- [ ] 图运行完成后自动发送通知
- [ ] 通知内容可读、信息量足够
- [ ] 通知失败不影响主流程（不抛异常）

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
from src.db.init_db import init_database

load_dotenv()


async def main():
    """主运行函数"""
    # 1. 初始化数据库（幂等操作）
    init_database()
    
    # 2. 构造当天的搜索 Prompt
    today = date.today().isoformat()
    prompt = (
        f"今天是 {today}。请全面搜索和分析 AI 技术对全球就业市场的最新影响。\n"
        f"重点关注：\n"
        f"1. 近一个月内权威机构（WEF、McKinsey、BCG 等）发布的相关报告\n"
        f"2. 招聘平台（LinkedIn、Indeed 等）的最新就业数据变化\n"
        f"3. AI 领域的最新技术动态和创业融资信号\n"
        f"请识别衰退、进化和新兴三类岗位，并给出具体的岗位名称和分析。"
    )
    
    # 3. 运行 Agent
    print(f"🚀 启动 AI 就业趋势分析 Agent... ({today})")
    print(f"{'='*60}")
    
    try:
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"recursion_limit": 50}
        )
        
        # 4. 打印运行结果
        status = result.get("db_save_status", "unknown")
        notification = result.get("notification_status", "unknown")
        print(f"\n{'='*60}")
        print(f"✅ 运行完成")
        print(f"   数据库写入: {status}")
        print(f"   通知推送: {notification}")
        
        # 5. 打印成本摘要
        if "cost_tracker" in result:
            print(result["cost_tracker"].get_summary())
        
    except Exception as e:
        print(f"\n❌ Agent 运行失败: {e}")
        # 尝试发送错误通知
        try:
            from src.notification.base import get_notifier_from_config
            notifier = get_notifier_from_config()
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
- [ ] 控制台输出清晰展示运行状态

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
          name: job-analysis-db-${{ github.run_number }}
          path: data/job_analysis.db
          retention-days: 90
```

#### 方案 C：Google Cloud Run Jobs + Cloud Scheduler（生产级）

- 考虑到你的 `talent-marketplace-agent` 已经使用了 Pulumi + Cloud Run 的部署方式，这个方案最符合你的技术栈。
- 使用 Cloud Run Jobs（而非 Cloud Run Services），因为这是一次性运行任务。
- Cloud Scheduler 触发 Cloud Run Job，和 crontab 等效但更可靠。
- 此方案的实现细节可在后续需要时展开。

### 推荐

**起步用方案 B（GitHub Actions）**，零运维成本。等需求稳定后再迁移到方案 C。

### 验收标准

- [ ] Docker 镜像能成功构建并运行
- [ ] GitHub Actions workflow 能手动触发并成功运行
- [ ] 数据库文件通过 artifact 持久化（GitHub Actions 场景）
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
│   ├── config.py                 # 配置管理
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── state.py              # 全局状态定义
│   │   ├── tools.py              # 工具函数（search_web, read_page 等）
│   │   ├── nodes.py              # 节点函数（call_model, save_to_db 等）
│   │   ├── graph.py              # 主图构建与编译
│   │   ├── prompts.py            # 所有 Prompt 模板
│   │   ├── models.py             # Pydantic 数据模型
│   │   ├── cost_tracker.py       # 成本追踪
│   │   └── research/             # 研究子图
│   │       ├── __init__.py
│   │       ├── state.py
│   │       ├── macro_research.py
│   │       ├── job_market_research.py
│   │       └── tech_frontier_research.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py             # SQLAlchemy ORM 模型
│   │   ├── repository.py         # 数据访问层
│   │   ├── session.py            # 数据库会话管理
│   │   └── init_db.py            # 数据库初始化
│   └── notification/
│       ├── __init__.py
│       ├── base.py               # 通知接口抽象
│       ├── feishu.py             # 飞书通知
│       ├── telegram.py           # Telegram 通知
│       └── console.py            # 控制台通知（开发用）
├── tests/
│   ├── __init__.py
│   └── test_golden_cases.py      # Golden Test Cases
└── milestone-*.md                # 里程碑计划文档
```

---

## Milestone 5 完成标志 ✅

- [ ] 成本追踪贯穿整个 Agent 运行过程，每次运行有详细的消耗报告
- [ ] 至少实现一个通知渠道（飞书/Telegram/控制台）
- [ ] `run_agent.py` 入口脚本完善，支持端到端自动运行
- [ ] Docker 镜像构建成功
- [ ] GitHub Actions 定时任务配置完成，手动触发测试通过
- [ ] 超出 API 预算时 Agent 能优雅停止
- [ ] 运行成本持久化到数据库，可查询历史趋势

---

## 🎉 全部里程碑完成后的系统能力

| 能力 | 描述 |
|------|------|
| 🔍 自主搜索 | 多维度并行搜索权威报告、招聘数据、技术前沿 |
| 🧠 智能收敛 | 有研究计划、有搜索上限、遇到付费墙能自动降级 |
| 📊 结构化输出 | 输出严格的 JSON，可直接入库和展示 |
| 💾 持久化存储 | 所有数据入库，支持历史趋势查询 |
| 🔄 去重机制 | 跨次运行的 URL 去重，不浪费 API 调用 |
| 📱 自动推送 | 报告摘要自动推送到手机 |
| ⏰ 定时运行 | 全自动，零人工干预 |
| 💰 成本可控 | 每次运行有预算上限，费用可追踪 |
