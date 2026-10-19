"""Clerk corrections of the printed values (aap.submit.correct), without a database."""

from __future__ import annotations

import pytest

from aap.schemas.fields import Method
from aap.schemas.source import ExtractedInvoice, SourceInvoice, _clean
from aap.submit.correct import apply_source_corrections
from aap.submit.decision import DecisionError


def _source() -> SourceInvoice:
    header = {name: None for name in ExtractedInvoice.model_fields if name != "lines"}
    header.update(vendor_name="Northwind IT Supplies Inc.", invoice_number="ALL28916", payment_terms="Terms",
                  total="150.00")
    line = dict(description=None, item_code=None, quantity=None, uom=None, unit_price=None, amount=None,
                tax_rate=None, po_line_ref=None)
    lines = [{**line, "description": "Laptop", "amount": "100.00"}, {**line, "description": "Subtotal"},
             {**line, "description": "Dock", "amount": "50.00"}]
    return SourceInvoice.from_extracted(ExtractedInvoice.model_validate({**header, "lines": lines}))


def test_only_changed_values_are_corrections():
    src = _source()
    changes = apply_source_corrections(src, "clerk", header={
        "vendor_name": "Northwind IT Supplies Inc.",     # as read: not a change
        "invoice_number": "AII28916", "payment_terms": "Net 30", "po_number": ""})
    assert changes == [{"path": "invoice_number", "model": "ALL28916", "corrected": "AII28916"},
                       {"path": "payment_terms", "model": "Terms", "corrected": "Net 30"}]
    assert src.invoice_number.method == Method.user and src.invoice_number.source_value == "AII28916"
    assert src.vendor_name.method == Method.extracted


def test_a_value_can_be_cleared():
    src = _source()
    changes = apply_source_corrections(src, "clerk", header={"payment_terms": ""})
    assert changes == [{"path": "payment_terms", "model": "Terms", "corrected": None}]
    assert src.payment_terms.value is None and src.payment_terms.source_value is None


def test_lines_edit_remove_add_and_renumber():
    src = _source()
    changes = apply_source_corrections(
        src, "clerk", lines={"1": {"amount": "110.00", "description": "Laptop"}}, remove_lines=[2],
        add_lines=[{"description": "Freight", "amount": "5.00", "position": 1}, {"description": " "}])
    assert [c["path"] for c in changes] == ["lines[1].amount", "lines[2]", "lines[+]"]
    assert [(ln.line_no, ln.description.value, ln.amount.value) for ln in src.lines] == [
        (1, "Freight", "5.00"), (2, "Laptop", "110.00"), (3, "Dock", "50.00")]


@pytest.mark.parametrize("kwargs", [
    {"header": {"no_such_field": "x"}}, {"lines": {"9": {"amount": "1"}}}, {"lines": {"1": {"bogus": "1"}}},
    {"remove_lines": [9]}, {"add_lines": [{"bogus": "1"}]}])
def test_unknown_fields_and_lines_are_rejected(kwargs):
    with pytest.raises(DecisionError):
        apply_source_corrections(_source(), "clerk", **kwargs)


def test_the_word_null_is_not_a_value():
    assert _clean("null") is None and _clean(" NULL ") is None and _clean("Nullmeier GmbH") == "Nullmeier GmbH"
