# Milestone 4：数据库持久化 + 外部去重

**目标：** 将结构化的 `JobTrendReport` 持久化到关系型数据库，并通过外部数据库（而非 LangGraph Checkpointer）实现跨次运行的 URL 去重，避免 Agent 重复抓取同一份报告。

**前置依赖：** Milestone 3 完成

**预估耗时：** 3-4 小时

---

## 前置准备

### 安装新依赖

```bash
uv add sqlalchemy
```

> M1 已安装的 `pydantic`、`pydantic-settings` 等继续复用。SQLAlchemy 是 M4 唯一新增的依赖。

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

-- 岗位趋势主表
CREATE TABLE job_trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,                    -- 对应 reports.id（记录该趋势最近一次由哪次运行写入）
    job_title TEXT NOT NULL,
    job_title_en TEXT,
    zone TEXT NOT NULL CHECK(zone IN ('red', 'yellow', 'green')),
    trend_description TEXT NOT NULL,
    ai_impact TEXT,
    demand_change TEXT,
    first_seen_date DATE NOT NULL,        -- 首次发现日期
    last_updated_date DATE NOT NULL,      -- 最近更新日期
    is_active BOOLEAN DEFAULT TRUE,       -- 是否仍然有效
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE SET NULL
);

-- ⭐ 为 upsert 查询添加索引
CREATE INDEX idx_job_trends_title ON job_trends(job_title);
CREATE INDEX idx_job_trends_title_en ON job_trends(job_title_en);

-- 岗位所需技能表（一对多）
CREATE TABLE job_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_trend_id INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    is_ai_related BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (job_trend_id) REFERENCES job_trends(id) ON DELETE CASCADE
);

-- 信息来源表（一对多，对应 Pydantic 的 Source 模型）
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
    url TEXT NOT NULL UNIQUE,             -- URL 唯一索引，防止重复插入
    title TEXT,
    content_hash TEXT,                    -- 内容摘要的 hash，用于检测内容变化
    first_processed_date DATE NOT NULL,
    last_processed_date DATE NOT NULL,
    process_count INTEGER DEFAULT 1       -- 被处理次数（用于观察热门来源）
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
```

### 设计说明

- **`job_trends.report_id` 使用 `ON DELETE SET NULL`**：岗位趋势跨报告持久存在（upsert 更新 `report_id`），删除某次报告不应连带删除其关联的岗位趋势，而是将 `report_id` 置空。因此 `report_id` 允许 NULL。
- **`job_title` 字段添加索引**：`find_job_by_title()` 按标题做 upsert 查询需要索引支撑，否则数据量大后会变慢。
- **`job_sources` 对应 M3 的 `Source` 模型**：每条记录绑定一个 URL + 名称，不再使用平行列表。

### 使用 SQLAlchemy ORM 映射

```python
from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Float, ForeignKey, Text, func
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

class ReportRecord(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    report_date = Column(Date, nullable=False)
    executive_summary = Column(Text)
    total_declining_jobs = Column(Integer, default=0)
    total_evolving_jobs = Column(Integer, default=0)
    total_emerging_jobs = Column(Integer, default=0)
    total_search_count = Column(Integer, default=0)
    total_tokens_used = Column(Integer, default=0)
    run_duration_seconds = Column(Float)
    created_at = Column(DateTime, server_default=func.now())
    
    # relationships
    job_trends = relationship("JobTrendRecord", back_populates="report")
    market_insights = relationship("MarketInsightRecord", back_populates="report")

class JobTrendRecord(Base):
    __tablename__ = "job_trends"
    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="SET NULL"), nullable=True)
    job_title = Column(String, nullable=False, index=True)
    job_title_en = Column(String, index=True)
    zone = Column(String, nullable=False)
    trend_description = Column(Text, nullable=False)
    ai_impact = Column(Text)
    demand_change = Column(String)
    first_seen_date = Column(Date, nullable=False)
    last_updated_date = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # relationships
    report = relationship("ReportRecord", back_populates="job_trends")
    skills = relationship("JobSkillRecord", back_populates="job_trend", cascade="all, delete-orphan")
    sources = relationship("JobSourceRecord", back_populates="job_trend", cascade="all, delete-orphan")

class JobSkillRecord(Base):
    __tablename__ = "job_skills"
    id = Column(Integer, primary_key=True)
    job_trend_id = Column(Integer, ForeignKey("job_trends.id", ondelete="CASCADE"), nullable=False)
    skill_name = Column(String, nullable=False)
    is_ai_related = Column(Boolean, default=False)
    
    job_trend = relationship("JobTrendRecord", back_populates="skills")

class JobSourceRecord(Base):
    __tablename__ = "job_sources"
    id = Column(Integer, primary_key=True)
    job_trend_id = Column(Integer, ForeignKey("job_trends.id", ondelete="CASCADE"), nullable=False)
    source_url = Column(String, nullable=False)
    source_name = Column(String)
    crawled_date = Column(Date, nullable=False)
    
    job_trend = relationship("JobTrendRecord", back_populates="sources")

class ProcessedSource(Base):
    __tablename__ = "processed_sources"
    id = Column(Integer, primary_key=True)
    url = Column(String, unique=True, nullable=False, index=True)
    title = Column(String)
    content_hash = Column(String)
    first_processed_date = Column(Date, nullable=False)
    last_processed_date = Column(Date, nullable=False)
    process_count = Column(Integer, default=1)

class MarketInsightRecord(Base):
    __tablename__ = "market_insights"
    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String, nullable=False)
    insight = Column(Text, nullable=False)
    data_point = Column(String)
    date_observed = Column(String)
    
    report = relationship("ReportRecord", back_populates="market_insights")
```

### 验收标准

- [ ] 所有表能正确创建（运行 migration 或 `create_all`）
- [ ] `processed_sources` 表的 `url` 字段有唯一索引
- [ ] `job_trends` 表的 `job_title` 和 `job_title_en` 字段有索引
- [ ] `job_trends.report_id` 使用 `ON DELETE SET NULL`，删除报告不连带删除岗位趋势
- [ ] ORM 模型与 M3 的 Pydantic 模型（`Source`、`JobTrend` 等）之间的映射清晰

---

## Task 4.2：数据库会话管理

**文件：** `src/db/session.py`（新建）

### 要做的事

统一管理数据库连接和会话生命周期，避免在工具/节点函数中反复创建 session：

```python
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from src.config import get_settings

# 模块级别的 engine 和 session factory（延迟初始化）
_engine = None
_SessionFactory = None


def get_engine():
    """获取数据库 engine（单例）"""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, echo=False)
    return _engine


def get_session_factory() -> sessionmaker:
    """获取 session factory（单例）"""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory


@contextmanager
def get_db_session() -> Session:
    """
    获取数据库 session 的上下文管理器。
    with 块正常退出时自动 commit，异常时自动 rollback，无需手动调用。
    
    用法：
        with get_db_session() as session:
            session.add(record)
            # 无需显式 commit —— 退出 with 块时自动 commit
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

### 设计说明

- 使用 context manager 模式确保 session 正确关闭和异常回滚。
- engine 和 session factory 是模块级单例，避免重复创建连接。
- 通过 `get_settings().database_url` 读取数据库连接字符串，支持 SQLite / PostgreSQL 切换。

---

## Task 4.3：实现工具层去重

**文件：** `src/agent/tools.py`（修改）

### 要做的事

在 `search_web` 和 `read_page` 工具中注入去重逻辑：

> **⭐ M4 完整 import 列表（M1 原有 + M4 新增合并）：** 以下是 M4 阶段 `tools.py` 顶部的完整 import，确保开发者不遗漏 M1 的基础依赖。

```python
# ---- src/agent/tools.py M4 完整 import 列表 ----
import json
import hashlib                             # ⭐ M4 新增：content_hash 计算
import asyncio                             # ⭐ M4 新增：asyncio.to_thread() 包装同步 Tavily 调用
import httpx                               # M1 原有
from langchain_core.tools import tool      # M1 原有
from src.config import get_settings        # M1 原有
from src.db.repository import SourceRepository  # ⭐ M4 新增
from tavily import TavilyClient            # M1 原有

settings = get_settings()
tavily_client = TavilyClient(api_key=settings.tavily_api_key)

# Jina Reader API 前缀（M1 原有，继续复用）
JINA_READER_PREFIX = "https://r.jina.ai/"
JINA_HEADERS = {"Authorization": f"Bearer {settings.jina_api_key}"} if settings.jina_api_key else {}
```

```python
@tool
async def search_web(query: str) -> str:
    """搜索网页获取最新信息。已处理过的 URL 会被自动过滤。返回 JSON 格式的搜索结果。"""
    try:
        repo = SourceRepository()
        
        # Tavily SDK 为同步调用，放到线程池避免阻塞事件循环
        raw_results = await asyncio.to_thread(
            tavily_client.search, query=query, max_results=8
        )  # 多搜一些，因为会过滤
        
        # 过滤已处理的 URL（批量查询，避免每条结果都开关一次 session）
        normalized_pairs = [
            (result, repo.normalize_url(result["url"]))
            for result in raw_results.get("results", [])
        ]
        processed_map = repo.are_urls_processed([n for _, n in normalized_pairs])
        filtered_results = [
            result for result, normalized in normalized_pairs
            if not processed_map.get(normalized, False)
        ]
        
        if not filtered_results:
            return json.dumps({
                "query": query,
                "results": [],
                "result_count": 0,
                "error": None,
                "note": "所有搜索结果都已在之前的运行中处理过。请尝试不同的搜索关键词。"
            }, ensure_ascii=False)
        
        # 格式化返回（最多 5 条）
        output = {
            "query": query,
            "results": [
                {"title": r["title"], "url": r["url"], "snippet": r.get("content", ""), "score": r.get("score")}
                for r in filtered_results[:5]
            ],
            "result_count": len(filtered_results[:5]),
            "error": None,
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"query": query, "results": [], "result_count": 0, "error": str(e)}, ensure_ascii=False)


async def _fetch_page_content(url: str) -> dict:
    """内部 helper：获取页面内容（Jina 优先 → httpx+BS4 降级）。
    从 M1 read_page 中提取的核心逻辑，返回 dict 而非 JSON 字符串，
    供外层 read_page（含去重检查）和其他需要页面内容的场景复用。"""
    # ── 方案 1：Jina Reader API（处理 JS 渲染，输出干净 Markdown）──
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            jina_url = f"{JINA_READER_PREFIX}{url}"
            resp = await client.get(jina_url, headers=JINA_HEADERS)
            if resp.status_code == 200 and resp.text.strip():
                content = resp.text[:8000]
                return {
                    "url": url, "status": "ok", "content": content,
                    "error": None, "truncated": len(resp.text) > 8000,
                }
    except httpx.TimeoutException:
        pass  # Jina 超时，降级到方案 2
    except Exception:
        pass  # Jina 其他错误，降级

    # ── 方案 2：httpx + BeautifulSoup 基础提取 ──
    try:
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)

            if resp.status_code == 403:
                return {"url": url, "status": "forbidden", "content": None,
                        "error": "Access forbidden (403)", "truncated": False}

            if resp.status_code in {401, 402}:
                return {"url": url, "status": "paywalled", "content": None,
                        "error": f"Paywalled ({resp.status_code})", "truncated": False}

            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            content = text[:8000]
            return {"url": url, "status": "ok", "content": content,
                    "error": None, "truncated": len(text) > 8000}

    except httpx.TimeoutException:
        return {"url": url, "status": "timeout", "content": None,
                "error": "Request timed out (10s)", "truncated": False}
    except Exception as e:
        return {"url": url, "status": "error", "content": None,
                "error": str(e), "truncated": False}


@tool
async def read_page(url: str) -> str:
    """阅读网页内容并返回 JSON。如果此 URL 已被处理过，将返回提示信息。"""
    repo = SourceRepository()
    
    # 检查是否已处理
    normalized = repo.normalize_url(url)
    existing = repo.get_processed_source(normalized)
    if existing:
        return json.dumps({
            "url": url,
            "status": "already_processed",
            "content": None,
            "error": f"此 URL 已在 {existing.last_processed_date} 处理过。如需最新信息，请搜索其他来源。",
            "truncated": False,
        }, ensure_ascii=False)
    
    # 调用提取后的 helper 函数获取页面内容
    result = await _fetch_page_content(url)
    
    # 处理完成后记录到去重表
    if result["status"] == "ok":
        content_hash = hashlib.md5(result["content"].encode()).hexdigest()
        repo.mark_url_as_processed(normalized, content_hash=content_hash)
    
    return json.dumps(result, ensure_ascii=False)


@tool
async def search_report_summary(report_name: str) -> str:
    """搜索报告公开摘要。已处理过的 URL 会被自动过滤。返回 JSON 字符串。"""
    try:
        repo = SourceRepository()
        query = f"{report_name} 摘要 解读 key findings"

        # Tavily SDK 为同步调用，放到线程池避免阻塞事件循环
        raw_results = await asyncio.to_thread(
            tavily_client.search, query=query, max_results=8
        )

        normalized_pairs = [
            (result, repo.normalize_url(result["url"]))
            for result in raw_results.get("results", [])
        ]
        processed_map = repo.are_urls_processed([n for _, n in normalized_pairs])
        filtered_results = [
            result for result, normalized in normalized_pairs
            if not processed_map.get(normalized, False)
        ]

        output = {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                    "score": r.get("score"),
                }
                for r in filtered_results[:5]
            ],
            "result_count": len(filtered_results[:5]),
            "error": None,
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"query": report_name, "results": [], "result_count": 0, "error": str(e)},
            ensure_ascii=False,
        )
```

### SourceRepository 接口设计

**文件：** `src/db/repository.py`（新建）

```python
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from src.db.session import get_db_session
from src.db.models import ProcessedSource
from datetime import date


class SourceRepository:
    """已处理 URL 的数据访问层"""

    def normalize_url(self, url: str) -> str:
        """
        规范化 URL：
        - 去掉 utm_* 等追踪参数
        - 去掉锚点（fragment）
        - 统一尾部斜杠
        - 统一为小写 scheme 和 host
        """
        parsed = urlparse(url)
        # 过滤追踪参数
        query_params = parse_qs(parsed.query)
        filtered_params = {k: v for k, v in query_params.items() if not k.startswith("utm_")}
        clean_query = urlencode(filtered_params, doseq=True)
        # 重组 URL（去掉 fragment，统一 scheme/host 小写）
        normalized = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.params,
            clean_query,
            "",  # 去掉 fragment
        ))
        return normalized
    
    def are_urls_processed(self, urls: list[str]) -> dict[str, bool]:
        """批量检查 URL 是否已处理，减少数据库往返次数。"""
        if not urls:
            return {}
        with get_db_session() as session:
            rows = session.query(ProcessedSource.url).filter(ProcessedSource.url.in_(urls)).all()
            processed = {url for (url,) in rows}
        return {url: url in processed for url in urls}

    def is_url_processed(self, url: str) -> bool:
        """单 URL 检查（内部复用批量接口，便于保持一致）"""
        return self.are_urls_processed([url]).get(url, False)
        
    def get_processed_source(self, url: str) -> ProcessedSource | None:
        """获取已处理的 URL 记录。
        
        注意：返回的对象在 session 关闭后变为 detached 状态，
        使用 expunge 确保简单属性（如 last_processed_date）仍可安全访问。
        """
        with get_db_session() as session:
            obj = session.query(ProcessedSource).filter_by(url=url).first()
            if obj:
                session.expunge(obj)  # 断开与 session 的绑定，避免 DetachedInstanceError
            return obj
        
    def mark_url_as_processed(self, url: str, title: str = None, content_hash: str = None) -> None:
        """标记 URL 为已处理（upsert）"""
        with get_db_session() as session:
            existing = session.query(ProcessedSource).filter_by(url=url).first()
            if existing:
                existing.last_processed_date = date.today()
                existing.process_count += 1
                if content_hash:
                    existing.content_hash = content_hash
            else:
                record = ProcessedSource(
                    url=url,
                    title=title,
                    content_hash=content_hash,
                    first_processed_date=date.today(),
                    last_processed_date=date.today(),
                    process_count=1,
                )
                session.add(record)
        
    def get_processed_count(self) -> int:
        """获取已处理的 URL 总数"""
        with get_db_session() as session:
            return session.query(ProcessedSource).count()
```

> 上述 `search_report_summary` 示例与 `search_web` 使用同一去重与异步包装策略，确保 M3 的付费墙降级路径在 M4 中也具备跨次去重能力。

### 设计说明

- 去重发生在**工具层**而非图层，对 Agent 逻辑透明。
- 使用 `get_db_session()` context manager 管理 session 生命周期；`search_web` 采用批量 URL 去重查询，避免逐条 URL 开关 session。
- `normalize_url()` 提供了完整的 URL 规范化实现：去 utm 参数、去锚点、统一大小写。
- `search_web` 采用 `asyncio.to_thread()` 包装同步 Tavily 调用，避免阻塞异步图执行。
- 使用 `content_hash` 可以在未来检测同一 URL 的内容是否更新了（如果 hash 变了，可以重新处理）。
- `search_web` 多搜 8 条再过滤到 5 条，确保过滤后仍有足够结果。

### 验收标准

- [ ] 第一次运行：Agent 正常搜索并记录所有处理过的 URL
- [ ] 第二次运行：相同搜索查询返回的已处理 URL 被自动过滤
- [ ] `read_page` 对已处理 URL 返回 `status: "already_processed"` JSON 而非重复抓取
- [ ] 去重表中的记录数随运行次数递增
- [ ] URL 规范化正确处理 utm 参数、锚点、尾部斜杠差异
- [ ] `search_web` 去重采用批量查询，单次搜索不会按结果条数线性增加数据库会话开销

---

## Task 4.4：增加"数据库写入"节点

**文件：** `src/agent/nodes.py`（修改）

### 要做的事

新增 `save_to_db` 节点，将 `format_output_with_retry` 生成的 `JobTrendReport` 拆解后写入数据库：

```python
from datetime import date
from src.db.session import get_db_session
from src.db.repository import ReportRepository

async def save_to_db(state: AgentState) -> dict:
    """将结构化报告持久化到数据库。
    
    整个写入在单事务中执行，任何错误都会自动回滚（由 get_db_session 保证）。
    ⭐ 数据库错误不应导致整个 Agent 崩溃，而是返回错误状态，让下游节点（如通知推送）继续执行。
    """
    report = state.final_report
    if report is None:
        return {"db_save_status": "skipped - no report"}
    
    try:
        # 单事务写入：整个 save_to_db 使用同一个 session，避免部分写入
        with get_db_session() as session:
            repo = ReportRepository(session=session)
        
            # 1. 创建报告元数据记录
            report_record = repo.create_report(
                report_date=date.fromisoformat(report.report_date),  # str → date 对象，兼容 PostgreSQL
                executive_summary=report.executive_summary,
                total_declining_jobs=len(report.declining_jobs),
                total_evolving_jobs=len(report.evolving_jobs),
                total_emerging_jobs=len(report.emerging_jobs),
            )
        
            # 2. 写入所有岗位趋势（使用 M3 的 Source 模型）
            all_jobs = [
                *[(job, "red") for job in report.declining_jobs],
                *[(job, "yellow") for job in report.evolving_jobs],
                *[(job, "green") for job in report.emerging_jobs],
            ]
        
            for job_trend, zone in all_jobs:
                # 去重逻辑：如果同名岗位已存在，更新而非插入
                existing = repo.find_job_by_title(job_trend.job_title)
                if existing:
                    repo.update_job_trend(existing.id, job_trend, zone, report_record.id)
                else:
                    repo.create_job_trend(report_record.id, job_trend, zone)
        
            # 3. 写入市场洞察
            for insight in report.market_insights:
                repo.create_market_insight(report_record.id, insight)
            
            # ⭐ 在 session 活跃时捕获 id，避免 with 块外访问 detached 对象
            saved_report_id = report_record.id
        
        return {"db_save_status": "success", "report_id": saved_report_id}

    except Exception as e:
        # ⭐ 数据库错误不阻塞主流程：get_db_session 已自动 rollback，
        # 返回错误状态让下游 send_notification 节点仍能执行
        return {"db_save_status": f"error: {str(e)}"}
```

### ReportRepository 接口设计

**文件：** `src/db/repository.py`（扩展）

```python
from src.db.session import get_db_session
from src.db.models import ReportRecord, JobTrendRecord, JobSkillRecord, JobSourceRecord, MarketInsightRecord
from datetime import date


class ReportRepository:
    """报告数据的数据访问层"""
    def __init__(self, session):
        self.session = session
    
    def create_report(self, **kwargs) -> ReportRecord:
        """创建报告元数据记录"""
        record = ReportRecord(**kwargs)
        self.session.add(record)
        self.session.flush()  # 获取自增 ID
        return record
    
    def find_job_by_title(self, job_title: str) -> JobTrendRecord | None:
        """按岗位中文名称查找（用于去重/更新）"""
        return self.session.query(JobTrendRecord).filter_by(
            job_title=job_title, is_active=True
        ).first()
    
    def create_job_trend(self, report_id: int, job_trend, zone: str) -> JobTrendRecord:
        """创建新的岗位趋势记录（含 skills 和 sources）"""
        record = JobTrendRecord(
            report_id=report_id,
            job_title=job_trend.job_title,
            job_title_en=job_trend.job_title_en,
            zone=zone,
            trend_description=job_trend.trend_description,
            ai_impact=job_trend.ai_impact,
            demand_change=job_trend.demand_change,
            first_seen_date=date.today(),
            last_updated_date=date.today(),
        )
        # 写入技能
        for skill in job_trend.required_skills:
            record.skills.append(JobSkillRecord(
                skill_name=skill.skill_name,
                is_ai_related=skill.is_ai_related,
            ))
        # 写入来源（使用 Source 模型的 url + name）
        for source in job_trend.sources:
            record.sources.append(JobSourceRecord(
                source_url=source.url,
                source_name=source.name,
                crawled_date=date.today(),
            ))
        self.session.add(record)
        self.session.flush()
        return record
    
    def update_job_trend(self, job_id: int, job_trend, zone: str, report_id: int) -> None:
        """更新已有的岗位趋势记录（含 skills/sources 同步）"""
        record = self.session.get(JobTrendRecord, job_id)
        if record:
            record.report_id = report_id
            record.zone = zone
            record.trend_description = job_trend.trend_description
            record.ai_impact = job_trend.ai_impact
            record.demand_change = job_trend.demand_change
            record.last_updated_date = date.today()
            # 策略：replace（清空旧技能/来源后按最新结果重写）
            record.skills.clear()
            for skill in job_trend.required_skills:
                record.skills.append(JobSkillRecord(
                    skill_name=skill.skill_name,
                    is_ai_related=skill.is_ai_related,
                ))
            record.sources.clear()
            for source in job_trend.sources:
                record.sources.append(JobSourceRecord(
                    source_url=source.url,
                    source_name=source.name,
                    crawled_date=date.today(),
                ))
    
    def create_market_insight(self, report_id: int, insight) -> None:
        """创建市场洞察记录"""
        record = MarketInsightRecord(
            report_id=report_id,
            platform=insight.platform,
            insight=insight.insight,
            data_point=insight.data_point,
            date_observed=insight.date_observed,
        )
        self.session.add(record)

    def update_report_cost(self, report_id: int, total_tokens: int, total_search_count: int, run_duration: float) -> None:
        """回填报告成本与运行统计（供 M5 调用）"""
        record = self.session.get(ReportRecord, report_id)
        if record:
            record.total_tokens_used = total_tokens
            record.total_search_count = total_search_count
            record.run_duration_seconds = run_duration
    
    def get_latest_report(self) -> ReportRecord | None:
        """获取最新的报告"""
        return self.session.query(ReportRecord).order_by(ReportRecord.id.desc()).first()
    
    def get_trend_history(self, job_title: str) -> list[JobTrendRecord]:
        """获取某个岗位的历史趋势（用于追踪变化）"""
        return self.session.query(JobTrendRecord).filter_by(job_title=job_title).all()
```

### 验收标准

- [ ] `save_to_db` 节点能正确将 `JobTrendReport` 拆解并写入数据库
- [ ] 岗位的 `sources` 字段正确映射到 `job_sources` 表（使用 Source 模型的 url + name）
- [ ] 同名岗位的第二次写入是更新而非重复插入
- [ ] 同名岗位更新时，`skills/sources` 子表同步刷新，不残留陈旧数据
- [ ] `save_to_db` 全流程在单事务下执行，异常时整体回滚
- [ ] 报告元数据（运行日期、岗位数量统计）被正确记录
- [ ] 可以通过 SQL 查询到写入的数据

---

## Task 4.5：修改图的终点 — 加入数据库写入

**文件：** `src/agent/graph.py`（修改）

### 要做的事

扩展图的末端流程：

```
... → summarize_findings → format_output_with_retry → save_to_db → END
```

```python
builder.add_node("summarize_findings", summarize_findings)
builder.add_node("format_output_with_retry", format_output_with_retry)
builder.add_node("save_to_db", save_to_db)

# 末端线性流程
builder.add_edge("summarize_findings", "format_output_with_retry")
builder.add_edge("format_output_with_retry", "save_to_db")
builder.add_edge("save_to_db", END)
```

### 在 AgentState 中新增字段

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `db_save_status` | `str` | `""` | 数据库写入状态：`success` / `skipped` / `error` |
| `report_id` | `int \| None` | `None` | 数据库中的报告 ID |

### 验收标准

- [ ] 端到端运行后，数据库中有完整的报告数据
- [ ] 图运行的最终状态包含 `db_save_status: "success"`

---

## Task 4.6：数据库初始化与迁移脚本

**文件：** `src/db/init_db.py`（新建）

### 要做的事

1. **开发期：** 使用 SQLite，数据库文件存放在 `data/job_analysis.db`
2. **提供初始化脚本：**

```python
from sqlalchemy import create_engine
from src.db.models import Base
from src.config import get_settings


def init_database(db_url: str | None = None):
    """初始化数据库，创建所有表（幂等操作）"""
    if db_url is None:
        db_url = get_settings().database_url
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    print(f"Database initialized at {db_url}")
    return engine


if __name__ == "__main__":
    init_database()
```

3. **预留 PostgreSQL 切换**：通过 `config.py` 的 `database_url` 控制数据库连接：

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
│   │   ├── repository.py     # 数据访问层（SourceRepository + ReportRepository）
│   │   ├── init_db.py        # 数据库初始化脚本
│   │   └── session.py        # 数据库会话管理（get_db_session context manager）
│   └── agent/
│       └── ...
```

### 验收标准

- [ ] 运行 `init_db.py` 后所有表正确创建
- [ ] SQLite 文件生成在 `data/` 目录下
- [ ] 通过 `config.py` 的 `database_url` 可以切换到 PostgreSQL（不需要改代码）

---

## Milestone 4 完成标志 ✅

- [ ] 数据库 Schema 设计完成，所有表正确创建（含 `job_title` 索引）
- [ ] `session.py` 提供统一的会话管理（context manager 模式）
- [ ] URL 去重在**工具层**实现，对 Agent 逻辑透明（含 URL 规范化）
- [ ] `save_to_db` 节点正确将报告数据写入数据库（`Source` 模型正确映射）
- [ ] 同名岗位的更新逻辑正确（upsert），`ON DELETE SET NULL` 保护岗位趋势数据
- [ ] 跨次运行的去重验证通过（第二次运行跳过已处理 URL）
- [ ] 数据库连接支持环境变量配置（SQLite / PostgreSQL 可切换，通过 `config.py`）
