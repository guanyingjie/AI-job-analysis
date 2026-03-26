#!/usr/bin/env python
"""CLI entry-point for the Koshien school agent.

Usage:
    uv run python run_koshien.py --school "大阪桐蔭"
    uv run python run_koshien.py --school "花巻東"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from src.koshien.graph import build_graph
from src.koshien.state import KoshienState


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("koshien")


async def main(school: str) -> None:
    graph = build_graph()
    initial_state = KoshienState(school_input=school)

    logger.info("Starting pipeline for: %s", school)
    result = await graph.ainvoke(initial_state)

    output_path = result.get("output_path") if isinstance(result, dict) else getattr(result, "output_path", "")
    errors = result.get("errors") if isinstance(result, dict) else getattr(result, "errors", [])

    if errors:
        logger.warning("Completed with %d warning(s):", len(errors))
        for err in errors:
            logger.warning("  • %s", err)

    if output_path:
        logger.info("Document saved to: %s", output_path)
    else:
        logger.error("No document was generated.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a Koshien school profile document.")
    parser.add_argument("--school", required=True, help='School name, e.g. "大阪桐蔭"')
    args = parser.parse_args()
    asyncio.run(main(args.school))
