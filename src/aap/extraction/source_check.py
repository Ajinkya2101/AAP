"""Source check (ARCHITECTURE 6.3, C12): find each extracted value in the page text.

- Found in a text layer: page + bbox from the pdfplumber word coordinates.
- Page without a text layer: the location the LLM gave (evidence), flag unverified_image_only.
- Not found although the document has a text layer: flag not_found_in_source.
"""

from __future__ import annotations

import re
from typing import Any

from aap.schemas.fields import Field, Flag
from aap.schemas.source import HEADER_FIELDS, LINE_FIELDS, SourceInvoice

from .extract import PageInput

_NORM = re.compile(r"[^0-9a-zÀ-ɏ]+")


def norm(s: str) -> str:
    return _NORM.sub("", s.lower())


def _find_in_words(target: str, words: list[dict[str, Any]]) -> list[float] | None:
    """Consecutive words whose normalized concatenation equals the target."""
    if not target:
        return None
    normed = [norm(w["text"]) for w in words]
    for i in range(len(words)):
        acc = ""
        for j in range(i, min(i + 40, len(words))):
            if not normed[j]:
                continue
            acc += normed[j]
            if acc == target:
                ws = words[i:j + 1]
                return [min(w["x0"] for w in ws), min(w["top"] for w in ws),
                        max(w["x1"] for w in ws), max(w["bottom"] for w in ws)]
            if not target.startswith(acc):
                break
    # Value is a part of a longer word (e.g. "PO#4500123")
    for w, n in zip(words, normed, strict=True):
        if len(target) >= 4 and target in n:
            return [w["x0"], w["top"], w["x1"], w["bottom"]]
    return None


def locate(field: Field[str], pages: list[PageInput], evidence: dict[str, dict[str, Any]], name: str) -> None:
    if not field.present:
        return
    target = norm(field.source_value or str(field.value))
    if not target:
        return
    has_text_layer = False
    for p in pages:
        if p.route == "text_and_image" and p.words:
            has_text_layer = True
            bbox = _find_in_words(target, p.words)
            if bbox:
                field.page, field.bbox = p.page_no, [round(v, 2) for v in bbox]
                return
        elif p.route == "text_only" and p.text:
            has_text_layer = True
            if target in norm(p.text):
                field.page = p.page_no
                return
    ev = evidence.get(name)
    image_pages = [p for p in pages if p.route == "image_only"]
    if image_pages:
        if ev:
            page = next((p for p in image_pages if p.page_no == ev["page"]), image_pages[0])
            scale_x = (page.width / page.image_width) if page.width and page.image_width else 1.0
            scale_y = (page.height / page.image_height) if page.height and page.image_height else 1.0
            # PDF pages: pixel box -> points; image files: width == image_width, scale 1.
            if page.kind == "pdf":
                field.bbox = [round(ev["bbox"][0] * scale_x, 2), round(ev["bbox"][1] * scale_y, 2),
                              round(ev["bbox"][2] * scale_x, 2), round(ev["bbox"][3] * scale_y, 2)]
            else:
                field.bbox = [float(v) for v in ev["bbox"]]
            field.page = page.page_no
        elif len(image_pages) == 1:
            field.page = image_pages[0].page_no
        if not has_text_layer:
            field.with_flag(Flag.unverified_image_only)
            return
    field.with_flag(Flag.not_found_in_source)


def source_check(src: SourceInvoice, pages: list[PageInput], evidence: list[dict[str, Any]]) -> dict[str, int]:
    ev = {e["field"]: e for e in evidence}
    for name in HEADER_FIELDS:
        locate(getattr(src, name), pages, ev, name)
    for ln in src.lines:
        for name in LINE_FIELDS:
            locate(getattr(ln, name), pages, ev, f"lines[{ln.line_no}].{name}")
    stats = {"checked": 0, "not_found": 0, "unverified": 0}
    for name in HEADER_FIELDS:
        f = getattr(src, name)
        if f.present:
            stats["checked"] += 1
            stats["not_found"] += Flag.not_found_in_source in f.flags
            stats["unverified"] += Flag.unverified_image_only in f.flags
    return stats
