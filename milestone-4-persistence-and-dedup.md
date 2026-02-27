# Milestone 4：数据库持久化 + 外部去重

**目标：** 将结构化的 `JobTrendReport` 持久化到关系型数据库，并通过外部数据库（而非 LangGraph Checkpointer）实现跨次运行的 URL 去重，避免 Agent 重复抓取同一份报告。

**前置依赖：** Milestone 3 完成

**预估耗时：** 3-4 小时

---

## 架构决策说明

### ❌ 为什么不能用 Checkpointer 做去重？

LangGraph 的 `MemorySaver` / `SqliteSaver` 是按 `thread_id` 隔离的：
- 每次 cron job 运行大概率创建新 `thread_id`
- 上一次运行的 `processed_urls` 对新线程**完全不可见**
- 即使复用同一个 `thread_id`，`messages` 列表会无限膨胀

### ✅ 正确方案：外部数据库 + 工具层去重

- 在 SQLite（开发期）/ PostgreSQL（生产期）中维护一张 `processed_sources` 表
- 在 `search_web` 和 `read_page` 工具内部，查询数据库判断 URL 是否已处理
- Agent 每次运行时自动跳过已处理的 URL

---

## Task 4.1：设计数据库 Schema

**文件：** `src/db/models.py`（新建）

### 数据库表设计

```sql
-- 岗位趋势主表
CREATE TABLE job_trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_title TEXT NOT NULL,
    job_title_en TEXT,
    zone TEXT NOT NULL CHECK(zone IN ('red', 'yellow', 'green')),
    trend_description TEXT NOT NULL,
    ai_impact TEXT,
    demand_change TEXT,
    first_seen_date DATE NOT NULL,      -- 首次发现日期
    last_updated_date DATE NOT NULL,    -- 最近更新日期
    is_active BOOLEAN DEFAULT TRUE,     -- 是否仍然有效
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 岗位所需技能表（一对多）
CREATE TABLE job_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_trend_id INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    is_ai_related BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (job_trend_id) REFERENCES job_trends(id) ON DELETE CASCADE
);

-- 信息来源表（多对多的桥接表）
CREATE TABLE job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_trend_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    source_name TEXT,
    crawled_date DATE NOT NULL,
    FOREIGN KEY (job_trend_id) REFERENCES job_trends(id) ON DELETE CASCADE
);

-- ⭐ 已处理 URL 表（用于跨次运行去重）
CREATE TABLE processed_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,           -- URL 唯一索引，防止重复插入
    title TEXT,
    content_hash TEXT,                  -- 内容摘要的 hash，用于检测内容变化
    first_processed_date DATE NOT NULL,
    last_processed_date DATE NOT NULL,
    process_count INTEGER DEFAULT 1     -- 被处理次数（用于观察热门来源）
);

-- 市场洞察表
CREATE TABLE market_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    insight TEXT NOT NULL,
    data_point TEXT,
    date_observed TEXT,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

-- 报告元数据表（每次运行生成一条记录）
CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date DATE NOT NULL,
    executive_summary TEXT,
    total_declining_jobs INTEGER DEFAULT 0,
    total_evolving_jobs INTEGER DEFAULT 0,
    total_emerging_jobs INTEGER DEFAULT 0,
    total_search_count INTEGER DEFAULT 0,
    total_tokens_used INTEGER DEFAULT 0,  -- 用于成本追踪（M5）
    run_duration_seconds REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 使用 SQLAlchemy ORM 映射

```python
from sqlalchemy import Column, Integer, String, Boolean, Date, Float, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

class JobTrendRecord(Base):
    __tablename__ = "job_trends"
    # ... ORM 映射

class ProcessedSource(Base):
    __tablename__ = "processed_sources"
    id = Column(Integer, primary_key=True)
    url = Column(String, unique=True, nullable=False, index=True)
    title = Column(String)
    content_hash = Column(String)
    first_processed_date = Column(Date, nullable=False)
    last_processed_date = Column(Date, nullable=False)
    process_count = Column(Integer, default=1)
```

### 验收标准

- [ ] 所有表能正确创建（运行 migration 或 `create_all`）
- [ ] `processed_sources` 表的 `url` 字段有唯一索引
- [ ] ORM 模型与 Pydantic 模型之间的映射清晰

---

## Task 4.2：实现工具层去重

**文件：** `src/agent/tools.py`（修改）

### 要做的事

在 `search_web` 和 `read_page` 工具中注入去重逻辑：

```python
from src.db.repository import SourceRepository

@tool
def search_web(query: str) -> str:
    """搜索网页获取信息。已处理过的 URL 会被自动过滤。"""
    repo = SourceRepository()
    
    raw_results = tavily_client.search(query=query, max_results=8)  # 多搜一些，因为会过滤
    
    # 过滤已处理的 URL
    filtered_results = []
    for result in raw_results["results"]:
        if not repo.is_url_processed(result["url"]):
            filtered_results.append(result)
    
    if not filtered_results:
        return "所有搜索结果都已在之前的运行中处理过。请尝试不同的搜索关键词。"
    
    # 格式化返回（最多 5 条）
    output_lines = []
    for r in filtered_results[:5]:
        output_lines.append(f"标题: {r['title']}\nURL: {r['url']}\n摘要: {r['content']}\n")
    
    return "\n---\n".join(output_lines)


@tool
def read_page(url: str) -> str:
    """阅读网页内容。如果此 URL 已被处理过，将返回缓存的摘要。"""
    repo = SourceRepository()
    
    # 检查是否已处理
    existing = repo.get_processed_source(url)
    if existing:
        return f"此 URL 已在 {existing.last_processed_date} 处理过。如需重新阅读，请使用不同的来源。"
    
    # 正常阅读逻辑（同 M1）
    content = _fetch_page_content(url)
    
    # 处理完成后记录到去重表
    content_hash = hashlib.md5(content.encode()).hexdigest()
    repo.mark_url_as_processed(url, content_hash=content_hash)
    
    return content
```

### SourceRepository 接口设计

**文件：** `src/db/repository.py`（新建）

```python
class SourceRepository:
    """已处理 URL 的数据访问层"""
    
    def is_url_processed(self, url: str) -> bool:
        """检查 URL 是否已经被处理过"""
        
    def get_processed_source(self, url: str) -> ProcessedSource | None:
        """获取已处理的 URL 记录"""
        
    def mark_url_as_processed(self, url: str, title: str = None, content_hash: str = None) -> None:
        """标记 URL 为已处理"""
        
    def get_processed_count(self) -> int:
        """获取已处理的 URL 总数"""
```

### 设计说明

- 去重发生在**工具层**而非图层，对 Agent 逻辑透明。
- 使用 `content_hash` 可以在未来检测同一 URL 的内容是否更新了（如果 hash 变了，可以重新处理）。
- `search_web` 多搜 8 条再过滤到 5 条，确保过滤后仍有足够结果。

### 验收标准

- [ ] 第一次运行：Agent 正常搜索并记录所有处理过的 URL
- [ ] 第二次运行：相同搜索查询返回的已处理 URL 被自动过滤
- [ ] `read_page` 对已处理 URL 返回友好提示而非重复抓取
- [ ] 去重表中的记录数随运行次数递增

---

## Task 4.3：增加"数据库写入"节点

**文件：** `src/agent/nodes.py`（修改）

### 要做的事

新增 `save_to_db` 节点，将 `format_output` 生成的 `JobTrendReport` 拆解后写入数据库：

```python
async def save_to_db(state: AgentState) -> dict:
    """将结构化报告持久化到数据库"""
    report = state.final_report
    if report is None:
        return {"db_save_status": "skipped - no report"}
    
    repo = ReportRepository()
    
    # 1. 创建报告元数据记录
    report_record = repo.create_report(
        report_date=report.report_date,
        executive_summary=report.executive_summary,
        total_declining_jobs=len(report.declining_jobs),
        total_evolving_jobs=len(report.evolving_jobs),
        total_emerging_jobs=len(report.emerging_jobs),
    )
    
    # 2. 写入所有岗位趋势
    all_jobs = [
        *[(job, "red") for job in report.declining_jobs],
        *[(job, "yellow") for job in report.evolving_jobs],
        *[(job, "green") for job in report.emerging_jobs],
    ]
    
    for job_trend, zone in all_jobs:
        # 去重逻辑：如果同名岗位已存在，更新而非插入
        existing = repo.find_job_by_title(job_trend.job_title)
        if existing:
            repo.update_job_trend(existing.id, job_trend, zone)
        else:
            repo.create_job_trend(report_record.id, job_trend, zone)
    
    # 3. 写入市场洞察
    for insight in report.market_insights:
        repo.create_market_insight(report_record.id, insight)
    
    return {"db_save_status": "success", "report_id": report_record.id}
```

### ReportRepository 接口设计

**文件：** `src/db/repository.py`（扩展）

```python
class ReportRepository:
    """报告数据的数据访问层"""
    
    def create_report(self, **kwargs) -> ReportRecord:
        """创建报告元数据记录"""
    
    def find_job_by_title(self, job_title: str) -> JobTrendRecord | None:
        """按岗位名称查找（用于去重/更新）"""
    
    def create_job_trend(self, report_id: int, job_trend: JobTrend, zone: str) -> JobTrendRecord:
        """创建新的岗位趋势记录"""
    
    def update_job_trend(self, job_id: int, job_trend: JobTrend, zone: str) -> None:
        """更新已有的岗位趋势记录"""
    
    def create_market_insight(self, report_id: int, insight: MarketInsight) -> None:
        """创建市场洞察记录"""
    
    def get_latest_report(self) -> ReportRecord | None:
        """获取最新的报告"""
    
    def get_trend_history(self, job_title: str) -> list[JobTrendRecord]:
        """获取某个岗位的历史趋势（用于追踪变化）"""
```

### 验收标准

- [ ] `save_to_db` 节点能正确将 `JobTrendReport` 拆解并写入数据库
- [ ] 同名岗位的第二次写入是更新而非重复插入
- [ ] 报告元数据（运行日期、岗位数量统计）被正确记录
- [ ] 可以通过 SQL 查询到写入的数据

---

## Task 4.4：修改图的终点 — 加入数据库写入

**文件：** `src/agent/graph.py`（修改）

### 要做的事

扩展图的末端流程：

```
... → summarize_findings → format_output → save_to_db → END
```

```python
builder.add_node("summarize_findings", summarize_findings)
builder.add_node("format_output", format_output_with_retry)
builder.add_node("save_to_db", save_to_db)

# 末端线性流程
builder.add_edge("summarize_findings", "format_output")
builder.add_edge("format_output", "save_to_db")
builder.add_edge("save_to_db", END)
```

### 在 AgentState 中新增字段

| 字段 | 类型 | 用途 |
|------|------|------|
| `db_save_status` | `str` | 数据库写入状态：`success` / `skipped` / `error` |
| `report_id` | `int \| None` | 数据库中的报告 ID |

### 验收标准

- [ ] 端到端运行后，数据库中有完整的报告数据
- [ ] 图运行的最终状态包含 `db_save_status: "success"`

---

## Task 4.5：数据库初始化与迁移脚本

**文件：** `src/db/init_db.py`（新建）

### 要做的事

1. **开发期：** 使用 SQLite，数据库文件存放在 `data/job_analysis.db`
2. **提供初始化脚本：**

```python
from sqlalchemy import create_engine
from src.db.models import Base

def init_database(db_url: str = "sqlite:///data/job_analysis.db"):
    """初始化数据库，创建所有表"""
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    print(f"Database initialized at {db_url}")
    return engine
```

3. **预留 PostgreSQL 切换**：通过环境变量控制数据库连接：

```python
# .env
DATABASE_URL=sqlite:///data/job_analysis.db
# 生产环境切换为：
# DATABASE_URL=postgresql://user:pass@host:5432/job_analysis
```

### 项目目录更新

```
AI-job-analysis/
├── data/                     # SQLite 数据库文件目录（加入 .gitignore）
│   └── .gitkeep
├── src/
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py         # SQLAlchemy ORM 模型
│   │   ├── repository.py     # 数据访问层
│   │   ├── init_db.py        # 数据库初始化脚本
│   │   └── session.py        # 数据库会话管理
│   └── agent/
│       └── ...
```

### 验收标准

- [ ] 运行 `init_db.py` 后所有表正确创建
- [ ] SQLite 文件生成在 `data/` 目录下
- [ ] 通过环境变量可以切换到 PostgreSQL（不需要改代码）

---

## Milestone 4 完成标志 ✅

- [ ] 数据库 Schema 设计完成，所有表正确创建
- [ ] URL 去重在**工具层**实现，对 Agent 逻辑透明
- [ ] `save_to_db` 节点正确将报告数据写入数据库
- [ ] 同名岗位的更新逻辑正确（upsert）
- [ ] 跨次运行的去重验证通过（第二次运行跳过已处理 URL）
- [ ] 数据库连接支持环境变量配置（SQLite / PostgreSQL 可切换）
