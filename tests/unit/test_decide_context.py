"""Context pack (aap.decide.context, PLAN-MODEL-JSON §4) on the made-up fixture deliveries: the "as of"
rule, site vs supplier history, reversals, distribution sets, the fallback and the store table."""

from datetime import date, timedelta

import pytest

from aap.clientdata.fixtures import DISTRIBUTION_SET, SET_SUPPLIER, account, build, build_list_format
from aap.clientdata.readers import day, num, read_delivery
from aap.common.coa import CoaLayout
from aap.decide.context import CONTEXT_PACK_VERSION, RECENT_INVOICES, build_context_pack, leaks
from aap.decide.history import from_delivery, store_key

KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)


@pytest.fixture(scope="module")
def delivery(tmp_path_factory):
    return read_delivery(build(tmp_path_factory.mktemp("kk") / "delivery"))


@pytest.fixture(scope="module")
def store(delivery):
    return from_delivery(delivery, KK)


def _invoices(delivery, vendor_id):
    return sorted((x for x in delivery.rows("AP_INVOICES_ALL") if num(x["VENDOR_ID"]) == vendor_id),
                  key=lambda x: num(x["INVOICE_ID"]))


def test_store_reads_suppliers_sites_and_history(store):
    assert set(store.suppliers) == {5001, 5002, 5003}
    assert store.sites[6001].terms == "30 NET 2%10" and store.sites[6001].payment_method == "EFT"
    assert store.sites[6001].liability_account == "10.20.00000.21100.00000.000.00.000"
    assert store.sites[6001].operating_unit == "Example Donuts US OU"
    assert "STANDARD" in store.lists["invoice_types"] and "DEBIT" not in store.lists["invoice_types"]
    assert store.lists["terms"] and store.lists["payment_methods"] == ["CHECK", "EFT", "WIRE"]
    assert store.account_descriptions[account(0, "packaging")] == "Store S0110 / Packaging supplies"
    assert all(ln.account != account(2, "packaging") for ln in store.lines)   # both rows of the reversal pair


def test_cancelled_invoices_are_not_history(delivery, store):
    cancelled = {num(x["INVOICE_ID"]) for x in delivery.rows("AP_INVOICES_ALL") if x.get("CANCELLED_DATE")}
    assert cancelled and not cancelled & {ln.invoice_id for ln in store.lines}


def test_as_of_rule_keeps_only_earlier_history(delivery, store):
    invs = _invoices(delivery, 5001)
    target = invs[3]
    as_of = day(target["GL_DATE"])
    pack = build_context_pack(store, vendor_id=5001, vendor_site_id=6001, as_of=as_of,
                              exclude_invoice_ids={num(target["INVOICE_ID"])})
    assert pack["context_pack_version"] == CONTEXT_PACK_VERSION and pack["history_scope"] == "site"
    assert leaks(pack, as_of=as_of, invoice_num=str(target["INVOICE_NUM"])) == []
    nums = [r["invoice_num"] for r in pack["recent_invoices"]]
    assert nums == [str(x["INVOICE_NUM"]) for x in reversed(invs[:3])]     # newest first, only the 3 before
    assert {a["account"] for a in pack["accounts"]} == {account(0, "packaging"), account(1, "packaging")}
    assert abs(sum(a["share"] for a in pack["accounts"]) - 1) < 0.01
    assert pack["accounts"][0]["typical_text"]                               # line texts come with the account
    assert pack["site"]["terms"] == "30 NET 2%10" and pack["site"]["vendor_number"] == "70001"


def test_the_invoice_itself_is_never_in_its_pack(delivery, store):
    target = _invoices(delivery, 5001)[2]
    as_of = day(target["GL_DATE"]) + timedelta(days=30)                       # even with a late "as of"
    pack = build_context_pack(store, vendor_id=5001, vendor_site_id=6001, as_of=as_of,
                              exclude_invoice_ids={num(target["INVOICE_ID"])})
    assert str(target["INVOICE_NUM"]) not in [r["invoice_num"] for r in pack["recent_invoices"]]
    assert leaks(pack, as_of=as_of, invoice_num=str(target["INVOICE_NUM"])) == []


def test_leaks_finds_a_late_date_and_the_invoice_itself(delivery, store):
    invs = _invoices(delivery, 5001)
    late = day(invs[-1]["GL_DATE"]) + timedelta(days=1)
    pack = build_context_pack(store, vendor_id=5001, vendor_site_id=6001, as_of=late)
    found = leaks(pack, as_of=day(invs[1]["GL_DATE"]), invoice_num=str(invs[-1]["INVOICE_NUM"]))
    assert any("dated" in p for p in found) and any("itself" in p for p in found)


def test_recent_invoices_are_limited(delivery, store):
    late = date(2030, 1, 1)
    pack = build_context_pack(store, vendor_id=5001, vendor_site_id=6001, as_of=late)
    assert len(pack["recent_invoices"]) == min(RECENT_INVOICES, len(_invoices(delivery, 5001)))
    line = pack["recent_invoices"][0]["lines"][0]
    assert line["accounts"] and line["line_type"] == "ITEM"


def test_no_history_falls_back_and_says_so(store):
    pack = build_context_pack(store, vendor_id=5002, vendor_site_id=6002, as_of=date(2025, 1, 1))
    assert pack["history_scope"] == "none" and pack["fallback"] is True
    assert pack["recent_invoices"] == []
    later = build_context_pack(store, vendor_id=5002, vendor_site_id=6002, as_of=date(2025, 3, 1))
    assert later["fallback"] is False


def test_fallback_uses_other_suppliers_before_the_date(store):
    pack = build_context_pack(store, vendor_id=9999, vendor_site_id=None, as_of=date(2025, 2, 1))
    assert pack["fallback"] and pack["accounts"]
    assert all(a["fallback"] for a in pack["accounts"])
    early = build_context_pack(store, vendor_id=9999, vendor_site_id=None, as_of=date(2024, 1, 1))
    assert early["accounts"] == []


def test_store_table_rows_match_the_printed_store(store):
    pack = build_context_pack(store, vendor_id=5001, vendor_site_id=6001, as_of=date(2025, 3, 1),
                              printed_store="Store #0316")
    assert pack["stores"] == [{"store": "S0316", "cost_center": "00316"}]
    assert build_context_pack(store, vendor_id=5001, vendor_site_id=6001, as_of=date(2025, 3, 1))["stores"] == []
    assert store_key("STORE 0316") == store_key("S0316") == "316"


def test_distribution_set_lines_are_shown_as_a_set(tmp_path):
    d = read_delivery(build_list_format(tmp_path / "list"))
    st = from_delivery(d, KK)
    pack = build_context_pack(st, vendor_id=SET_SUPPLIER, vendor_site_id=6002, as_of=date(2030, 1, 1))
    assert pack["accounts"] == []                    # every line went through the set
    (ds,) = pack["distribution_sets"]
    assert ds["name"] == f"set {DISTRIBUTION_SET}" and ds["share"] == 1.0
    assert {a["account"] for a in ds["keyed_accounts"]} == {account(0, "cleaning"), account(2, "repairs")}


def test_placeholder_accounts_without_a_chart(tmp_path):
    d = read_delivery(build_list_format(tmp_path / "noaccounts", accounts=False))
    st = from_delivery(d, KK)
    pack = build_context_pack(st, vendor_id=5001, vendor_site_id=6001, as_of=date(2030, 1, 1))
    assert pack["accounts"] and all(a["account"].startswith("X.X.X.") for a in pack["accounts"])
