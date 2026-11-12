"""Adapter B's answer schema, request and training target (aap.decide.schema / request, PLAN-MODEL-JSON
E3), the stub's answer, and how the decide node compares an answer with the code's values. Made-up
fixture data only."""

from datetime import date
from types import SimpleNamespace

import pytest

from aap.clientdata.fixtures import account, build, build_list_format
from aap.clientdata.readers import day, num, read_delivery
from aap.common.coa import CoaLayout
from aap.decide.context import build_context_pack
from aap.decide.history import from_delivery
from aap.decide.request import (
    PROMPT_VERSION,
    SYSTEM,
    decide_request,
    printed_from_keyed,
    target_from_keyed,
    user_text,
)
from aap.decide.schema import BASIS, account_candidates, check_answer, decide_schema

KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)


@pytest.fixture(scope="module")
def delivery(tmp_path_factory):
    return read_delivery(build(tmp_path_factory.mktemp("kk") / "delivery"))


@pytest.fixture(scope="module")
def store(delivery):
    return from_delivery(delivery, KK)


def _keyed(delivery, invoice_id):
    h = next(x for x in delivery.rows("AP_INVOICES_ALL") if num(x["INVOICE_ID"]) == invoice_id)
    lines = [x for x in delivery.rows("AP_INVOICE_LINES_ALL") if num(x["INVOICE_ID"]) == invoice_id]
    dists = [x for x in delivery.rows("AP_INVOICE_DISTRIBUTIONS_ALL") if num(x["INVOICE_ID"]) == invoice_id]
    return h, lines, dists


def _example(delivery, store, invoice_id, printed_store=None):
    h, lines, dists = _keyed(delivery, invoice_id)
    as_of = day(h["GL_DATE"])
    pack = build_context_pack(store, vendor_id=num(h["VENDOR_ID"]), vendor_site_id=num(h["VENDOR_SITE_ID"]),
                              as_of=as_of, exclude_invoice_ids={invoice_id}, printed_store=printed_store)
    printed = printed_from_keyed(h, lines, store.suppliers[num(h["VENDOR_ID"])].vendor_name)
    return h, lines, dists, pack, printed


def test_schema_restricts_every_oracle_value_to_the_pack(delivery, store):
    _, _, _, pack, printed = _example(delivery, store, 7000104)
    schema = decide_schema(pack, line_numbers=[1, 2], coa=KK)
    props = schema["properties"]
    assert props["terms_name"]["enum"][-1] is None and "30 NET 2%10" in props["terms_name"]["enum"]
    assert props["liability_account"]["enum"] == ["10.20.00000.21100.00000.000.00.000", None]
    line = props["lines"]["items"]["properties"]
    assert set(line["account"]["enum"]) == {account(0, "packaging"), account(1, "packaging"), None}
    assert line["basis"]["enum"] == [*BASIS, None] and props["lines"]["minItems"] == 2 == props["lines"]["maxItems"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(props)


def test_a_printed_store_adds_accounts_with_its_cost_center(delivery, store):
    _, _, _, pack, _ = _example(delivery, store, 7000104, printed_store="STORE 451")
    cands = account_candidates(pack, KK)
    assert account(2, "packaging") in cands and account(0, "packaging") in cands


def test_target_from_the_keyed_record_fits_the_schema(delivery, store):
    h, lines, dists, pack, printed = _example(delivery, store, 7000104)
    target = target_from_keyed(h, lines, dists, pack, account_of=lambda c: _account_of(delivery, c),
                               set_name=lambda s: f"set {s}", terms_name="30 NET 2%10",
                               liability=pack["site"]["liability_account"], coa=KK)
    schema = decide_schema(pack, line_numbers=[ln["line_number"] for ln in printed["lines"]], coa=KK)
    assert check_answer(target, schema) == []
    assert target["terms_name"] == "30 NET 2%10" and target["basis"]["terms_name"] == "site_default"
    assert target["invoice_type_lookup_code"] == "STANDARD" and target["basis"]["invoice_type_lookup_code"] == "printed"
    assert [ln["account"] for ln in target["lines"]] == [account(0, "packaging"), account(1, "packaging")]
    assert {ln["basis"] for ln in target["lines"]} == {"history"}


def _account_of(delivery, ccid):
    c = next(x for x in delivery.rows("GL_CODE_COMBINATIONS") if num(x["CODE_COMBINATION_ID"]) == num(ccid))
    return ".".join(str(c[f"SEGMENT{i}"]) for i in range(1, 9))


def test_a_keyed_value_that_is_not_a_candidate_becomes_null(delivery, store):
    h, lines, dists, pack, _ = _example(delivery, store, 7000104)
    target = target_from_keyed(h, lines, dists, pack, account_of=lambda c: "99.99.99999.99999.00000.000.00.000",
                               set_name=lambda s: f"set {s}", terms_name="NOT A TERM", liability=None, coa=KK)
    assert target["terms_name"] is None and target["basis"]["terms_name"] is None
    assert all(ln["account"] is None and ln["basis"] is None for ln in target["lines"])


def test_keyed_printed_values_hold_nothing_ap_decided(delivery):
    h, lines, _ = _keyed(delivery, 7000104)
    printed = printed_from_keyed(h, lines, "Northwind Packaging LLC")
    assert printed["printed_from"] == "keyed" and printed["terms_printed"] is None
    assert [ln["line_number"] for ln in printed["lines"]] == [1, 2]
    assert "TERMS_ID" not in str(printed) and "account" not in str(printed).lower()


def test_request_carries_system_text_and_schema(delivery, store):
    _, _, _, pack, printed = _example(delivery, store, 7000104)
    req = decide_request(printed, pack, coa=KK, model="m")
    assert req.task == "decide" and req.system == SYSTEM and req.model == "m" and PROMPT_VERSION == "decide/1"
    assert req.content == [{"type": "text", "text": user_text(printed, pack)}]
    assert req.schema["properties"]["lines"]["minItems"] == 2


def test_stub_answers_from_the_pack(delivery, store):
    from aap.config import get_settings
    from aap.llm.stub import StubClient

    _, _, _, pack, printed = _example(delivery, store, 7000104)
    req = decide_request(printed, pack, coa=KK)
    answer = StubClient(get_settings()).ask(req).data
    assert check_answer(answer, req.schema) == []
    assert answer["terms_name"] == pack["site"]["terms"] and answer["basis"]["terms_name"] == "site_default"
    assert answer["lines"][0]["account"] == pack["accounts"][0]["account"]


def test_stub_uses_a_distribution_set_when_there_is_no_plain_account(tmp_path):
    d = read_delivery(build_list_format(tmp_path / "list"))
    st = from_delivery(d, KK)
    h = next(x for x in d.rows("AP_INVOICES_ALL") if num(x["VENDOR_ID"]) == 5002 and num(x["INVOICE_ID"]) == 7000109)
    pack = build_context_pack(st, vendor_id=5002, vendor_site_id=6002, as_of=date(2030, 1, 1),
                              exclude_invoice_ids={7000109})
    lines = [x for x in d.rows("AP_INVOICE_LINES_ALL") if num(x["INVOICE_ID"]) == 7000109]
    from aap.config import get_settings
    from aap.llm.stub import StubClient

    req = decide_request(printed_from_keyed(h, lines, "Bluebird"), pack, coa=KK)
    answer = StubClient(get_settings()).ask(req).data
    assert answer["lines"][0]["account"] is None and answer["lines"][0]["distribution_set"] == "set 8801"
    assert answer["lines"][0]["basis"] == "distribution_set"


# ----------------------------------------------------------- node helpers
def _f(v):
    return SimpleNamespace(value=v, source_value=None)


def _inv(terms="30 NET", lines=(1, 2)):
    item = [SimpleNamespace(line_number=n) for n in lines]
    return SimpleNamespace(invoice_type=_f("STANDARD"), terms_name=_f(terms), payment_method=_f("EFT"),
                           pay_group=_f("STANDARD"), item_lines=lambda: item)


def test_compare_lists_header_differences_and_line_agreement():
    from aap.agents.nodes.decide import compare

    pack = {"site": {"liability_account": "L"}, "distribution_sets": []}
    answer = {"invoice_type_lookup_code": "STANDARD", "terms_name": "45 NET", "payment_method_code": "EFT",
              "pay_group_lookup_code": "STANDARD", "liability_account": "L",
              "lines": [{"line_number": 1, "account": "A", "distribution_set": None, "basis": "history"},
                        {"line_number": 2, "account": "B", "distribution_set": None, "basis": "chosen"},
                        {"line_number": 3, "account": "F", "distribution_set": None, "basis": "chosen"}]}
    header, lines = compare(answer, _inv(), pack, [{"line": 1, "account": "A"}, {"line": 2, "account": "C"}])
    assert header == [{"field": "terms_name", "model": "45 NET", "code": "30 NET"}]
    assert [(x["line"], x["agrees"]) for x in lines] == [(1, True), (2, False)]    # line 3 is not an item line


def test_set_account_resolves_one_account_sets_only():
    from aap.agents.nodes.decide import set_account

    pack = {"distribution_sets": [
        {"name": "ONE", "lines": [{"account": "A", "percent": "100"}], "keyed_accounts": []},
        {"name": "SPLIT", "lines": [], "keyed_accounts": [{"account": "A", "share": 0.6}, {"account": "B", "share": 0.4}]}]}
    assert set_account(pack, "ONE") == ("A", True)
    assert set_account(pack, "SPLIT") == ("A", False)
    assert set_account(pack, "MISSING") == (None, False)
