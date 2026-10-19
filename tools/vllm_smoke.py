"""Phase 0 smoke test: one invoice -> vLLM -> schema-constrained JSON.

    python -m tools.vllm_smoke                 # renders the WORKED-EXAMPLE 1 invoice, asks vLLM
    python -m tools.vllm_smoke --pdf some.pdf  # any PDF
    python -m tools.vllm_smoke --backend stub  # pipeline wiring check without a GPU

Prints the extracted JSON, the source-check result and the timing. Exit code 0
when the answer parses against the SourceInvoice schema.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from aap.config import Settings
from aap.extraction.extract import PageInput, extract
from aap.extraction.source_check import source_check
from aap.ingest.parse import parse_document
from aap.ingest.route import route_page
from aap.llm.client import VllmClient
from aap.schemas.source import SourceInvoice


def sample_pdf() -> bytes:
    from tools.invoicegen.layouts import EN
    from tools.invoicegen.model import InvoiceDoc, Line, Vendor
    from tools.invoicegen.render import html_to_pdf, render_html

    doc = InvoiceDoc(
        key="smoke", layout="classic", labels=dict(EN),
        vendor=Vendor("NORTHWIND IT SUPPLIES INC.", ["1200 Commerce St", "Dallas, TX 75201"],
                      email="billing@northwind.example", tax_id="84-1234567"),
        bill_to=["ACSE Corp", "Pune"], invoice_number="NW-INV-20457", invoice_date="12-Sep-2026",
        po_number="4500123", payment_terms="Net 30 days from date of invoice", currency_symbol="$",
        lines=[Line('Dell Latitude 5440 14" i7/16GB/512GB', "$11,500.00", "10", None, "$1,150.00"),
               Line("Dell Dock WD19S USB-C 130W", "$1,800.00", "10", None, "$180.00"),
               Line("On-site imaging & setup", "$500.00", "1", None, "$500.00")],
        subtotal="$13,800.00", tax_label="Sales tax 8%", tax_amount="$1,104.00", total="$14,904.00")
    return html_to_pdf(render_html(doc))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.vllm_smoke")
    p.add_argument("--pdf", type=Path)
    p.add_argument("--backend", choices=["vllm", "stub"], default="vllm")
    p.add_argument("--base-url", default=None)
    a = p.parse_args(argv)

    settings = Settings()
    if a.base_url:
        settings.vllm_base_url = a.base_url
    data = a.pdf.read_bytes() if a.pdf else sample_pdf()
    pages = []
    for pp in parse_document("pdf", data):
        route, score, _ = route_page(pp.kind, pp.text, pp.words, pp.height, settings.text_quality_threshold)
        pages.append(PageInput(page_no=pp.page_no, kind=pp.kind, route=route, text=pp.text, words=pp.words,
                               image_png=pp.image_png, width=pp.width, height=pp.height,
                               image_width=pp.image_width, image_height=pp.image_height))
        print(f"page {pp.page_no}: route={route} text_quality={score}")
    if a.backend == "vllm":
        llm = VllmClient(settings)
        if not llm.health():
            print(f"vLLM not reachable at {settings.vllm_base_url}", file=sys.stderr)
            return 2
    else:
        from aap.llm.stub import StubClient

        llm = StubClient(settings)
    t0 = time.monotonic()
    out = extract(pages, llm)
    src = SourceInvoice.from_extracted(out.extracted)
    stats = source_check(src, pages, out.evidence)
    print(json.dumps(out.extracted.model_dump(), indent=2, ensure_ascii=False))
    print(f"model={out.model} backend={out.backend} requests={out.requests} "
          f"llm_ms={out.duration_ms} total_ms={int((time.monotonic() - t0) * 1000)}")
    print(f"source check: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
