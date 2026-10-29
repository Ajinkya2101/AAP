"""Download of the extracted and validated data (pipeline/export.py), without a database."""

import csv
import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

from aap.db.models import Document, ExtractionRun, InvoiceVersion, ReviewTask, ValidationResult
from aap.pipeline.export import (
    HEADER_FIELDS,
    INVOICE_CSV_COLUMNS,
    LINE_FIELDS,
    DocumentData,
    document_zip,
    export_filename,
    invoice_csv,
    label,
    load_invoice,
)
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine, CanonicalTax
from aap.schemas.fields import Field, Method

NOW = datetime(2026, 10, 26, tzinfo=UTC)
MODEL_ANSWER = {"invoice_number": "82094", "total": "$6,825.31", "lines": [], "taxes": []}


def _invoice() -> CanonicalInvoice:
    inv = CanonicalInvoice(country_hint="CA", flags=["multi_request"])
    inv.invoice_num = Field(value="82094", source_value="82094", page=1, method=Method.normalized)
    inv.invoice_date = Field(value=date(2026, 9, 21), source_value="09/21/2026", method=Method.normalized)
    inv.invoice_amount = Field(value=Decimal("-6825.31"), source_value="$6,825.31", flags=["sign_from_document_type"])
    inv.currency_code = Field(value="CAD", flags=["currency_from_vendor_default"], note="symbol '$'")
    inv.vendor_name = Field(value="=HYPERLINK(\"http://x\")", source_value="=HYPERLINK(\"http://x\")")
    inv.taxes = [CanonicalTax(tax_no=1, label=Field(value="TPS"), rate=Field(value=Decimal("5")),
                              amount=Field(value=Decimal("100.00"))),
                 CanonicalTax(tax_no=2, label=Field(value="TVQ 9.975%"), rate=Field(value=Decimal("9.975")),
                              amount=Field(value=Decimal("199.50")))]
    inv.lines = [CanonicalLine(line_number=1, description=Field(value="Énergie", source_value="Énergie"),
                               quantity=Field(value=Decimal("2")), amount=Field(value=Decimal("6438.97")),
                               unit_price=Field(value=Decimal("3219.485"), flags=["not_found_in_source"],
                                                note="price per 1000"),
                               dist_account=Field(value="01-510-6420-0000", method=Method.user)),
                 CanonicalLine(line_number=2, line_type="TAX", printed=False, amount=Field(value=Decimal("386.34")))]
    return inv


def _data(inv: CanonicalInvoice | None = None) -> DocumentData:
    doc = Document(id=uuid.uuid4(), filename="inv.pdf", status="IN_REVIEW", flags=["unknown_sender"],
                   created_at=NOW)
    version = InvoiceVersion(id=uuid.uuid4(), version=3, kind="mapped", created_at=NOW,
                             canonical_invoice=(inv or _invoice()).model_dump(mode="json"))
    results = [ValidationResult(rule_id=rid, rule_name=name, result=res, message=msg, field_paths=[], run_no=1,
                                created_at=NOW)
               for rid, name, res, msg in [("C1", "Required fields", "pass", None),
                                           ("V-REF", "Reference data age", "warning", "23 days"),
                                           ("C5", "Grand total", "fail", "off by 1.00")]]
    runs = [ExtractionRun(id=uuid.uuid4(), backend="vllm", model="qwen2.5-vl-7b", prompt_version="v3", status="error",
                          error="LLM output truncated", raw_output=[{"raw": "{\"vendor_name\": \"Lu"}], created_at=NOW),
            ExtractionRun(id=uuid.uuid4(), backend="vllm", model="qwen2.5-vl-7b", prompt_version="v4", status="ok",
                          raw_output=[{"chunk": 0, "output": MODEL_ANSWER}], source_invoice={}, created_at=NOW),
            ExtractionRun(id=uuid.uuid4(), backend="user", model="clerk", prompt_version="v4", status="ok",
                          raw_output=[{"corrects_run": "x", "by": "me", "changes": {}}], created_at=NOW)]
    task = ReviewTask(reason_codes=["INVOICE_MATH"], summary="amounts do not add up", suggested_action="ask_vendor")
    return DocumentData(doc=doc, version=version, invoice=load_invoice(version), validation=results,
                        validation_history=[(version, r) for r in results], runs=runs, tasks=[task])


def _read(text: str) -> list[dict[str, str]]:
    assert text.startswith("﻿")
    reader = csv.DictReader(io.StringIO(text[1:]))
    rows = list(reader)
    assert reader.fieldnames == INVOICE_CSV_COLUMNS
    return rows


def test_layout_covers_every_field_once():
    # taxes, flags and country_hint have their own columns; lines are the rows
    assert set(CanonicalInvoice.model_fields) - {"taxes", "lines", "flags", "country_hint"} == set(HEADER_FIELDS)
    assert set(CanonicalLine.model_fields) == set(LINE_FIELDS)
    assert len(set(INVOICE_CSV_COLUMNS)) == len(INVOICE_CSV_COLUMNS)


def test_labels_are_readable():
    assert label("invoice_num") == "Invoice number"
    assert label("lines[2].unit_price") == "Line 2 unit price"
    assert label("lines[2].dist_account") == "Line 2 GL account"
    assert "Line unit of measure" in INVOICE_CSV_COLUMNS and "Line PO line" in INVOICE_CSV_COLUMNS


def test_one_row_per_line_with_the_invoice_on_every_row():
    rows = _read(invoice_csv(_data()))
    assert [(r["Line number"], r["Line type"], r["Line printed on the invoice"]) for r in rows] == \
        [("1", "ITEM", "yes"), ("2", "TAX", "no")]
    for r in rows:
        assert (r["Invoice number"], r["Invoice date"], r["Total"], r["Currency code"]) == \
            ("82094", "2026-09-21", "-6825.31", "CAD")
        assert (r["Version"], r["Version kind"], r["Status"]) == ("3", "mapped", "IN_REVIEW")
    assert rows[0]["Line description"] == "Énergie" and rows[0]["Line GL account"] == "01-510-6420-0000"
    assert rows[0]["Line flags"] == "Unit price: not_found_in_source"
    assert rows[0]["Line notes"] == "Unit price: price per 1000"


def test_validation_review_errors_and_flags_columns():
    r = _read(invoice_csv(_data()))[0]
    assert (r["Validation result"], r["Failed checks"], r["Warning checks"]) == ("fail", "C5", "V-REF")
    assert r["Validation messages"] == "C5 Grand total: off by 1.00 | V-REF Reference data age: 23 days"
    assert (r["Open review reasons"], r["Suggested action"]) == ("INVOICE_MATH", "ask_vendor")
    assert r["Errors"] == "extraction run 1: LLM output truncated"
    assert r["Taxes"] == "TPS 5% 100.00; TVQ 9.975% 199.50"     # the rate once, also when the label has it
    assert r["Flags"] == ("document: unknown_sender; invoice: multi_request; "
                          "Currency code: currency_from_vendor_default; Total: sign_from_document_type")
    assert r["Notes"] == "Currency code: symbol '$'"
    assert r["Country (hint)"] == "CA"


def test_csv_neutralizes_formulas_from_the_invoice():
    assert _read(invoice_csv(_data()))[0]["Vendor name"].startswith("'=")


def test_invoice_without_lines_or_without_invoice_is_one_row():
    inv = _invoice()
    inv.lines = []
    rows = _read(invoice_csv(_data(inv)))
    assert len(rows) == 1 and rows[0]["Line number"] == "" and rows[0]["Invoice number"] == "82094"
    doc = Document(id=uuid.uuid4(), filename="broken.pdf", status="FAILED", error="damaged PDF", flags=[],
                   created_at=NOW)
    rows = _read(invoice_csv(DocumentData(doc=doc)))
    assert len(rows) == 1 and (rows[0]["Status"], rows[0]["Errors"], rows[0]["Invoice number"]) == \
        ("FAILED", "damaged PDF", "")


def test_document_zip_has_one_csv_and_raw_model_output():
    zf = zipfile.ZipFile(io.BytesIO(document_zip(_data())))
    names = set(zf.namelist())
    assert {n for n in names if n.endswith(".csv")} == {"invoice.csv"}
    assert {"README.txt", "invoice.json", "validation.json", "extraction_runs.json"} <= names
    # exactly what the model returned; the truncated answer as text; the clerk's correction is not model output
    assert json.loads(zf.read("model_output/run-2-request-1.json")) == MODEL_ANSWER
    assert zf.read("model_output/run-1-request-1-truncated.txt").decode() == "{\"vendor_name\": \"Lu"
    assert not any(n.startswith("model_output/run-3") for n in names)
    runs = json.loads(zf.read("extraction_runs.json"))
    assert [r["backend"] for r in runs] == ["vllm", "vllm", "user"] and runs[2]["raw_output"][0]["by"] == "me"
    assert [v["rule_id"] for v in json.loads(zf.read("validation.json"))] == ["C1", "V-REF", "C5"]


def test_old_version_without_new_fields_still_exports_them():
    stored = _invoice().model_dump(mode="json")
    for key in ("location_ref", "cost_center", "taxes", "bank_change_notice"):
        stored.pop(key)
    inv = load_invoice(InvoiceVersion(canonical_invoice=stored))
    assert inv.cost_center.value is None and inv.taxes == []


def test_printed_terms_and_due_date_columns():
    # only a due date printed: terms derived from it (sent to Oracle) but not "on invoice"
    inv = _invoice()
    inv.due_date = Field(value=date(2026, 11, 20), source_value="11/20/2026", method=Method.normalized)
    inv.terms_name = Field(value="60 NET", source_value="11/20/2026", method=Method.derived, note="due date - ...")
    r = _read(invoice_csv(_data(inv)))[0]
    assert (r["Payment terms"], r["Due date"]) == ("60 NET", "2026-11-20")
    assert (r["Payment terms on invoice"], r["Due date on invoice"]) == ("", "2026-11-20")
    # only terms printed: due date derived from them
    inv.terms_name = Field(value="30 NET", source_value="Net 30", method=Method.normalized)
    inv.due_date = Field(value=date(2026, 10, 21), method=Method.derived, note="invoice date + 30 days")
    r = _read(invoice_csv(_data(inv)))[0]
    assert (r["Payment terms on invoice"], r["Due date on invoice"]) == ("30 NET", "")
    # terms the source check did not find on the page, and supplier defaults, are not "on invoice"
    inv.terms_name = Field(value="30 NET", source_value="Net 30", flags=["not_found_in_source"])
    assert _read(invoice_csv(_data(inv)))[0]["Payment terms on invoice"] == ""
    inv.terms_name = Field(value="45 NET", method=Method.default)
    assert _read(invoice_csv(_data(inv)))[0]["Payment terms on invoice"] == ""


def test_export_filename():
    data = _data()
    assert export_filename(data.doc, data.version, "json") == "82094-v3-mapped.json"
