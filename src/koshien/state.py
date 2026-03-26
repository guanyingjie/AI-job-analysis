"""LangGraph state definition for the Koshien agent pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class KoshienState:
    school_input: str = ""
    school_entry: dict | None = None

    # --- data sources ---
    wiki_content: str = ""
    kyureki_content: str = ""
    news_content: str = ""
    hb_nippon_content: str = ""
    draft_content: str = ""
    tavily_content: str = ""

    extracted_data: dict | None = None
    document_markdown: str = ""
    output_path: str = ""
    errors: list[str] = field(default_factory=list)
