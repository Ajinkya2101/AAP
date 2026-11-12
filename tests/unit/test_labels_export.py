"""Training labels for adapter A (IMPLEMENTATION-PLAN-CLIENT-DATA P6.1 / P6.2): the reading compared with
the keyed record, the gold set's split in the export, and the model's evidence boxes kept on the fields
the target keeps. Made-up values."""

import json

from aap.clientdata.labels import LabelResult, compare_invoice, markdown
from aap.finetune.export import gold_splits, kept_evidence


def _inv(**kw):
    base = {"invoice_num": "NWP-2401", "invoice_date": "2025-01-23", "currency_code": "USD",
            "invoice_amount": "385.90", "tax_amount": None}
    base.update(kw)
    inv = {k: {"value": v} for k, v in base.items()}
    inv["lines"] = [{"line_type": "ITEM", "amount": {"value": "187.45"}},
                    {"line_type": "ITEM", "amount": {"value": "198.45"}}]
    return inv


TRUTH = {"invoice_id": 7000102, "split": "train",
         "source": {"invoice_number": "NWP-2401", "total": "385.90"},
         "expected": {"invoice_num": "NWP-2401", "invoice_date": "2025-01-23", "currency_code": "USD",
                      "invoice_amount": "385.90",
                      "lines": [{"line_type": "ITEM", "amount": "198.45"}, {"line_type": "ITEM", "amount": "187.45"},
                                {"line_type": "TAX", "amount": "0"}]}}


def test_a_reading_equal_to_the_keyed_record_has_no_differences():
    diffs, lines = compare_invoice(_inv(), TRUTH)
    assert diffs == [] and lines is True                 # line order does not matter, tax lines are left out


def test_differences_name_the_field_and_both_values():
    diffs, lines = compare_invoice(_inv(invoice_num="NWP-24O1", invoice_amount="358.90"), TRUTH)
    assert [d["field"] for d in diffs] == ["invoice_number", "total"]
    assert diffs[1] == {"field": "total", "model": "358.90", "keyed": "385.90"}
    assert lines is True


def test_lines_that_do_not_match_are_reported():
    inv = _inv()
    inv["lines"] = inv["lines"][:1]
    assert compare_invoice(inv, TRUTH)[1] is False


def test_an_invoice_not_read_yet():
    diffs, lines = compare_invoice(None, TRUTH)
    assert diffs[0]["field"] == "reading" and lines is None


def test_review_list_puts_reviews_first():
    rows = [LabelResult("d1", 1, "train", "accepted"),
            LabelResult("d2", 2, "train", "review", [{"field": "total", "model": "1", "keyed": "2"}], ["vendor_name"], True)]
    text = markdown(rows, "gold")
    assert "1 accepted, 1 to review" in text and text.index("| 2 |") < text.index("| 1 |")


def test_evidence_is_kept_only_where_the_target_keeps_the_value():
    raw = [{"chunk": 0, "output": {"invoice_number": "A-1", "total": "10.00",
                                   "evidence": [{"field": "invoice_number", "page": 1, "bbox": [1, 2, 3, 4]},
                                                {"field": "total", "page": 1, "bbox": [5, 6, 7, 8]}]}}]
    model = {"invoice_number": "A-1", "total": "10.00"}
    target = {"invoice_number": "A-1", "total": "100.00"}              # the total was corrected
    assert kept_evidence(raw, model, target) == [{"field": "invoice_number", "page": 1, "bbox": [1, 2, 3, 4]}]
    assert kept_evidence([{"chunk": 0, "output": {"total": "1"}}], model, target) is None   # not image-only


def test_gold_splits_by_file_hash(tmp_path):
    (tmp_path / "truth").mkdir()
    (tmp_path / "truth" / "abc.truth.json").write_text(json.dumps({"split": "dev", "invoice_id": 7}), encoding="utf-8")
    assert gold_splits(tmp_path) == {"abc": {"split": "dev", "invoice_id": 7}}
    assert gold_splits(None) == {}
