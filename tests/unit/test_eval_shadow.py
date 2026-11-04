"""Shadow comparison, GL metrics, keyed line types and the per-supplier report (eval/shadow.py, eval/gl.py).
Made-up values in the client gold-set format (clientdata/goldset.py)."""

from decimal import Decimal

from aap.common.coa import CoaLayout
from aap.eval.gl import gl_result, summarize_gl
from aap.eval.metrics import DocResult, field_results, summarize
from aap.eval.report import render
from aap.eval.shadow import shadow_compare

KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)
A_SUPPLIES = "10.20.00110.61210.00000.000.00.000"
A_REPAIRS = "10.20.00110.65300.00000.000.00.000"
A_SUPPLIES_OTHER_STORE = "10.20.00220.61210.00000.000.00.000"


def keyed(**over):
    exp = {"vendor_id": 5001, "vendor_site_id": 6001, "org_id": 81, "currency_code": "USD", "invoice_num": "NWP-1",
           "invoice_date": "2026-10-01", "invoice_amount": "1300.00", "terms_id": 102, "invoice_type": "STANDARD",
           "payment_method": "EFT", "gl_date": "2026-10-03", "po_matched": False,
           "lines": [{"line_number": 1, "line_type": "ITEM", "amount": "1000.00", "description": "Boxes",
                      "distributions": [{"amount": "1000.00", "account": A_SUPPLIES}]},
                     {"line_number": 2, "line_type": "ITEM", "amount": "200.00", "description": "Oven repair",
                      "distributions": [{"amount": "200.00", "account": A_REPAIRS}]},
                     {"line_number": 3, "line_type": "TAX", "amount": "100.00", "description": "Tax",
                      "distributions": [{"amount": "100.00", "account": "10.20.00110.21500.00000.000.00.000"}]}]}
    exp.update(over)
    return {"vendor_number": "70001", "split": "test", "supplier_holdout": False, "expected": exp,
            "source": {"invoice_number": "NWP-1", "total": "1300.00"}}


def oracle(*, accounts=(A_SUPPLIES, A_REPAIRS), state="ready", review_line=None, **header):
    h = {"invoice_num": "NWP-1", "invoice_date": "2026-10-01", "invoice_amount": "1300.00",
         "invoice_currency_code": "USD", "invoice_type_lookup_code": "STANDARD", "payment_method_code": "EFT",
         "gl_date": "2026-10-04"}
    h.update(header)
    issues = [{"field": f"lines[{review_line}].dist_code_concatenated", "code": "gl_review"}] if review_line else []
    return {"status": {"state": state, "issues": issues}, "header": h,
            "source_ids": {"vendor_id": 5001, "vendor_site_id": 6001, "org_id": 81, "terms_id": 102},
            "lines": [{"line_number": 1, "line_type_lookup_code": "ITEM", "amount": "1000.00",
                       "dist_code_concatenated": accounts[0], "account_source": "line"},
                      {"line_number": 2, "line_type_lookup_code": "ITEM", "amount": "200.00",
                       "dist_code_concatenated": accounts[1], "account_source": "line"},
                      {"line_number": 3, "line_type_lookup_code": "TAX", "amount": "100.00",
                       "dist_code_concatenated": None, "account_source": "oracle_setup"}]}


def test_equal_json_is_equal_and_gl_date_does_not_count():
    r = shadow_compare(keyed(), oracle(), KK)
    assert r.equal and r.ready
    gl_date = next(f for f in r.fields if f.name == "gl_date")
    assert not gl_date.correct and not gl_date.core           # policy, not part of "equal"
    assert r.gl.full_equal and r.gl.keyed_amount == Decimal("1200.00")   # tax line is not a GL line


def test_wrong_supplier_or_line_types_make_it_different():
    r = shadow_compare(keyed(), oracle(), KK)
    assert r.equal
    j = oracle()
    j["source_ids"]["vendor_id"] = 5002
    assert not shadow_compare(keyed(), j, KK).equal
    j = oracle()
    j["lines"][2]["line_type_lookup_code"] = "FREIGHT"
    r = shadow_compare(keyed(), j, KK)
    assert {f.name for f in r.fields if not f.correct} >= {"line_types", "amount_by_line_type"}


def test_unknown_keyed_value_is_not_scored():
    r = shadow_compare(keyed(payment_method=None), oracle(payment_method_code="CHECK"), KK)
    assert "payment_method" not in {f.name for f in r.fields} and r.equal


def test_gl_amount_weighted_natural_vs_full():
    # line 1 coded to the right natural account but the wrong store: natural right, full wrong
    g = gl_result(keyed()["expected"], oracle(accounts=(A_SUPPLIES_OTHER_STORE, A_REPAIRS)), KK)
    assert g.full_amount_ok == Decimal("200.00") and g.natural_amount_ok == Decimal("1200.00")
    s = summarize_gl([g], KK)
    assert s["amount_accuracy_full"] == round(200 / 1200, 4) and s["amount_accuracy_natural"] == 1.0
    assert s["no_review_accuracy_full"] == 0.5 and s["no_review_accuracy_natural"] == 1.0


def test_gl_review_lines_are_not_counted_as_coded_without_review():
    g = gl_result(keyed()["expected"], oracle(accounts=(A_REPAIRS, A_REPAIRS), review_line=1), KK)
    auto = [ln for ln in g.lines if ln.auto]
    assert [ln.line_number for ln in auto] == [2] and auto[0].predicted == auto[0].expected
    s = summarize_gl([g], KK)
    assert s["no_review_share"] == 0.5 and s["no_review_accuracy_full"] == 1.0


def test_one_keyed_line_for_a_multi_line_invoice():
    # AP keyed one summary line; the JSON has two lines on the same account: right at invoice level
    exp = keyed(lines=[{"line_number": 1, "line_type": "ITEM", "amount": "1200.00",
                        "distributions": [{"amount": "1200.00", "account": A_SUPPLIES}]}])["expected"]
    g = gl_result(exp, oracle(accounts=(A_SUPPLIES, A_SUPPLIES)), KK)
    assert g.full_equal and all(ln.expected == A_SUPPLIES for ln in g.lines)


def test_po_matched_and_synthetic_truths_are_not_gl_scored():
    assert gl_result(keyed(po_matched=True)["expected"], oracle(), KK) is None
    assert gl_result({"vendor_id": 1}, oracle(), KK) is None


def test_keyed_line_types_on_the_mapped_invoice():
    canon = {"lines": [{"line_type": "ITEM"}, {"line_type": "ITEM"}, {"line_type": "TAX"}]}
    res = {f.path: f for f in field_results(keyed(), {}, canon)}
    assert res["lines.line_types"].correct and res["lines.line_types"].expected == "ITEMx2,TAXx1"
    canon["lines"][2]["line_type"] = "FREIGHT"
    assert not {f.path: f for f in field_results(keyed(), {}, canon)}["lines.line_types"].correct


def _doc(name, supplier, shadow, holdout=False):
    return DocResult(name, "digital", "IN_REVIEW", supplier=supplier, split="test", holdout=holdout,
                     shadow=shadow, line_tp=1, line_pred=1, line_truth=1)


def test_summary_per_supplier_holdout_ready_but_different_and_gl_gate():
    good = shadow_compare(keyed(), oracle(), KK)
    wrong = shadow_compare(keyed(), oracle(accounts=(A_REPAIRS, A_REPAIRS)), KK)     # ready, line 1 miscoded
    review = shadow_compare(keyed(), oracle(state="needs_review"), KK)
    m = summarize([_doc("a.pdf", "70001", good), _doc("b.pdf", "70001", wrong), _doc("c.pdf", "70009", review, True)],
                  KK)
    sh = m["all"]["shadow"]
    assert sh["invoices"] == 3 and sh["ready_but_different"] == ["b.pdf"] and sh["states"]["needs_review"] == 1
    assert set(m["by_supplier"]) == {"70001", "70009"} and m["by_supplier"]["70001"]["documents"] == 2
    assert m["holdout"]["suppliers"] == ["70009"] and m["holdout"]["documents"] == 1
    gate = m["gates"]["gl_no_review_accuracy"]
    assert gate["threshold"] == 0.98 and not gate["pass"]                  # b.pdf line 1 is wrong without review
    text = render("00000000-0000-0000-0000-000000000000", "t", m)
    assert "READY BUT DIFFERENT" in text and "70009 *" in text and "Per supplier" in text


def test_no_gl_gate_without_gl_results():
    m = summarize([DocResult("a", "digital", "VALIDATED", line_tp=1, line_pred=1, line_truth=1)], KK)
    assert "gl_no_review_accuracy" not in m["gates"] and m["all"]["gl"] is None and "by_supplier" not in m
