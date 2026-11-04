"""Oracle-ready JSON (pipeline/oracle_json.py), interface payload (adapters/payload.py), chart of
accounts layout (common/coa.py) and the instance value map (mapping/instance.py), without a database.
All values are made up."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aap.adapters.payload import build_payload
from aap.common.coa import CoaLayout
from aap.mapping.instance import InstanceMap, InstanceMapError, load_instance_map
from aap.pipeline.oracle_json import (
    SAMPLE_FILE,
    SCHEMA_FILE,
    JsonContext,
    build_oracle_invoice,
    json_schema,
    sample,
    to_json,
)
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
from aap.schemas.fields import Field, Flag, Method

D = Decimal
KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)
ACCOUNT = "10.20.00110.61210.00000.000.00.000"
NOW = datetime(2026, 11, 4, tzinfo=UTC)


def _invoice(**over) -> CanonicalInvoice:
    inv = CanonicalInvoice()
    values = dict(invoice_num="INV-1001", invoice_date=date(2026, 10, 1), gl_date=date(2026, 10, 2), vendor_id=301,
                  vendor_number="22001", vendor_name="Example Packaging Co", vendor_site_id=901,
                  vendor_site_code="MAIN", org_id=204, operating_unit="Example US OU", currency_code="USD",
                  invoice_amount=D("1219.30"), terms_id=11, terms_name="30 NET", terms_date=date(2026, 10, 1),
                  payment_method="EFT", pay_group="STANDARD", payment_currency_code="USD", tax_amount=D("19.30"))
    values.update(over)
    for k, v in values.items():
        setattr(inv, k, Field(value=v))
    inv.lines = [CanonicalLine(line_number=1, description=Field(value="Folding cartons"), amount=Field(value=D("1200.00")),
                               dist_account=Field(value=ACCOUNT, method=Method.derived))]
    return inv


def _ctx(**over) -> JsonContext:
    values = dict(document_id="doc-1", filename="inv.pdf", document_status="IN_REVIEW", version=3,
                  version_kind="proposed", validation=[("C3 Lines subtotal", "pass", "")],
                  gl={1: {"account": ACCOUNT, "auto": True, "reason": "exact history"}})
    values.update(over)
    return JsonContext(**values)


def _build(inv=None, ctx=None, imap=None):
    return build_oracle_invoice(inv or _invoice(), ctx or _ctx(), source="AAP", coa=KK, imap=imap, now=NOW)


# ------------------------------------------------------------------ layout
def test_layout_splits_and_replaces_segments():
    assert KK.natural_account(ACCOUNT) == "61210" and KK.cost_center(ACCOUNT) == "00110" and KK.company(ACCOUNT) == "10"
    assert KK.with_cost_center(ACCOUNT, "00316") == "10.20.00316.61210.00000.000.00.000"
    assert KK.with_natural_account(ACCOUNT, "61000") == "10.20.00110.61000.00000.000.00.000"
    mock = CoaLayout()
    assert mock.natural_account("01-510-6420-0000") == "6420"
    assert mock.split("01-510-6420") is None and KK.split("01-510-6420-0000") is None
    assert KK.split("04..00110.61210.00000.000.00.000") is None          # an empty segment


def test_layout_rejects_impossible_settings():
    with pytest.raises(ValueError):
        CoaLayout(segment_count=4, natural_account_segment=5)
    with pytest.raises(ValueError):
        CoaLayout(separator="a")


# ----------------------------------------------------------------- payload
def test_payload_has_business_keys_line_dates_and_a_tax_line():
    p = build_payload(_invoice(), source="AAP")
    assert (p.vendor_num, p.vendor_site_code, p.operating_unit, p.payment_method_code) == \
        ("22001", "MAIN", "Example US OU", "EFT")
    assert [ln.line_type_lookup_code for ln in p.lines] == ["ITEM", "TAX"]
    assert all(ln.accounting_date == date(2026, 10, 2) for ln in p.lines)
    assert p.terms_name is None and p.terms_id == 11          # the interface gets the ID when there is one


def test_payload_strict_needs_the_required_values():
    inv = _invoice(invoice_num=None)
    with pytest.raises(ValidationError):
        build_payload(inv, source="AAP")
    assert build_payload(inv, source="AAP", strict=False).invoice_num is None


def test_adapter_posts_the_same_payload():
    from aap.adapters.ebs_mock import MockEbsAdapter

    adapter = MockEbsAdapter("postgresql://unused/ebs", coa=KK)       # to_payload needs no connection
    assert adapter.to_payload(_invoice()) == build_payload(_invoice(), source="AAP")


# -------------------------------------------------------------------- JSON
def test_clean_invoice_is_ready_with_one_distribution_per_line():
    doc = _build()
    assert doc.status.state == "ready" and doc.status.postable and not doc.status.missing
    assert doc.header.vendor_num == "22001" and doc.header.terms_name == "30 NET"
    item, tax = doc.lines
    assert item.account_source == "line" and item.distributions[0].dist_code_concatenated == ACCOUNT
    assert item.distributions[0].amount == D("1200.00")
    assert tax.account_source == "oracle_setup" and not tax.distributions
    assert doc.source_ids.vendor_id == 301 and doc.meta.account_layout.separator == "."


def test_po_matched_line_has_no_account_of_its_own():
    inv = _invoice(po_number="4500123")
    inv.lines[0].po_line_number = Field(value=1)
    inv.lines[0].dist_account = Field(value=None)
    line = _build(inv).lines[0]
    assert line.account_source == "po_match" and line.po_number == "4500123" and not line.distributions
    assert "lines[1].dist_code_concatenated" not in _build(inv).status.missing


def test_missing_required_values_block_posting():
    inv = _invoice(payment_method=None, vendor_site_code=None)
    inv.lines[0].dist_account = Field(value=None)
    doc = _build(inv)
    assert doc.status.state == "needs_review" and not doc.status.postable
    assert {"header.payment_method_code", "header.vendor_site_code", "lines[1].dist_code_concatenated"} \
        <= set(doc.status.missing)


def test_uncertain_values_are_listed_with_the_reason():
    inv = _invoice()
    inv.terms_name = Field(value="30 NET", flags=[Flag.check_map], note="supplier site default")
    doc = _build(inv, _ctx(gl={1: {"account": ACCOUNT, "auto": False, "reason": "model chose 61210"}}))
    codes = {(i.field, i.code) for i in doc.status.issues}
    assert ("header.terms_name", "check_map") in codes
    assert ("lines[1].dist_code_concatenated", "gl_review") in codes
    assert doc.status.state == "needs_review"


def test_review_reasons_and_status_decide_the_state():
    assert _build(ctx=_ctx(review_reasons=["GL_REVIEW"])).status.state == "needs_review"
    assert _build(ctx=_ctx(document_status="ON_HOLD")).status.state == "on_hold"
    approved = _build(ctx=_ctx(document_status="APPROVED", version_kind="approved"))
    assert approved.status.state == "approved" and approved.status.postable
    assert _build(ctx=_ctx(document_status="MAPPED")).status.state == "not_ready"
    failed = _build(ctx=_ctx(validation=[("C3 Lines subtotal", "fail", "lines 1200.00 vs subtotal 1300.00")]))
    assert failed.status.state == "needs_review" and failed.status.validation == "fail"
    assert failed.status.validation_messages == ["C3 Lines subtotal: lines 1200.00 vs subtotal 1300.00"]


def test_json_is_plain_json_with_amounts_as_strings():
    data = json.loads(json.dumps(to_json(_build())))
    assert data["header"]["invoice_amount"] == "1219.30" and data["lines"][0]["amount"] == "1200.00"


def test_schema_file_is_up_to_date():
    """docs/oracle-invoice.schema.json is what Dhivya codes the push against: regenerate it with
    `python -m aap.pipeline.oracle_json --schema` after changing the models."""
    assert json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) == json_schema()
    assert json.loads(SAMPLE_FILE.read_text(encoding="utf-8")) == to_json(sample())


# ------------------------------------------------------------ instance map
def _map(tmp_path, text: str) -> InstanceMap:
    path = tmp_path / "internal.csv"
    path.write_text(text, encoding="utf-8")
    return load_instance_map(path)


def test_instance_map_translates_values_and_reports_gaps(tmp_path):
    imap = _map(tmp_path, "kind,scope,source,target\n"
                          "supplier,,22001,10345\n"
                          "site,22001,MAIN,MAIN-01\n"
                          "terms,,30 NET,Net 30\n"
                          "segment3,,00110,00950\n")
    doc = _build(imap=imap)
    assert (doc.header.vendor_num, doc.header.vendor_site_code, doc.header.terms_name) == ("10345", "MAIN-01", "Net 30")
    assert doc.lines[0].dist_code_concatenated == "10.20.00950.61210.00000.000.00.000"
    assert doc.lines[0].distributions[0].dist_code_concatenated == doc.lines[0].dist_code_concatenated
    assert doc.header.payment_method_code == "EFT"           # no payment_method rows: same in both instances
    assert doc.meta.target_instance == "internal" and not doc.status.issues


def test_value_missing_from_a_covered_kind_is_an_issue(tmp_path):
    imap = _map(tmp_path, "kind,source,target\nsupplier,99999,10345\naccount,01-000-1000-0000,x\n")
    doc = _build(imap=imap)
    assert doc.header.vendor_num == "22001"
    assert {(i.field, i.code) for i in doc.status.issues} == {
        ("header.vendor_num", "not_in_instance_map"), ("lines[1].dist_code_concatenated", "not_in_instance_map")}
    assert doc.status.state == "needs_review"


def test_instance_map_rejects_bad_rows(tmp_path):
    with pytest.raises(InstanceMapError):
        _map(tmp_path, "kind,source,target\nvendor,1,2\n")
    with pytest.raises(InstanceMapError):
        _map(tmp_path, "kind,source,target\nterms,30 NET,A\nterms,30 net,B\n")
    with pytest.raises(InstanceMapError):
        _map(tmp_path, "source,target\n1,2\n")
