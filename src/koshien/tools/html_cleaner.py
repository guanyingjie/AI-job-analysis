"""HTML → clean Markdown conversion with noise stripping."""

from __future__ import annotations

import re

import html2text
from bs4 import BeautifulSoup

_DEFAULT_MAX_CHARS = 6000

_WIKI_LINK_RE = re.compile(
    r"\[([^\]]+)\]\(/wiki/[^\)]+\)",
)
_EMPTY_LINK_RE = re.compile(r"\[\]\([^\)]+\)")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_GEOHACK_RE = re.compile(r"\[?https?://geohack\.toolforge\.org[^\s\]]*\]?")
_COORD_NOISE_RE = re.compile(r"[北南]緯\d+度.*?/\s*[\d.]+;\s*[\d.]+\)?")


def clean_html(raw_html: str, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Strip ads / nav / scripts from *raw_html* and convert to Markdown."""
    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    for cls_pattern in ("sidebar", "ad", "advertisement", "nav", "footer", "mw-editsection",
                        "mw-jump-link", "mw-cite-backlink", "reference", "reflist", "navbox",
                        "infobox", "metadata", "mbox"):
        for el in soup.find_all(class_=lambda c: c and cls_pattern in str(c).lower()):
            el.decompose()

    for el in soup.find_all("sup", class_="reference"):
        el.decompose()

    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_links = False
    converter.ignore_images = True
    converter.ignore_emphasis = False
    converter.protect_links = False

    md = converter.handle(str(soup))

    md = _WIKI_LINK_RE.sub(r"\1", md)
    md = _EMPTY_LINK_RE.sub("", md)
    md = _GEOHACK_RE.sub("", md)
    md = _COORD_NOISE_RE.sub("", md)
    md = re.sub(r"\(/wiki/[^\)]+\)", "", md)
    md = _MULTI_NEWLINE_RE.sub("\n\n", md)

    md = md.strip()
    if len(md) > max_chars:
        md = md[:max_chars] + "\n\n…[truncated]"
    return md
