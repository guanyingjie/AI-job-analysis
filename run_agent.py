# 入口脚本
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

# ── 配置日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
# 第三方库日志设为 WARNING，避免刷屏
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("langchain").setLevel(logging.WARNING)
logging.getLogger("langsmith").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)

from src.agent.graph import graph
from src.agent.nodes import _extract_text


async def main():
    print("=" * 60)
    print("🚀 启动 AI 就业市场研究 Agent")
    print("=" * 60)

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "搜索近一周内关于 AI 替代和新增岗位的中英文报告，给我一个简要总结。"}]},
        config={"recursion_limit": 30}
    )

    # 提取并打印最终回复
    final_content = result["messages"][-1].content
    final_text = _extract_text(final_content)

    print("\n" + "=" * 60)
    print("📋 最终分析结果")
    print("=" * 60)
    print(final_text)


asyncio.run(main())
