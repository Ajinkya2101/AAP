"""PDF: pdfplumber for text/words/tables, pypdfium2 for 200 DPI page images."""

from __future__ import annotations

import io

import pdfplumber
import pypdfium2 as pdfium

from aap.config import get_settings

from .common import ParsedPage, ParseError, png_bytes


def _markdown(table: list[list[str | None]]) -> str:
    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in table if row]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def parse_pdf(data: bytes) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    try:
        pdf_doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise ParseError(f"damaged PDF: {exc}") from exc
    try:
        with pdfplumber.open(io.BytesIO(data)) as plumber:
            for i, page in enumerate(plumber.pages, start=1):
                try:
                    words = page.extract_words(keep_blank_chars=False, use_text_flow=True,
                                               extra_attrs=["size"])
                    text = page.extract_text(layout=False) or ""
                    tables = [md for t in (page.extract_tables() or []) if (md := _markdown(t))]
                except Exception as exc:  # noqa: BLE001 - a broken text layer is routed to image_only
                    words, text, tables = [], "", []
                    err = str(exc)
                else:
                    err = None
                img = pdf_doc[i - 1].render(scale=get_settings().page_render_dpi / 72).to_pil()
                pages.append(ParsedPage(
                    page_no=i, kind="pdf", text=text,
                    words=[{"text": w["text"], "x0": round(w["x0"], 2), "top": round(w["top"], 2),
                            "x1": round(w["x1"], 2), "bottom": round(w["bottom"], 2)} for w in words],
                    tables=tables, image_png=png_bytes(img), width=float(page.width), height=float(page.height),
                    image_width=img.width, image_height=img.height,
                    meta={"text_layer_error": err} if err else {},
                ))
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"cannot read PDF: {exc}") from exc
    finally:
        pdf_doc.close()
    if not pages:
        raise ParseError("PDF has no pages")
    return pages
