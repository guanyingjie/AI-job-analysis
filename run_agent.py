# Entry script (M3: linear pipeline + structured JSON output + auto-save)
import asyncio
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# ── Configure logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(message)s",
    datefmt="%H:%M:%S",
)
# Set third-party library logs to WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("langchain").setLevel(logging.WARNING)
logging.getLogger("langsmith").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)

from src.agent.graph import graph

logger = logging.getLogger("agent")


async def main():
    print("=" * 60)
    print("🚀 AI Job Market Research Agent (M3: Plan → Subgraph Search → Structured Output)")
    print("=" * 60)

    try:
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content":
                "Conduct a comprehensive, DATA-DRIVEN analysis of AI's impact on the global job market. "
                "For each job category (declining, evolving, emerging), I need SPECIFIC NUMBERS: "
                "how many active job postings exist on LinkedIn/Indeed, what is the YoY growth or decline rate, "
                "what is the salary range, and which companies are hiring or laying off. "
                "Use data from WEF, McKinsey, LinkedIn, Indeed, Glassdoor, and other authoritative sources. "
                "Do NOT give vague descriptions — every claim must be backed by quantitative data."
            }]},
            config={"recursion_limit": 50}
        )

        report = result.get("final_report")
        if report is None:
            print("\n❌ No structured report generated. Check logs.")
            return

        # Print structured JSON report
        report_json = report.model_dump_json(indent=2, ensure_ascii=False)

        print("\n" + "=" * 60)
        print("📋 Structured AI Job Trend Report")
        print("=" * 60)
        print(report_json)

        # Print bilingual summary
        print("\n" + "-" * 60)
        print(f"📅 Report Date / 报告日期: {report.report_date}")
        print(f"\n📝 Executive Summary (EN):\n{report.executive_summary}")
        print(f"\n📝 执行摘要 (ZH):\n{report.executive_summary_zh}")
        print(f"\n🔴 Declining Jobs / 红区衰退岗位: {len(report.declining_jobs)}")
        for j in report.declining_jobs:
            print(f"   - {j.job_title_en} / {j.job_title_zh}")
        print(f"🟡 Evolving Jobs / 黄区演变岗位: {len(report.evolving_jobs)}")
        for j in report.evolving_jobs:
            print(f"   - {j.job_title_en} / {j.job_title_zh}")
        print(f"🟢 Emerging Jobs / 绿区新兴岗位: {len(report.emerging_jobs)}")
        for j in report.emerging_jobs:
            print(f"   - {j.job_title_en} / {j.job_title_zh}")
        print(f"📊 Market Insights / 市场洞察: {len(report.market_insights)}")
        print(f"📚 Reports Referenced / 引用报告: {len(report.key_reports_referenced)}")
        print("-" * 60)

        # 💾 Save report to project root
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"report_{timestamp}.json"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report_json)
        abs_path = os.path.abspath(filename)
        print(f"\n💾 Report saved to: {abs_path}")

    except KeyboardInterrupt:
        print("\n⏹️  User interrupted")
    except Exception as e:
        logger.error("❌ Unhandled exception: %s", e, exc_info=True)
        print(f"\n❌ Run failed: {e}")
        print("Check API key configuration and network connectivity.")


asyncio.run(main())
