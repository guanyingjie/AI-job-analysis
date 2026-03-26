# Koshien School Agent — 高校野球強校巡礼文档生成

基于 LangGraph 的自动化 Agent，输入一所日本高中校名，自动抓取 Wikipedia / kyureki.com / Yahoo Japan News 三路数据源，经 Gemini LLM 结构化抽取后，生成一篇中文强校巡礼 Markdown 文档（保留日文专有名词）。

## 项目结构

```
.
├── run_koshien.py                  # CLI 入口
├── src/
│   ├── config.py                   # 集中配置（pydantic-settings，读取 .env）
│   └── koshien/
│       ├── graph.py                # LangGraph 线性流水线编排
│       ├── models.py               # 6 大模块 Pydantic schema（SchoolReport）
│       ├── nodes.py                # 5 个流水线节点
│       ├── prompts.py              # 抽取 Prompt + 写作 Prompt（含去 AI 味规则）
│       ├── school_registry.py      # 30 所知名强校别名映射表 + resolve()
│       ├── state.py                # KoshienState 流水线状态定义
│       └── tools/
│           ├── html_cleaner.py     # BeautifulSoup + html2text 清洗
│           ├── kyureki.py          # kyureki.com 甲子園战绩抓取
│           ├── news.py             # Yahoo Japan News 搜索抓取
│           ├── scraper.py          # ScraperAPI 代理封装（重试/超时/预算）
│           └── wikipedia.py        # MediaWiki Action API 封装
├── docs/                           # 生成的文档输出目录
├── pyproject.toml                  # 依赖声明
└── .env                            # API Keys（不提交到 Git）
```

### 流水线流程

```
resolve_school → fetch_all_sources → extract_modules → write_document → save_document
```

| 节点 | 作用 |
|---|---|
| `resolve_school` | 别名解析，将用户输入映射到标准学校信息；未命中时降级 LLM 推断 |
| `fetch_all_sources` | 并行抓取 Wikipedia（免费）+ kyureki.com + Yahoo News |
| `extract_modules` | Gemini（temp=0）结构化抽取 → 6 大模块 JSON |
| `write_document` | Gemini（temp=0.6）以体育专栏作家身份撰写中文 Markdown |
| `save_document` | 落盘到 `docs/{school_name}_{date}.md` |

## 环境要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器

## 安装依赖

```bash
# 克隆仓库后
uv sync
```

## 配置

在项目根目录创建 `.env` 文件：

```env
GOOGLE_API_KEY=your_google_api_key          # 必须 – Gemini LLM
SCRAPER_API_KEY=your_scraper_api_key        # 可选 – 启用 kyureki.com 和 Yahoo News
TAVILY_API_KEY=your_tavily_api_key          # 备用
SERPER_API_KEY=your_serper_api_key          # 备用
JINA_API_KEY=your_jina_api_key              # 备用
```

| Key | 是否必须 | 说明 |
|---|---|---|
| `GOOGLE_API_KEY` | **必须** | Gemini API，用于结构化抽取和文章撰写 |
| `SCRAPER_API_KEY` | 推荐 | ScraperAPI，用于抓取 kyureki.com 和 Yahoo News。不配置则仅使用 Wikipedia |
| `KOSHIEN_MAX_SCRAPER_CALLS` | 可选 | ScraperAPI 单次运行调用上限，默认 `5` |

## 使用

### 基本用法

```bash
uv run python run_koshien.py --school "大阪桐蔭"
```

生成的文档保存在 `docs/大阪桐蔭_20260326.md`。

### 更多示例

```bash
uv run python run_koshien.py --school "花巻東"
uv run python run_koshien.py --school "PL学園"
uv run python run_koshien.py --school "仙台育英"
uv run python run_koshien.py --school "金足農業"
uv run python run_koshien.py --school "明徳義塾"
```

### 支持的学校

内置 30 所知名强校的别名映射（见 `school_registry.py`），支持简称输入：

- "大阪桐蔭"、"桐蔭" → 大阪桐蔭高等学校
- "PL学園"、"PL" → PL学園高等学校
- "金足農"、"カナノウ" → 金足農業高等学校
- "駒大苫小牧"、"駒苫" → 駒澤大学附属苫小牧高等学校
- …等

未在注册表中的学校名会降级为 LLM 自动推断全称。

## 降级与容错

- **Wikipedia 不可用** → 该数据源标记为空，继续流程
- **ScraperAPI 未配置或调用失败** → kyureki / News 标记为空，文末披露缺失来源
- **LLM 抽取失败** → 最多重试 3 次，仍失败则输出空 SchoolReport
- **LLM 撰写失败** → 回退为 raw JSON dump
- **ScraperAPI 预算耗尽** → 跳过剩余请求，已获取数据正常处理
