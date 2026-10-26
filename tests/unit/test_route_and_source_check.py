from aap.extraction.extract import PageInput
from aap.extraction.source_check import source_check
from aap.ingest.route import IMAGE_ONLY, TEXT_AND_IMAGE, TEXT_ONLY, route_page
from aap.schemas.fields import Flag
from aap.schemas.source import ExtractedInvoice, SourceInvoice


def _words(text: str) -> list[dict]:
    out, y = [], 50.0
    for line in text.splitlines():
        x = 40.0
        for w in line.split():
            out.append({"text": w, "x0": x, "top": y, "x1": x + 6 * len(w), "bottom": y + 10})
            x += 6 * len(w) + 5
        y += 14
    return out


TEXT = """NORTHWIND IT SUPPLIES INC. INVOICE
1200 Commerce St, Dallas TX 75201 Invoice #: NW-INV-20457
Tax ID (EIN): 84-1234567 Date: 12-Sep-2026
Your PO#: 4500123 Terms: Net 30 days from date of invoice
1 Dell Latitude 5440 14" i7/16GB/512GB 10 $1,150.00 $11,500.00
2 Dell Dock WD19S USB-C 130W 10 $180.00 $1,800.00
3 On-site imaging & setup 1 $500.00 $500.00
Subtotal $13,800.00 Sales tax 8% $1,104.00 TOTAL DUE $14,904.00
""" * 3


def test_route_good_text_layer():
    route, score, _ = route_page("pdf", TEXT, _words(TEXT), 792.0, 0.6)
    assert route == TEXT_AND_IMAGE and score >= 0.6


def test_route_scanned_and_cid():
    assert route_page("pdf", "", [], 792.0, 0.6)[0] == IMAGE_ONLY
    garbage = " ".join(["(cid:12)(cid:44)"] * 200)
    assert route_page("pdf", garbage, _words(garbage), 792.0, 0.6)[0] == IMAGE_ONLY
    assert route_page("sheet", "| a |", [], None, 0.6)[0] == TEXT_ONLY
    assert route_page("image", "", [], None, 0.6)[0] == IMAGE_ONLY


def _extracted(**over) -> ExtractedInvoice:
    base = {k: None for k in ExtractedInvoice.model_fields if k not in ("lines", "taxes")}
    base.update(vendor_name="NORTHWIND IT SUPPLIES INC.", invoice_number="NW-INV-20457", invoice_date="12-Sep-2026",
                total="$14,904.00", vendor_tax_id="84-1234567", po_number="4500123")
    base.update(over)
    return ExtractedInvoice(lines=[], taxes=[], **base)


def test_source_check_finds_values_and_flags_inventions():
    page = PageInput(page_no=1, kind="pdf", route=TEXT_AND_IMAGE, text=TEXT, words=_words(TEXT), image_png=None)
    src = SourceInvoice.from_extracted(_extracted(total="$14,940.00"))   # misread total
    source_check(src, [page], [])
    assert src.invoice_number.page == 1 and src.invoice_number.bbox is not None
    assert Flag.not_found_in_source not in src.vendor_name.flags
    assert Flag.not_found_in_source in src.total.flags


def test_source_check_image_only_uses_evidence():
    page = PageInput(page_no=1, kind="pdf", route=IMAGE_ONLY, text="", words=[], image_png=b"x", width=612,
                     height=792, image_width=1700, image_height=2200)
    src = SourceInvoice.from_extracted(_extracted())
    source_check(src, [page], [{"field": "invoice_number", "page": 1, "bbox": [1000, 200, 1300, 240]}])
    assert Flag.unverified_image_only in src.invoice_number.flags
    assert src.invoice_number.bbox == [360.0, 72.0, 468.0, 86.4]


def test_source_check_percent_needs_the_percent_sign():
    text = "Paiement recu le 12 FE 2025\nSales tax 8 % 21.57"
    page = PageInput(page_no=1, kind="pdf", route=TEXT_AND_IMAGE, text=text, words=_words(text), image_png=None)
    src = SourceInvoice.from_extracted(_extracted(tax_rate="12%", tax_label="Sales tax 8 %"))
    source_check(src, [page], [])
    assert Flag.not_found_in_source in src.tax_rate.flags          # only a bare "12" is printed
    assert Flag.not_found_in_source not in src.tax_label.flags
    src = SourceInvoice.from_extracted(_extracted(tax_rate="8%"))
    source_check(src, [page], [])
    assert Flag.not_found_in_source not in src.tax_rate.flags
