"""Sports Blog Agent — State definition"""

from typing import TypedDict


class SportsState(TypedDict):
    tournament: str       # e.g. "WBC", "MLB"
    date: str             # YYYY-MM-DD
    search_results: str   # combined API data + web content
    blog_markdown: str    # final Markdown blog post
    output_path: str      # saved file path
