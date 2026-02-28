# 入口脚本（M3 版本：线性流水线 + 结构化 JSON 输出）
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

# ── 配置日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(message)s",
    datefmt="%H:%M:%S",
)
# 第三方库日志设为 WARNING，避免刷屏
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("langchain").setLevel(logging.WARNING)
logging.getLogger("langsmith").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)

from src.agent.graph import graph

logger = logging.getLogger("agent")


async def main():
    print("=" * 60)
    print("🚀 启动 AI 就业市场研究 Agent（M3：计划 → 子图搜索 → 结构化输出）")
    print("=" * 60)

    try:
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "全面分析 AI 对就业市场的影响，包括衰退、进化和新兴岗位"}]},
            config={"recursion_limit": 50}
        )

        report = result.get("final_report")
        if report is None:
            print("\n❌ 未生成结构化报告，请检查日志。")
            return

        # 打印结构化 JSON 报告
        print("\n" + "=" * 60)
        print("📋 结构化 AI 就业趋势报告")
        print("=" * 60)
        print(report.model_dump_json(indent=2, ensure_ascii=False))

        # 打印摘要信息
        print("\n" + "-" * 60)
        print(f"📅 报告日期: {report.report_date}")
        print(f"📝 执行摘要: {report.executive_summary}")
        print(f"🔴 衰退岗位: {len(report.declining_jobs)} 个")
        print(f"🟡 进化岗位: {len(report.evolving_jobs)} 个")
        print(f"🟢 新兴岗位: {len(report.emerging_jobs)} 个")
        print(f"📊 市场洞察: {len(report.market_insights)} 条")
        print(f"📚 引用报告: {len(report.key_reports_referenced)} 份")
        print("-" * 60)

    except KeyboardInterrupt:
        print("\n⏹️  用户中断运行")
    except Exception as e:
        logger.error("❌ Agent 运行出现未处理异常: %s", e, exc_info=True)
        print(f"\n❌ 运行失败: {e}")
        print("请检查 API Key 配置和网络连接后重试。")


asyncio.run(main())
