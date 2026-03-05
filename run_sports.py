# Entry script — Sports Blog Agent
# Usage:
#   uv run python run_sports.py                                # WBC, today
#   uv run python run_sports.py --tournament MLB --date 2026-03-10

import argparse
import asyncio
import logging
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# ── Configure logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("langchain").setLevel(logging.WARNING)
logging.getLogger("langsmith").setLevel(logging.WARNING)
logging.getLogger("google").setLevel(logging.WARNING)

from src.sports.graph import graph

logger = logging.getLogger("sports")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sports Blog Agent — generate a daily game recap blog post",
    )
    parser.add_argument(
        "--tournament",
        type=str,
        choices=["WBC", "MLB"],
        default="WBC",
        help="Tournament name (default: WBC)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=date.today().isoformat(),
        help="Game date in YYYY-MM-DD format (default: today)",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    print("=" * 60)
    print(f"  Sports Blog Agent — {args.tournament} {args.date}")
    print("=" * 60)

    try:
        result = await graph.ainvoke({
            "tournament": args.tournament,
            "date": args.date,
            "search_results": "",
            "blog_markdown": "",
            "output_path": "",
        })

        print("\n" + result["blog_markdown"])
        print("\n" + "-" * 60)
        print(f"  Saved to: {result['output_path']}")
        print("-" * 60)

    except KeyboardInterrupt:
        print("\n  User interrupted")
    except Exception as e:
        logger.error("Unhandled exception: %s", e, exc_info=True)
        print(f"\n  Run failed: {e}")
        print("  Check API key configuration and network connectivity.")


asyncio.run(main())
