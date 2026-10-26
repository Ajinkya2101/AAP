import json

from aap.config import Settings
from aap.llm import LlmRequest
from aap.llm.stub import StubClient
from aap.schemas.source import ExtractedInvoice, ExtractedInvoiceGrounded


def test_stub_extract_reads_truth_sidecar(tmp_path):
    source = {k: None for k in ExtractedInvoice.model_fields if k not in ("lines", "taxes")}
    source.update(invoice_number="X-1", total="$10.00")
    source["lines"] = [{"description": "a", "item_code": None, "quantity": "1", "uom": None, "unit_price": "$10.00",
                        "amount": "$10.00", "tax_rate": None, "po_line_ref": None, "_page": 1}]
    (tmp_path / "abc.truth.json").write_text(json.dumps({"source": source}))
    stub = StubClient(Settings(truth_dir=tmp_path))
    resp = stub.ask(LlmRequest(task="extract", system="", content=[], schema=ExtractedInvoice.model_json_schema(),
                               context={"sha256": "abc"}))
    parsed = ExtractedInvoice.model_validate(resp.data)
    assert parsed.invoice_number == "X-1" and parsed.lines[0].amount == "$10.00"
    grounded = stub.ask(LlmRequest(task="extract", system="", content=[],
                                   schema=ExtractedInvoiceGrounded.model_json_schema(), context={"sha256": "abc"}))
    assert ExtractedInvoiceGrounded.model_validate(grounded.data).evidence == []


def test_stub_extract_falls_back_to_regex(tmp_path):
    stub = StubClient(Settings(truth_dir=tmp_path))
    text = "ACME LTD\nInvoice No: A-77\nDate: 12-Sep-2026\nPO Number: 4500123\nTotal: $1,234.50"
    resp = stub.ask(LlmRequest(task="extract", system="", content=[], schema=ExtractedInvoice.model_json_schema(),
                               context={"sha256": "missing", "page_texts": [text]}))
    assert resp.data["invoice_number"] == "A-77"
    assert resp.data["po_number"] == "4500123"
    assert resp.data["total"] == "$1,234.50"


def test_stub_choose_picks_first_real_candidate():
    stub = StubClient(Settings())
    schema = {"type": "object", "properties": {"choice": {"enum": ["none", "17", "42"]}, "reason": {"type": "string"}},
              "required": ["choice", "reason"]}
    assert stub.ask(LlmRequest(task="choose", system="", content=[], schema=schema)).data["choice"] == "17"
