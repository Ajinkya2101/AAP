from aap.eval.metrics import DocResult, FieldResult, field_results, line_match, summarize, validation_results

TRUTH = {
    "scanned": False,
    "source": {"vendor_name": "Northwind IT Supplies Inc.", "invoice_number": "NW-INV-20457", "total": "$14,904.00",
               "tax_amount": "$1,104.00", "subtotal": "$13,800.00", "po_number": "4500123", "bank_account": None,
               "lines": [{"description": "Dell Latitude 5440", "quantity": "10", "unit_price": "$1,150.00",
                          "amount": "$11,500.00"},
                         {"description": "Dell Dock WD19S", "quantity": "10", "unit_price": "180", "amount": "1800"}]},
    "expected": {"vendor_id": 30418, "vendor_site_id": 7276, "invoice_date": "2026-09-12", "invoice_amount": "14904.00",
                 "tax_amount": "1104.00", "currency_code": "USD", "po_number": "4500123", "expect_fail": ["C10"]},
}


def test_extract_fields_ignore_formatting_and_catch_invented_values():
    src = {"vendor_name": "NORTHWIND IT SUPPLIES INC", "invoice_number": "NW INV 20457", "total": "14904.00",
           "tax_amount": "1,104.00", "subtotal": "13800", "po_number": "PO 4500123", "bank_account": "12345678"}
    res = {f.path: f.correct for f in field_results(TRUTH, src, None)}
    assert res["extract.vendor_name"] and res["extract.invoice_number"] and res["extract.total"]
    assert res["extract.po_number"] is False          # "PO4500123" != "4500123": the model kept the label
    assert res["extract.bank_account"] is False       # truth has none, the model invented one


def test_header_fields_from_canonical():
    canon = {"vendor_id": {"value": 30418}, "vendor_site_id": {"value": 7276}, "invoice_date": {"value": "2026-09-12"},
             "invoice_amount": {"value": "14904.0"}, "tax_amount": {"value": "1104"}, "currency_code": {"value": "USD"},
             "po_number": {"value": "4500123"}, "invoice_num": {"value": "NW-INV-20457"}, "subtotal": {"value": None}}
    res = {f.path: f.correct for f in field_results(TRUTH, {}, canon)}
    assert all(res[f"header.{k}"] for k in ("vendor_id", "vendor_site_id", "invoice_date", "invoice_amount",
                                            "tax_amount", "currency_code", "po_number", "invoice_num"))


def test_line_match_needs_numbers_and_similar_text():
    pred = [{"description": "DELL DOCK WD19S USB-C", "quantity": "10", "unit_price": "180.00", "amount": "1,800.00"},
            {"description": "Dell Latitude 5440 laptop", "quantity": "10", "unit_price": "1150", "amount": "11600"}]
    assert line_match(TRUTH["source"]["lines"], pred) == 1     # second line has the wrong amount


def test_validation_and_summary_gates():
    v = validation_results(TRUTH, [{"rule_id": "C10", "result": "fail"}, {"rule_id": "C9", "result": "warning"}])
    assert v[0].correct and v[1].correct
    d = DocResult("a", "digital", "VALIDATED", fields=[FieldResult("header.vendor_id", "1", "1", True)],
                  line_tp=2, line_pred=2, line_truth=2)
    m = summarize([d])
    assert m["all"]["line_f1"] == 1.0 and m["gates"]["line_f1"]["pass"]


def test_implied_quantity_one_matches_but_is_counted_as_invented():
    from aap.eval.metrics import invented_line_values

    truth = [{"description": "Jira Annual Subscription", "quantity": None, "unit_price": None, "amount": "$194.30"}]
    pred = [{"description": "Jira Annual Subscription", "quantity": None, "unit_price": "$194.30", "amount": "$194.30"}]
    assert line_match(truth, pred) == 1
    assert invented_line_values(truth, pred) == 1
