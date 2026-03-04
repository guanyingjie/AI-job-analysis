# src/agent/research/defaults.py
# Fallback search queries when M2 dynamic planning produces no steps for a dimension.
# Functions generate queries dynamically with the current year/month.
# Focused on QUANTITATIVE data: job posting counts, salary data, hiring statistics.

from datetime import date


def _current_year() -> str:
    return str(date.today().year)


def _current_year_month() -> str:
    return date.today().strftime("%Y-%m")


def get_default_macro_queries() -> list[str]:
    y = _current_year()
    return [
        f"WEF Future of Jobs Report {y} key statistics jobs displaced created numbers",
        f"WEF Future of Jobs {y} filetype:pdf",
        f"McKinsey AI automation {y} percentage jobs affected workforce statistics",
        f"site:mckinsey.com AI workforce impact statistics {y}",
        f"OECD AI employment statistics job displacement numbers {y}",
        f"BCG AI workforce transformation hiring data {y}",
    ]


def get_default_job_market_queries() -> list[str]:
    y = _current_year()
    ym = _current_year_month()
    return [
        f"AI engineer job postings count {y} LinkedIn Indeed statistics",
        f"site:linkedin.com AI jobs hiring trends {y}",
        f"Indeed Hiring Lab AI job postings growth statistics {y}",
        f"software developer job postings trend {y} decline growth numbers",
        f"data entry clerk job postings decline statistics {y}",
        f"prompt engineer AI specialist job postings salary {y} Glassdoor",
        f"AI job market statistics active listings count {ym}",
        f"tech layoffs vs AI hiring data numbers {y}",
    ]


def get_default_tech_queries() -> list[str]:
    y = _current_year()
    ym = _current_year_month()
    return [
        f"AI startup hiring headcount growth {y} statistics",
        f"generative AI enterprise adoption statistics numbers {y}",
        f"AI company funding rounds {ym} hiring plans headcount",
    ]
