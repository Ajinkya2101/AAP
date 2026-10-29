"""Download of everything the application read and checked for an invoice: one CSV per invoice in
a fixed column layout, and the data as stored (JSON).

One document (`GET /api/documents/{id}/export.zip`):

    README.txt
    invoice.csv           the invoice in the standard layout (INVOICE_CSV_COLUMNS)
    invoice.json          the newest invoice version, every field with its provenance
    validation.json       every validation run of every version
    extraction_runs.json  every extraction run: model, prompt, status, raw output, printed values
    model_output/         exactly what the model returned, one file per request

Several documents (`GET /api/export.zip`): the same per document under `documents/`, plus
invoices.csv: every invoice's rows under one column header.

The standard layout: one row per invoice line; the document, header, validation and review columns
repeat on every row of the invoice. An invoice without lines (or a document that never got one) is
one row with the line columns empty. The columns are the same for every invoice, so files can be
appended. Dates YYYY-MM-DD, amounts plain decimals with a point and no thousands separator, yes / no,
lists separated by "; ", validation messages by " | ". PRINTED_COLUMNS repeat payment terms and due
date as printed: terms only when terms are printed, the due date only when a due date is printed.
"""

from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import uuid
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import IO, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.db.models import Document, ExtractionRun, InvoiceVersion, ReviewTask, ValidationResult
from aap.schemas.canonical import CanonicalInvoice
from aap.schemas.fields import Field, Flag, Method

# The versions validation runs on (as the invoice page): an approved version is not re-validated.
REVIEWED_KINDS = ("mapped", "user", "vendor_reply")
LABELS = {"invoice_num": "Invoice number", "org_id": "Operating unit ID", "dist_account": "GL account",
          "country_hint": "Country (hint)", "source": "Invoice source", "category_code": "Category",
          "category_id": "Category ID", "po_line_number": "PO line", "uom": "Unit of measure",
          "printed": "Printed on the invoice", "line_type": "Type", "terms_name": "Payment terms",
          "terms_id": "Payment terms ID", "invoice_amount": "Total", "location_ref": "Location",
          "po_release_num": "PO release"}
WORDS = {"id": "ID", "po": "PO", "gl": "GL", "iban": "IBAN", "uom": "UOM", "num": "number"}

# The standard layout. Every Field of CanonicalInvoice / CanonicalLine is listed (a unit test checks it).
DOCUMENT_COLUMNS = ["Document ID", "File", "Status", "Received", "Version", "Version kind"]
HEADER_FIELDS = ["invoice_num", "invoice_type", "invoice_date", "due_date", "gl_date", "vendor_name", "vendor_id",
                 "vendor_number", "vendor_site_id", "vendor_site_code", "vendor_tax_id", "vendor_email",
                 "vendor_address", "org_id", "operating_unit", "ledger_currency", "currency_code",
                 "exchange_rate_type", "terms_id", "terms_name", "po_number", "po_release_num", "description", "location_ref",
                 "cost_center", "subtotal", "tax_rate", "tax_amount", "freight_amount", "invoice_amount",
                 "bank_account", "bank_routing", "iban", "bank_change_notice", "source"]
# What the invoice itself states: terms or a due date, whichever is printed (both when both are). The
# derived "Payment terms" / "Due date" columns above always hold the values EBS gets.
PRINTED_COLUMNS = ["Payment terms on invoice", "Due date on invoice"]
CHECK_COLUMNS =["Taxes", "Country (hint)", "Validation result", "Failed checks", "Warning checks",
                 "Validation messages", "Open review reasons", "Suggested action", "Errors", "Flags", "Notes"]
LINE_FIELDS = ["line_number", "line_type", "printed", "description", "item_code", "quantity", "uom", "unit_price",
               "amount", "tax_rate", "tax_rate_code", "category_code", "category_id", "po_number", "po_release_num", "po_line_number",
               "dist_account"]
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_PATH = re.compile(r"(lines|taxes)\[(\d+)\]\.(\w+)")

README = """\
AAP export of {what}, made {when}.

invoice.csv           The invoice in the standard CSV layout (below); opens in Excel
invoice.json          The newest invoice version with every field and its provenance (as printed,
                      page, method, flags, note)
validation.json       Every validation run of every version
extraction_runs.json  Every extraction run: model, prompt version, status, raw output, printed values
model_output/         Exactly what the model returned, one file per request
                      (run-N-request-K.json; -truncated.txt when the answer was cut off)

Standard CSV layout: one row per invoice line; the document, header, validation and review columns
repeat on every row of the invoice. An invoice without lines is one row with the line columns empty.
The columns are the same for every invoice, so files can be appended. Dates YYYY-MM-DD, amounts
plain decimals with a point, yes / no, lists separated by "; ", validation messages by " | ".
"Payment terms" and "Due date" are the values sent to Oracle (one derived from the other when the
invoice prints only one); "Payment terms on invoice" and "Due date on invoice" hold only what the
invoice prints, so one of them is usually empty.
"""


class ExportError(LookupError):
    pass


# ------------------------------------------------------------------ loading
@dataclass
class DocumentData:
    doc: Document
    version: InvoiceVersion | None = None              # newest version (the approved one after approval)
    invoice: CanonicalInvoice | None = None
    validation: list[ValidationResult] = field(default_factory=list)          # current run
    validation_history: list[tuple[InvoiceVersion, ValidationResult]] = field(default_factory=list)
    runs: list[ExtractionRun] = field(default_factory=list)
    tasks: list[ReviewTask] = field(default_factory=list)                     # open review tasks


def load_invoice(version: InvoiceVersion) -> CanonicalInvoice:
    """Validated through the model, so a version stored before a field existed still has every key (empty)."""
    return CanonicalInvoice.model_validate(version.canonical_invoice)


def load_document(session: Session, document_id: uuid.UUID) -> DocumentData:
    doc = session.get(Document, document_id)
    if doc is None:
        raise ExportError("document not found")
    versions = list(session.scalars(select(InvoiceVersion).where(InvoiceVersion.document_id == doc.id)
                                    .order_by(InvoiceVersion.version)))
    by_id = {v.id: v for v in versions}
    history = [(by_id[r.invoice_version_id], r) for r in session.scalars(
        select(ValidationResult).where(ValidationResult.invoice_version_id.in_(by_id)).order_by(ValidationResult.id))]
    reviewed = next((v for v in reversed(versions) if v.kind in REVIEWED_KINDS), None)
    last_run = max((r.run_no for v, r in history if v is reviewed), default=None)
    newest = versions[-1] if versions else None
    return DocumentData(
        doc=doc, version=newest, invoice=load_invoice(newest) if newest else None,
        validation=[r for v, r in history if v is reviewed and r.run_no == last_run], validation_history=history,
        runs=list(session.scalars(select(ExtractionRun).where(ExtractionRun.document_id == doc.id)
                                  .order_by(ExtractionRun.created_at))),
        tasks=list(session.scalars(select(ReviewTask).where(ReviewTask.document_id == doc.id,
                                                           ReviewTask.status == "open")
                                   .order_by(ReviewTask.created_at))))


def latest_invoice(session: Session, document_id: uuid.UUID) -> tuple[Document, InvoiceVersion]:
    """The document's newest invoice version of any kind (after approval: the approved one)."""
    data = load_document(session, document_id)
    if data.version is None:
        raise ExportError("no invoice version yet")
    return data.doc, data.version


# ------------------------------------------------------------------- CSV
def label(name: str) -> str:
    """invoice_num -> Invoice number; lines[2].unit_price -> Line 2 unit price."""
    m = _PATH.fullmatch(name)
    if m:
        kind, n, attr = m.groups()
        return f"{'Line' if kind == 'lines' else 'Tax'} {n} {_lower_first(label(attr))}"
    if name in LABELS:
        return LABELS[name]
    text = " ".join(WORDS.get(w, w) for w in name.split("_"))
    return text[:1].upper() + text[1:]


def _lower_first(text: str) -> str:
    """"Unit price" -> "unit price", but "GL account" and "PO line" keep their capitals."""
    return text if text[1:2].isupper() else text[:1].lower() + text[1:]


INVOICE_CSV_COLUMNS = [*DOCUMENT_COLUMNS, *(label(n) for n in HEADER_FIELDS), *PRINTED_COLUMNS, *CHECK_COLUMNS,
                       *("Line number" if n == "line_number" else f"Line {_lower_first(label(n))}"
                         for n in LINE_FIELDS), "Line flags", "Line notes"]


def _cell(value: Any) -> str:
    """Text for a CSV cell. Supplier text that starts like a spreadsheet formula gets a leading
    quote, so opening the file in Excel never runs it (negative amounts stay numbers)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    text = str(value)
    if text[:1] in ("=", "+", "@", "\t", "\r") or (text[:1] == "-" and not _NUMBER.fullmatch(text)):
        return "'" + text
    return text


def csv_text(columns: list[str], rows: Iterable[list[Any]]) -> str:
    out = io.StringIO()
    out.write("﻿")                            # BOM: Excel then reads the file as UTF-8 (é, ß, ¢)
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(columns)
    w.writerows([_cell(v) for v in row] for row in rows)
    return out.getvalue()


def printed_value(f: Field) -> Any:
    """The value when the invoice states it: not derived or defaulted, and found on the page."""
    if f.value is None or f.method in (Method.derived, Method.default) or Flag.not_found_in_source in f.flags:
        return None
    return f.value


def _printed(inv: CanonicalInvoice | None) -> list[Any]:
    """PRINTED_COLUMNS: payment terms only when terms are printed (mapped to the EBS name when they
    could be), the due date only when a due date is printed."""
    if inv is None:
        return [None, None]
    return [printed_value(inv.terms_name), printed_value(inv.due_date)]


def _flags_and_notes(fields:Iterable[tuple[str, dict[str, Any]]]) -> tuple[list[str], list[str]]:
    flags, notes = [], []
    for name, f in fields:
        flags += [f"{label(name)}: {flag}" for flag in f.get("flags") or []]
        if f.get("note"):
            notes.append(f"{label(name)}: {f['note']}")
    return flags, notes


def _checks(data: DocumentData) -> list[Any]:
    """The CHECK_COLUMNS of an invoice: taxes, validation, review, errors, flags and notes."""
    inv = data.invoice.model_dump(mode="json") if data.invoice else None
    results = data.validation
    fails = [r for r in results if r.result == "fail"]
    warns = [r for r in results if r.result == "warning"]
    overall = "fail" if fails else "warning" if warns else "pass" if results else ""
    errors = [data.doc.error] if data.doc.error else []
    errors += [f"extraction run {n}: {r.error}" for n, r in enumerate(data.runs, start=1) if r.status != "ok"]
    flags = [f"document: {f}" for f in data.doc.flags or []]
    taxes, notes = [], []
    if inv is not None:
        flags += [f"invoice: {f}" for f in inv["flags"]]
        header = [(n, v) for n, v in inv.items() if isinstance(v, dict)]
        header += [(f"taxes[{t['tax_no']}].{n}", t[n]) for t in inv["taxes"] for n in ("label", "rate", "amount")]
        more_flags, notes = _flags_and_notes(header)
        flags += more_flags
        for t in inv["taxes"]:
            name, rate = t["label"]["value"], t["rate"]["value"]
            rate = f"{Decimal(rate).normalize():f}%" if rate is not None else None
            if rate and rate in (name or "").replace(" ", ""):
                rate = None                        # "Sales Tax 6%": the label already shows it
            taxes.append(" ".join(str(x) for x in (name, rate, t["amount"]["value"]) if x is not None))
    return [taxes, inv["country_hint"] if inv else None, overall, [r.rule_id for r in fails],
            [r.rule_id for r in warns], " | ".join(f"{r.rule_id} {r.rule_name}: {r.message or ''}".rstrip(": ")
                                                   for r in fails + warns),
            list(dict.fromkeys(c for t in data.tasks for c in t.reason_codes)),
            list(dict.fromkeys(t.suggested_action for t in data.tasks if t.suggested_action)), errors, flags, notes]


def invoice_csv_rows(data: DocumentData) -> list[list[Any]]:
    """The invoice in the standard layout: one row per line (one row if it has none)."""
    inv = data.invoice.model_dump(mode="json") if data.invoice else None
    version = data.version
    lead = [data.doc.id, data.doc.filename, data.doc.status, data.doc.created_at.isoformat(timespec="seconds"),
            version.version if version else None, version.kind if version else None,
            *((inv[n]["value"] if inv else None) for n in HEADER_FIELDS), *_printed(data.invoice), *_checks(data)]
    lines = inv["lines"] if inv else []
    if not lines:
        return [lead + [None] * (len(LINE_FIELDS) + 2)]
    rows = []
    for ln in lines:
        flags, notes = _flags_and_notes((n, v) for n, v in ln.items() if isinstance(v, dict))
        values = [ln[n]["value"] if isinstance(ln[n], dict) else ln[n] for n in LINE_FIELDS]
        rows.append(lead + values + [flags, notes])
    return rows


def invoice_csv(data: DocumentData) -> str:
    return csv_text(INVOICE_CSV_COLUMNS, invoice_csv_rows(data))


# ------------------------------------------------------------------- JSON
def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n"


def invoice_json(doc: Document, version: InvoiceVersion) -> dict[str, Any]:
    """Every header, tax-row and line field with its provenance, and which version it is."""
    return {"document_id": str(doc.id), "filename": doc.filename, "status": doc.status,
            "version": version.version, "kind": version.kind, "created_at": version.created_at.isoformat(),
            "invoice": load_invoice(version).model_dump(mode="json")}


def validation_json(data: DocumentData) -> list[dict[str, Any]]:
    return [{"version": v.version, "kind": v.kind, "run_no": r.run_no, "rule_id": r.rule_id, "rule_name": r.rule_name,
             "result": r.result, "message": r.message, "field_paths": r.field_paths, "details": r.details,
             "created_at": r.created_at} for v, r in data.validation_history]


def extraction_runs_json(data: DocumentData) -> list[dict[str, Any]]:
    return [{"run": n, "id": str(r.id), "created_at": r.created_at, "backend": r.backend, "model": r.model,
             "prompt_version": r.prompt_version, "status": r.status, "error": r.error, "requests": r.requests,
             "duration_ms": r.duration_ms, "route_summary": r.route_summary, "raw_output": r.raw_output,
             "source_invoice": r.source_invoice} for n, r in enumerate(data.runs, start=1)]


def model_outputs(data: DocumentData) -> Iterator[tuple[str, str]]:
    """(file name, content) of each model answer: the JSON the model returned, or the raw text of an
    answer cut off at max_tokens. Clerk corrections (backend `user`) are not model output."""
    for n, run in enumerate(data.runs, start=1):
        for k, entry in enumerate(run.raw_output or [], start=1):
            if not isinstance(entry, dict):
                continue
            if "output" in entry:
                yield f"run-{n}-request-{k}.json", _json(entry["output"])
            elif "raw" in entry:
                yield f"run-{n}-request-{k}-truncated.txt", str(entry["raw"])


# -------------------------------------------------------------------- ZIP
def folder_name(doc: Document, inv: CanonicalInvoice | None) -> str:
    base = (inv.invoice_num.value if inv else None) or (doc.filename or "").rsplit(".", 1)[0] or "document"
    return f"{_safe(base)[:60]}-{str(doc.id)[:8]}"


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "x"


def export_filename(doc: Document, version: InvoiceVersion, ext: str) -> str:
    inv = (version.canonical_invoice or {}).get("invoice_num") or {}
    return f"{_safe(str(inv.get('value') or doc.id))}-v{version.version}-{version.kind}.{ext}"


def _write_document(zf: zipfile.ZipFile, prefix: str, data: DocumentData) -> None:
    files = {"invoice.csv": invoice_csv(data), "validation.json": _json(validation_json(data)),
             "extraction_runs.json": _json(extraction_runs_json(data))}
    if data.version is not None:
        files["invoice.json"] = _json(invoice_json(data.doc, data.version))
    for name, text in files.items():
        zf.writestr(prefix + name, text)
    for name, text in model_outputs(data):
        zf.writestr(f"{prefix}model_output/{name}", text)


def _readme(what: str) -> str:
    from datetime import UTC, datetime

    return README.format(what=what, when=datetime.now(UTC).isoformat(timespec="seconds"))


def document_zip(data: DocumentData) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _readme(f"document {data.doc.id} ({data.doc.filename})"))
        _write_document(zf, "", data)
    return buf.getvalue()


def bulk_zip(session: Session, document_ids: Iterable[uuid.UUID]) -> IO[bytes]:
    """Every given document in its own folder, plus invoices.csv with all their rows. Written to a
    temporary file (in memory up to 32 MB) that the caller streams and closes."""
    rows: list[list[Any]] = []
    out = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)  # noqa: SIM115 - the caller closes it
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        n = 0
        for document_id in document_ids:
            try:
                data = load_document(session, document_id)
            except ExportError:
                continue
            _write_document(zf, f"documents/{folder_name(data.doc, data.invoice)}/", data)
            rows += invoice_csv_rows(data)
            session.expunge_all()                  # keep memory flat over thousands of documents
            n += 1
        zf.writestr("README.txt", _readme(f"{n} document(s)") + "\ninvoices.csv at the top has the rows of every "
                    "document in the standard layout; documents/ has one folder per document.\n")
        zf.writestr("invoices.csv", csv_text(INVOICE_CSV_COLUMNS, rows))
    out.seek(0)
    return out
