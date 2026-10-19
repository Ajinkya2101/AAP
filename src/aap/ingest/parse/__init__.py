"""Stage 3 Parse: every format becomes the same ParsedPage structure (ARCHITECTURE 6.2)."""

from __future__ import annotations

from .common import ParsedPage, ParseError


def parse_document(file_type: str, data: bytes) -> list[ParsedPage]:
    if file_type == "pdf":
        from .pdf import parse_pdf

        return parse_pdf(data)
    if file_type in ("png", "jpg", "tiff"):
        from .image import parse_image

        return parse_image(data)
    if file_type in ("xlsx", "xls", "csv"):
        from .sheet import parse_sheet

        return parse_sheet(data, file_type)
    if file_type in ("txt", "html"):
        from .text import parse_text

        return parse_text(data, file_type)
    raise ParseError(f"no parser for file type {file_type}")


__all__ = ["ParseError", "ParsedPage", "parse_document"]
