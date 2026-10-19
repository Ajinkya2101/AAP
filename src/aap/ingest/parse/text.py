"""Email body / plain text / HTML: text only (scripts removed from HTML)."""

from __future__ import annotations

from charset_normalizer import from_bytes

from aap.ingest.emailparse import html_to_text

from .common import ParsedPage


def parse_text(data: bytes, file_type: str) -> list[ParsedPage]:
    best = from_bytes(data).best()
    text = str(best) if best is not None else data.decode("utf-8", errors="replace")
    if file_type == "html":
        text = html_to_text(text)
    return [ParsedPage(page_no=1, kind="text", text=text.strip())]
