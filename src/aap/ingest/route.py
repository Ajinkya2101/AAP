"""Stage 4 Route: text-layer quality score per page (F-02, TECH-STACK 7).

Rules: minimum characters, no (cid:NNN) / replacement glyphs, share of real
words and numbers, and text that covers the page (not only a header).
score >= TEXT_QUALITY_THRESHOLD -> text_and_image, otherwise image_only.
Sheets and text bodies are text_only; images are image_only.
"""

from __future__ import annotations

import re
from typing import Any

TEXT_AND_IMAGE = "text_and_image"
IMAGE_ONLY = "image_only"
TEXT_ONLY = "text_only"

MIN_CHARS = 200
CID = re.compile(r"\(cid:\d+\)")
VALID_TOKEN = re.compile(r"^[\W_]*([A-Za-zÀ-ɏ]{2,}|[\d.,:/%$€£₹\-+#()]+|[A-Za-z]?\d+[A-Za-z\d\-/]*)[\W_]*$")


def text_quality(text: str, words: list[dict[str, Any]], page_height: float | None) -> tuple[float, dict[str, Any]]:
    stripped = re.sub(r"\s+", "", text or "")
    chars = len(stripped)
    cid_hits = len(CID.findall(text or ""))
    bad_glyphs = (text or "").count("�") + cid_hits
    tokens = [w["text"] for w in words] or (text or "").split()
    valid = sum(1 for t in tokens if VALID_TOKEN.match(t))
    valid_ratio = valid / len(tokens) if tokens else 0.0
    if words and page_height:
        tops = sorted(w["top"] for w in words)
        coverage = min(1.0, (tops[-1] - tops[0]) / (page_height * 0.5))
    else:
        coverage = 1.0 if chars >= MIN_CHARS else chars / MIN_CHARS
    chars_ok = min(chars / MIN_CHARS, 1.0)
    glyph_ok = 1.0 - min(bad_glyphs / max(len(tokens), 1) * 5, 1.0)
    score = 0.30 * chars_ok + 0.30 * valid_ratio + 0.20 * coverage + 0.20 * glyph_ok
    if cid_hits > 3:
        score *= 0.5
    details = dict(chars=chars, tokens=len(tokens), valid_ratio=round(valid_ratio, 3), coverage=round(coverage, 3),
                   cid_hits=cid_hits, bad_glyphs=bad_glyphs)
    return round(score, 3), details


def route_page(kind: str, text: str, words: list[dict[str, Any]], page_height: float | None,
               threshold: float) -> tuple[str, float | None, dict[str, Any]]:
    if kind in ("sheet", "text"):
        return TEXT_ONLY, None, {}
    if kind == "image":
        return IMAGE_ONLY, 0.0, {"reason": "image file"}
    score, details = text_quality(text, words, page_height)
    return (TEXT_AND_IMAGE if score >= threshold else IMAGE_ONLY), score, details
