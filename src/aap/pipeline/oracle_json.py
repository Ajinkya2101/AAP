"""Oracle-ready JSON: one document per invoice with everything Oracle needs to create it (Siva,
2026-11-02: "invoice in, one JSON out, a Python program calls the Oracle API / interface").

Built from the same InterfacePayload the EBS adapter inserts into the Payables Open Interface
(adapters/payload.py), so the JSON and what is posted cannot differ. On top of the interface rows:

- business keys first: supplier number, site code, operating unit name, terms name, payment method
  code and the account string. IDs differ between the Krispy Kreme instance and the internal one,
  so the IDs of the instance the reference data came from are kept apart in `source_ids`;
- one distribution per non-PO line (Vikram's mandatory-field list: number, type, amount, GL date,
  account); PO-matched lines get theirs from the PO, tax and freight lines from Oracle's setup;
- `status`: whether the invoice can be created as it is, the required fields that are empty, and
  every value that is not certain, with the reason;
- optional translation to the target instance's values (mapping/instance.py).

The JSON Schema is docs/oracle-invoice.schema.json (`python -m aap.pipeline.oracle_json --schema`);
a unit test keeps it in step with the models below.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic import Field as PydField
from sqlalchemy.orm import Session

from aap.adapters.payload import build_payload
from aap.common.coa import CoaLayout, layout_from_settings
from aap.mapping.instance import InstanceMap, instance_map_from_settings
from aap.schemas.canonical import CanonicalInvoice
from aap.schemas.fields import Field, Flag, Method

SCHEMA_ID = "aap.oracle-invoice"
SCHEMA_VERSION = "1.0"
SCHEMA_FILE = Path(__file__).resolve().parents[3] / "docs" / "oracle-invoice.schema.json"

AccountSource = Literal["line", "po_match", "oracle_setup"]
ValidationResult = Literal["pass", "warning", "fail", "not_run"]
State = Literal["ready", "needs_review", "on_hold", "approved", "submitted", "created", "rejected", "not_ready"]


# ================================================================== models
class Distribution(BaseModel):
    """AP_INVOICE_DISTRIBUTIONS: one per non-PO line here (the whole line amount to its account)."""
    distribution_line_number: int
    line_type_lookup_code: str
    amount: Decimal | None
    accounting_date: date | None = PydField(description="GL date of the distribution")
    dist_code_concatenated: str | None = PydField(description="Account: the concatenated segments")


class Line(BaseModel):
    """AP_INVOICE_LINES_INTERFACE columns."""
    line_number: int
    line_type_lookup_code: str = PydField(description="ITEM, TAX, FREIGHT or MISCELLANEOUS")
    amount: Decimal | None
    accounting_date: date | None = PydField(description="GL date of the line")
    description: str | None = None
    quantity_invoiced: Decimal | None = None
    unit_price: Decimal | None = None
    unit_of_meas_lookup_code: str | None = None
    po_number: str | None = None
    release_num: int | None = None
    po_line_number: int | None = None
    tax_rate_code: str | None = None
    dist_code_concatenated: str | None = PydField(
        default=None, description="Account of a non-PO line; empty on PO-matched, tax and freight lines")
    account_source: AccountSource = PydField(
        description="line: the account above; po_match: Oracle takes the PO distribution's; "
                    "oracle_setup: Oracle's tax / freight setup decides")
    distributions: list[Distribution] = PydField(default_factory=list)


class Header(BaseModel):
    """AP_INVOICES_INTERFACE columns, by business key (no instance IDs)."""
    operating_unit: str | None
    invoice_type_lookup_code: str | None = PydField(description="STANDARD, CREDIT, ...")
    vendor_name: str | None = PydField(description="Trading partner (supplier name)")
    vendor_num: str | None = PydField(description="Supplier number (AP_SUPPLIERS.SEGMENT1)")
    vendor_site_code: str | None
    invoice_num: str | None
    invoice_date: date | None
    invoice_currency_code: str | None
    invoice_amount: Decimal | None
    gl_date: date | None
    payment_currency_code: str | None
    terms_name: str | None
    terms_date: date | None
    payment_method_code: str | None
    pay_group_lookup_code: str | None = None
    exchange_rate_type: str | None = None
    exchange_date: date | None = None
    po_number: str | None = None
    description: str | None = None
    source: str
    due_date: date | None = PydField(default=None, description="For information: Oracle computes it from the terms")


class SourceIds(BaseModel):
    """IDs in the instance the reference data was read from. Valid only there; on another instance
    look the records up by the business keys in `header`."""
    instance: str
    org_id: int | None = None
    vendor_id: int | None = None
    vendor_site_id: int | None = None
    terms_id: int | None = None


class Issue(BaseModel):
    field: str = PydField(description="JSON path, e.g. header.terms_name or lines[2].dist_code_concatenated")
    code: str = PydField(description="Why the value is not certain (e.g. check_map, gl_review, not_in_instance_map)")
    detail: str | None = None


class Status(BaseModel):
    state: State = PydField(description="ready: can be created as it is; needs_review / on_hold: a clerk decides "
                                        "first; approved / submitted / created: decided in the application")
    postable: bool = PydField(description="Every required field is filled and the state is ready or approved")
    validation: ValidationResult
    validation_messages: list[str] = PydField(default_factory=list)
    review_reasons: list[str] = PydField(default_factory=list)
    missing: list[str] = PydField(default_factory=list, description="Required fields without a value")
    issues: list[Issue] = PydField(default_factory=list)


class AccountLayout(BaseModel):
    segment_count: int
    separator: str


class Meta(BaseModel):
    schema_id: str = SCHEMA_ID
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    document_id: str
    filename: str | None
    document_status: str
    invoice_version: int
    invoice_version_kind: str = PydField(description="approved: what the clerk approved; proposed: the newest "
                                                     "reviewed version with the agents' PO and GL decisions")
    model: str | None = None
    prompt_version: str | None = None
    account_layout: AccountLayout
    target_instance: str = PydField(description="Whose values the JSON carries (instance value map)")


class OracleInvoice(BaseModel):
    meta: Meta
    status: Status
    header: Header
    lines: list[Line]
    source_ids: SourceIds


# ========================================================= required fields
# Invoice Workbench mandatory fields (Vikram, AI AP Excel.xlsx, 2026-11-04).
REQUIRED_HEADER = ["operating_unit", "invoice_type_lookup_code", "vendor_name", "vendor_num", "vendor_site_code",
                   "invoice_date", "invoice_num", "invoice_currency_code", "invoice_amount", "gl_date",
                   "payment_currency_code", "terms_date", "terms_name", "payment_method_code"]
REQUIRED_LINE = ["line_type_lookup_code", "amount", "accounting_date"]
REQUIRED_DISTRIBUTION = ["line_type_lookup_code", "amount", "accounting_date", "dist_code_concatenated"]

# Field flags that make a value uncertain (the others only explain how it was read).
UNCERTAIN = {Flag.no_map, Flag.check_map, Flag.missing, Flag.not_found_in_source, Flag.low_match_score,
             Flag.terms_due_date_conflict, Flag.implausible_value, Flag.currency_ambiguous, Flag.date_parse_error,
             Flag.amount_parse_error, Flag.two_date_formats, Flag.unverified_image_only}
HEADER_PATHS = {"vendor_id": "vendor_name", "vendor_name": "vendor_name", "vendor_site_id": "vendor_site_code",
                "invoice_num": "invoice_num", "invoice_date": "invoice_date", "invoice_amount": "invoice_amount",
                "currency_code": "invoice_currency_code", "terms_id": "terms_name", "terms_name": "terms_name",
                "gl_date": "gl_date", "invoice_type": "invoice_type_lookup_code", "org_id": "operating_unit",
                "payment_method": "payment_method_code", "pay_group": "pay_group_lookup_code",
                "payment_currency_code": "payment_currency_code", "terms_date": "terms_date",
                "po_number": "po_number", "exchange_rate_type": "exchange_rate_type"}
LINE_PATHS = {"description": "description", "amount": "amount", "quantity": "quantity_invoiced",
              "unit_price": "unit_price", "uom": "unit_of_meas_lookup_code", "dist_account": "dist_code_concatenated",
              "po_line_number": "po_line_number", "po_number": "po_number", "tax_rate_code": "tax_rate_code"}


# ================================================================== build
@dataclass
class JsonContext:
    """What the JSON says about the document besides the invoice itself."""
    document_id: str
    filename: str | None
    document_status: str
    version: int
    version_kind: str
    model: str | None = None
    prompt_version: str | None = None
    validation: list[tuple[str, str, str]] = field(default_factory=list)   # (rule id + name, result, message)
    review_reasons: list[str] = field(default_factory=list)
    gl: dict[int, dict[str, Any]] = field(default_factory=dict)            # agents' GL decision per line number
    source_instance: str = "the reference data's instance"


def _issues(inv: CanonicalInvoice, ctx: JsonContext) -> list[Issue]:
    out: list[Issue] = []

    def add(path: str, f: Field) -> None:
        codes = [fl for fl in f.flags if fl in UNCERTAIN]
        if f.method == Method.fuzzy_llm and Flag.check_map not in codes:
            codes.append("chosen_by_model")
        for code in codes:
            out.append(Issue(field=path, code=code, detail=f.note))

    for name, path in HEADER_PATHS.items():
        add(f"header.{path}", getattr(inv, name))
    for n, ln in enumerate(inv.lines, start=1):
        for name, path in LINE_PATHS.items():
            add(f"lines[{n}].{path}", getattr(ln, name))
        g = ctx.gl.get(ln.line_number)
        if g and g.get("account") and not g.get("auto", True) and ln.dist_account.method != Method.user \
                and ln.po_line_number.value is None:
            out.append(Issue(field=f"lines[{n}].dist_code_concatenated", code="gl_review", detail=g.get("reason")))
    seen: set[tuple[str, str]] = set()
    return [i for i in out if (i.field, i.code) not in seen and not seen.add((i.field, i.code))]  # type: ignore[func-returns-value]


def _line(ln: Any) -> Line:
    paired = ln.po_line_number is not None
    account = ln.dist_code_concatenated
    own_account = ln.line_type_lookup_code in ("ITEM", "MISCELLANEOUS") or bool(account)
    source: AccountSource = "po_match" if paired else "line" if own_account else "oracle_setup"
    dists = []
    if source == "line":
        dists.append(Distribution(distribution_line_number=1, line_type_lookup_code=ln.line_type_lookup_code,
                                  amount=ln.amount, accounting_date=ln.accounting_date,
                                  dist_code_concatenated=account))
    return Line(line_number=ln.line_number, line_type_lookup_code=ln.line_type_lookup_code, amount=ln.amount,
                accounting_date=ln.accounting_date, description=ln.description,
                quantity_invoiced=ln.quantity_invoiced, unit_price=ln.unit_price,
                unit_of_meas_lookup_code=ln.unit_of_meas_lookup_code, po_number=ln.po_number,
                release_num=ln.release_num, po_line_number=ln.po_line_number, tax_rate_code=ln.tax_rate_code,
                dist_code_concatenated=account, account_source=source, distributions=dists)


def _translate(header: Header, lines: list[Line], imap: InstanceMap, coa: CoaLayout) -> list[Issue]:
    """Apply the instance value map in place; return the values it has no row for."""
    issues: list[Issue] = []

    def tr(kind: str, value: str | None, path: str, scope: str | None = None) -> str | None:
        target, found = imap.lookup(kind, value, scope)
        if not found:
            issues.append(Issue(field=path, code="not_in_instance_map", detail=f"{kind} {value!r} has no row in "
                                f"the instance value map {imap.name!r}; passed on unchanged"))
        return target

    source_vendor = header.vendor_num
    header.operating_unit = tr("operating_unit", header.operating_unit, "header.operating_unit")
    header.vendor_num = tr("supplier", header.vendor_num, "header.vendor_num")
    header.vendor_site_code = tr("site", header.vendor_site_code, "header.vendor_site_code", scope=source_vendor)
    header.terms_name = tr("terms", header.terms_name, "header.terms_name")
    header.payment_method_code = tr("payment_method", header.payment_method_code, "header.payment_method_code")
    header.pay_group_lookup_code = tr("pay_group", header.pay_group_lookup_code, "header.pay_group_lookup_code")
    header.invoice_type_lookup_code = tr("invoice_type", header.invoice_type_lookup_code,
                                         "header.invoice_type_lookup_code")
    for n, ln in enumerate(lines, start=1):
        ln.line_type_lookup_code = tr("line_type", ln.line_type_lookup_code,
                                      f"lines[{n}].line_type_lookup_code") or ln.line_type_lookup_code
        if ln.dist_code_concatenated:
            target, found = imap.account(ln.dist_code_concatenated, coa)
            if not found:
                issues.append(Issue(field=f"lines[{n}].dist_code_concatenated", code="not_in_instance_map",
                                    detail=f"account {ln.dist_code_concatenated!r} has no row in {imap.name!r}"))
            ln.dist_code_concatenated = target
        for d in ln.distributions:
            d.line_type_lookup_code = ln.line_type_lookup_code
            d.dist_code_concatenated = ln.dist_code_concatenated
    return issues


def _state(ctx: JsonContext, missing: list[str], issues: list[Issue], validation: str) -> State:
    status = ctx.document_status
    if status in ("APPROVED", "SUBMITTED", "CREATED", "REJECTED"):
        return status.lower()  # type: ignore[return-value]
    if status == "ON_HOLD":
        return "on_hold"
    if status in ("IN_REVIEW", "ORACLE_ERROR"):
        clean = not ctx.review_reasons and not missing and not issues and validation != "fail"
        return "ready" if clean else "needs_review"
    return "not_ready"


def build_oracle_invoice(inv: CanonicalInvoice, ctx: JsonContext, *, source: str,
                         coa: CoaLayout | None = None, imap: InstanceMap | None = None,
                         now: datetime | None = None) -> OracleInvoice:
    """The Oracle-ready JSON of one invoice (pure: no database)."""
    coa = coa or layout_from_settings()
    imap = imap or InstanceMap()
    p = build_payload(inv, source=source, strict=False)
    header = Header(
        operating_unit=p.operating_unit, invoice_type_lookup_code=p.invoice_type_lookup_code,
        vendor_name=p.vendor_name, vendor_num=p.vendor_num, vendor_site_code=p.vendor_site_code,
        invoice_num=p.invoice_num, invoice_date=p.invoice_date, invoice_currency_code=p.invoice_currency_code,
        invoice_amount=p.invoice_amount, gl_date=p.gl_date, payment_currency_code=p.payment_currency_code,
        terms_name=inv.terms_name.value, terms_date=p.terms_date, payment_method_code=p.payment_method_code,
        pay_group_lookup_code=p.pay_group_lookup_code, exchange_rate_type=p.exchange_rate_type,
        exchange_date=p.exchange_date, po_number=p.po_number, description=p.description, source=p.source,
        due_date=inv.due_date.value)
    lines = [_line(ln) for ln in p.lines]
    issues = _issues(inv, ctx)
    if not imap.identity:
        issues += _translate(header, lines, imap, coa)

    missing = [f"header.{n}" for n in REQUIRED_HEADER if getattr(header, n) in (None, "")]
    if not lines:
        missing.append("lines")
    for n, ln in enumerate(lines, start=1):
        missing += [f"lines[{n}].{c}" for c in REQUIRED_LINE if getattr(ln, c) is None]
        if ln.account_source == "line" and not ln.dist_code_concatenated:
            missing.append(f"lines[{n}].dist_code_concatenated")
        for d in ln.distributions:
            missing += [f"lines[{n}].distributions[{d.distribution_line_number}].{c}"
                        for c in REQUIRED_DISTRIBUTION[:-1] if getattr(d, c) is None]
    results = [r for _, r, _ in ctx.validation]
    validation: ValidationResult = "fail" if "fail" in results else "warning" if "warning" in results else \
        "pass" if results else "not_run"
    state = _state(ctx, missing, issues, validation)
    status = Status(state=state, postable=state in ("ready", "approved") and not missing, validation=validation,
                    validation_messages=[f"{rule}: {msg}".rstrip(": ") for rule, r, msg in ctx.validation
                                         if r in ("fail", "warning")],
                    review_reasons=list(ctx.review_reasons), missing=missing, issues=issues)
    meta = Meta(generated_at=now or datetime.now(UTC), document_id=ctx.document_id, filename=ctx.filename,
                document_status=ctx.document_status, invoice_version=ctx.version, invoice_version_kind=ctx.version_kind,
                model=ctx.model, prompt_version=ctx.prompt_version,
                account_layout=AccountLayout(segment_count=coa.segment_count, separator=coa.separator),
                target_instance=imap.name)
    ids = SourceIds(instance=ctx.source_instance, org_id=inv.org_id.value, vendor_id=inv.vendor_id.value,
                    vendor_site_id=inv.vendor_site_id.value, terms_id=inv.terms_id.value)
    return OracleInvoice(meta=meta, status=status, header=header, lines=lines, source_ids=ids)


# ============================================================ from the db
class OracleJsonError(LookupError):
    pass


def oracle_invoice(session: Session, document_id: uuid.UUID, *, imap: InstanceMap | None = None) -> OracleInvoice:
    """The JSON of a document: the approved version once there is one, otherwise the newest reviewed
    version with the agents' PO pairing and GL accounts (what approval would freeze)."""
    from aap.config import get_settings
    from aap.pipeline.export import ExportError, load_document
    from aap.pipeline.stages import latest_version
    from aap.submit.decision import DecisionError, approved_invoice

    try:
        data = load_document(session, document_id)
    except ExportError as exc:
        raise OracleJsonError(str(exc)) from exc
    doc = data.doc
    approved = latest_version(session, doc.id, ["approved"])
    gl: dict[int, Any] = {}
    if approved is not None:
        inv, version, kind = CanonicalInvoice.model_validate(approved.canonical_invoice), approved, "approved"
    else:
        try:
            inv, version, gl = approved_invoice(session, doc)
        except DecisionError as exc:
            raise OracleJsonError(f"no invoice to export yet ({doc.status})") from exc
        kind = "proposed"
    run = next((r for r in reversed(data.runs) if r.status == "ok" and r.backend != "user"), None)
    ctx = JsonContext(document_id=str(doc.id), filename=doc.filename, document_status=doc.status,
                      version=version.version, version_kind=kind, model=run.model if run else None,
                      prompt_version=run.prompt_version if run else None,
                      validation=[(f"{r.rule_id} {r.rule_name}", r.result, r.message or "") for r in data.validation],
                      review_reasons=list(dict.fromkeys(c for t in data.tasks for c in t.reason_codes)), gl=gl)
    return build_oracle_invoice(inv, ctx, source=get_settings().invoice_source,
                                imap=imap if imap is not None else instance_map_from_settings())


def to_json(doc: OracleInvoice) -> dict[str, Any]:
    return doc.model_dump(mode="json")


def oracle_json_or_none(session: Session, document_id: uuid.UUID) -> dict[str, Any] | None:
    """For the export ZIPs: the JSON, or None for a document without an invoice yet."""
    try:
        return to_json(oracle_invoice(session, document_id))
    except OracleJsonError:
        return None


def json_schema() -> dict[str, Any]:
    schema = OracleInvoice.model_json_schema(mode="serialization")
    schema["$id"] = f"https://acse.example/schemas/{SCHEMA_ID}/{SCHEMA_VERSION}"
    schema["title"] = "Oracle-ready invoice"
    schema["description"] = (__doc__ or "").strip().split("\n\n")[0]
    return schema


SAMPLE_FILE = SCHEMA_FILE.with_name("oracle-invoice.sample.json")


def sample() -> OracleInvoice:
    """A made-up invoice: 8-segment accounts, a non-PO line, a freight line whose account needs review."""
    from aap.schemas.canonical import CanonicalLine

    inv = CanonicalInvoice()
    for k, v in dict(invoice_num="NWP-24017", invoice_date=date(2026, 10, 1), gl_date=date(2026, 10, 2),
                     due_date=date(2026, 10, 31), vendor_id=5001, vendor_number="70001",
                     vendor_name="Northwind Packaging LLC", vendor_site_id=6001, vendor_site_code="NWP-MAIN",
                     org_id=81, operating_unit="Example Donuts US OU", currency_code="USD",
                     invoice_amount=Decimal("1274.95"), terms_id=102, terms_name="30 NET 2%10",
                     terms_date=date(2026, 10, 1), payment_method="EFT", pay_group="STANDARD",
                     payment_currency_code="USD", description="Northwind Packaging LLC invoice NWP-24017").items():
        setattr(inv, k, Field(value=v))
    inv.lines = [
        CanonicalLine(line_number=1, description=Field(value="Donut boxes 12 count"),
                      quantity=Field(value=Decimal(500)), unit_price=Field(value=Decimal("2.40")),
                      amount=Field(value=Decimal("1200.00")),
                      dist_account=Field(value="10.20.00110.61210.00000.000.00.000", method=Method.derived)),
        CanonicalLine(line_number=2, line_type="FREIGHT", description=Field(value="Freight"),
                      amount=Field(value=Decimal("74.95")),
                      dist_account=Field(value="10.20.00110.65020.00000.000.00.000", method=Method.derived))]
    ctx = JsonContext(document_id="3f0c9a52-6f1e-4c1b-9d2e-5a7b8c9d0e1f", filename="NWP-24017.pdf",
                      document_status="IN_REVIEW", version=3, version_kind="proposed", model="qwen2.5-vl-7b",
                      prompt_version="v5", validation=[("C3 Lines subtotal", "pass", "")], review_reasons=["GL_REVIEW"],
                      gl={1: {"account": "10.20.00110.61210.00000.000.00.000", "auto": True, "reason": "exact history"},
                          2: {"account": "10.20.00110.65020.00000.000.00.000", "auto": False,
                              "reason": "model chose 65020 among 2 accounts"}})
    coa = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3,
                    natural_account_segment=4)
    return build_oracle_invoice(inv, ctx, source="AAP", coa=coa, now=datetime(2026, 11, 4, 9, 0, tzinfo=UTC))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.pipeline.oracle_json", description=__doc__.split("\n")[0])
    ap.add_argument("--schema", action="store_true",
                    help=f"write the JSON Schema to {SCHEMA_FILE} and a made-up sample next to it")
    ap.add_argument("--document", help="print the JSON of one document (needs the database)")
    a = ap.parse_args(argv)
    if a.schema:
        SCHEMA_FILE.write_text(json.dumps(json_schema(), indent=2) + "\n", encoding="utf-8")
        SAMPLE_FILE.write_text(json.dumps(to_json(sample()), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {SCHEMA_FILE} and {SAMPLE_FILE}")
    if a.document:
        from aap.db.session import session_scope

        with session_scope() as s:
            print(json.dumps(to_json(oracle_invoice(s, uuid.UUID(a.document))), indent=2))
    if not (a.schema or a.document):
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
