"""Stage 5 Extract: pages -> LLM -> SourceInvoice (ARCHITECTURE 6.3).

One request per invoice with up to LLM_MAX_IMAGES page images. Longer invoices:
the first chunk gives header + lines, later chunks give lines only; the lines
are joined and the invoice is flagged `multi_request`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from aap.config import get_settings
from aap.llm import LlmClient, LlmRequest
from aap.schemas.fields import Flag
from aap.schemas.source import ExtractedInvoice, ExtractedInvoiceGrounded, ExtractedLine

from .prompts import CURRENT_VERSION, load_prompt


@dataclass
class PageInput:
    page_no: int
    kind: str
    route: str
    text: str
    words: list[dict[str, Any]]
    image_png: bytes | None
    width: float | None = None
    height: float | None = None
    image_width: int | None = None
    image_height: int | None = None


@dataclass
class ExtractionOutput:
    extracted: ExtractedInvoice
    evidence: list[dict[str, Any]]
    raw: list[dict[str, Any]]
    model: str
    backend: str
    requests: int
    duration_ms: int
    flags: list[str] = field(default_factory=list)


def _chunks(pages: list[PageInput], max_images: int) -> list[list[PageInput]]:
    with_images = [p for p in pages if p.image_png is not None]
    if not with_images:
        return [pages]            # sheets / text: one text-only request
    out, cur, count = [], [], 0
    for p in pages:
        if p.image_png is not None and count == max_images:
            out.append(cur)
            cur, count = [], 0
        cur.append(p)
        if p.image_png is not None:
            count += 1
    if cur:
        out.append(cur)
    return out


def _content(pages: list[PageInput]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for p in pages:
        if p.image_png is not None:
            b64 = base64.b64encode(p.image_png).decode("ascii")
            parts.append({"type": "text", "text": f"Page {p.page_no} image:"})
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        if p.route in ("text_and_image", "text_only") and p.text:
            parts.append({"type": "text",
                          "text": f"Text layer of page {p.page_no}:\n{p.text[:get_settings().extract_max_text_chars_per_page]}"})
        elif p.route == "image_only":
            parts.append({"type": "text", "text": f"Page {p.page_no} has no usable text layer; read the image."})
    return parts


def extract(pages: list[PageInput], llm: LlmClient, *, sha256: str | None = None) -> ExtractionOutput:
    settings = get_settings()
    chunks = _chunks(sorted(pages, key=lambda p: p.page_no), settings.llm_max_images_per_request)
    header: ExtractedInvoice | None = None
    lines: list[ExtractedLine] = []
    evidence: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = []
    duration = 0
    model = backend = ""
    for idx, chunk in enumerate(chunks):
        grounded = any(p.route == "image_only" for p in chunk)
        model_cls = ExtractedInvoiceGrounded if grounded else ExtractedInvoice
        system = load_prompt("extract_system")
        if grounded:
            system += "\n\n" + load_prompt("extract_grounded")
        if idx > 0:
            system += "\n\n" + load_prompt("extract_lines_only")
        req = LlmRequest(
            task="extract", system=system, content=_content(chunk), schema=model_cls.model_json_schema(),
            max_tokens=settings.extract_max_tokens,
            context={"sha256": sha256, "page_texts": [p.text for p in chunk],
                     "chunk": {"index": idx, "pages": [p.page_no for p in chunk], "lines_only": idx > 0}
                     if len(chunks) > 1 else None},
        )
        resp = llm.ask(req)
        parsed = model_cls.model_validate(resp.data)
        raw.append({"chunk": idx, "pages": [p.page_no for p in chunk], "model": resp.model,
                    "duration_ms": resp.duration_ms, "output": resp.data, "usage": resp.usage})
        duration += resp.duration_ms
        model, backend = resp.model, resp.backend
        if idx == 0:
            header = parsed
        lines.extend(parsed.lines)
        if isinstance(parsed, ExtractedInvoiceGrounded):
            evidence.extend(e.model_dump() for e in parsed.evidence)
    assert header is not None
    merged = ExtractedInvoice.model_validate({**header.model_dump(exclude={"evidence"}),
                                              "lines": [ln.model_dump() for ln in lines]})
    flags = [Flag.multi_request] if len(chunks) > 1 else []
    return ExtractionOutput(extracted=merged, evidence=evidence, raw=raw, model=model, backend=backend,
                            requests=len(chunks), duration_ms=duration, flags=flags)


PROMPT_VERSION = CURRENT_VERSION
