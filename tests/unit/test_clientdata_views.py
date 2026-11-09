"""Deliveries in the layout of the 2026-11-09 extract (aap.clientdata.views), placeholder accounts in the
replica, and the history report (aap.clientdata.history). Made-up fixture data only."""

from datetime import date
from decimal import Decimal

import pytest

from aap.clientdata.check import check_delivery
from aap.clientdata.fixtures import DISTRIBUTION_SET, ORG, SET_SUPPLIER, build_list_format
from aap.clientdata.history import default_cutoff, defaults_vs_keyed, gl_from_history, history_shape, markdown
from aap.clientdata.readers import num, read_delivery
from aap.common.coa import CoaLayout
from aap.ebs_mock.client_load import build_replica, placeholder_account, unmatched_invoices

KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)


@pytest.fixture
def kk_layout(monkeypatch):
    from aap.config import get_settings

    for k, v in {"GL_SEGMENT_COUNT": "8", "GL_SEGMENT_SEPARATOR": ".", "GL_COST_CENTER_SEGMENT": "3",
                 "GL_NATURAL_ACCOUNT_SEGMENT": "4"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def listed(tmp_path):
    return build_list_format(tmp_path / "2026-11-09")


def _derived(d, word):
    return [m for _, m, _ in d.derived if word in m]


def test_sheets_are_matched_by_name(listed):
    d = read_delivery(listed)
    assert {"AP_INVOICES_ALL", "AP_INVOICE_LINES_ALL", "AP_INVOICE_DISTRIBUTIONS_ALL", "AP_SUPPLIERS",
            "AP_SUPPLIER_SITES_ALL"} <= set(d.tables)
    assert "SUPPLIER_SITE_VIEW" not in d.tables            # split, not kept as a table
    assert not d.unknown_files                             # the SQL sheets are skipped


def test_exact_duplicate_header_row_is_dropped(listed):
    d = read_delivery(listed)
    ids = [num(x["INVOICE_ID"]) for x in d.rows("AP_INVOICES_ALL")]
    assert len(ids) == len(set(ids)) == 14
    assert _derived(d, "exact duplicate")


def test_supplier_view_is_split(listed):
    d = read_delivery(listed)
    suppliers = {str(s["SEGMENT1"]): s for s in d.rows("AP_SUPPLIERS")}
    assert set(suppliers) == {"70001", "70002", "70003"}   # 70009: no VENDOR_ID anywhere -> left out
    assert num(suppliers["70001"]["VENDOR_ID"]) == 5001    # from the invoices of its site
    sites = {num(s["VENDOR_SITE_ID"]): s for s in d.rows("AP_SUPPLIER_SITES_ALL")}
    assert set(sites) == {6001, 6002, 6003}
    # two rows per site (primary method + CHECK): the payee's default wins
    assert sites[6001]["PAYMENT_METHOD_LOOKUP_CODE"] == "EFT"
    assert sites[6003]["PAYMENT_METHOD_LOOKUP_CODE"] == "WIRE"
    assert num(sites[6001]["VENDOR_ID"]) == 5001 and num(sites[6001]["ORG_ID"]) == ORG
    assert num(suppliers["70001"]["TERMS_ID"]) == 102     # "Supplier Terms" is the supplier's terms
    warnings = [m for lvl, m, items in d.derived if lvl == "warning" and "70009" in items]
    assert warnings


def test_header_org_comes_from_the_lines(listed):
    d = read_delivery(listed)
    assert {num(x["ORG_ID"]) for x in d.rows("AP_INVOICES_ALL")} == {ORG}
    assert _derived(d, "ORG_ID not in AP_INVOICES_ALL")


def test_list_format_passes_the_checker(listed, kk_layout):
    report, _ = check_delivery(listed, pdf_scan=False)
    assert report.passed, [f.message for f in report.errors]
    infos = {f.message for f in report.findings if f.check == "derived"}
    assert any("split into 3 supplier(s) and 3 site(s)" in m for m in infos)
    sets = [f for f in report.findings if "distribution sets used" in f.message]
    assert sets and sets[0].examples == [str(DISTRIBUTION_SET)]


def test_without_accounts_the_checker_warns_and_the_replica_uses_placeholders(tmp_path, kk_layout):
    root = build_list_format(tmp_path / "d", accounts=False)
    report, d = check_delivery(root, pdf_scan=False)
    assert report.passed
    assert any("GL_CODE_COMBINATIONS not delivered" in f.message for f in report.warnings)
    r = build_replica(d, KK)
    combos = {c["code_combination_id"]: c for c in r.rows["gl.gl_code_combinations"]}
    used = {num(x["DIST_CODE_COMBINATION_ID"]) for x in d.rows("AP_INVOICE_DISTRIBUTIONS_ALL")}
    assert used <= set(combos)
    some = next(iter(used))
    assert [combos[some][f"segment{i}"] for i in range(1, 9)] == placeholder_account(some, KK)
    assert combos[some]["segment4"] == str(some) and combos[some]["segment1"] == "X"
    assert any("placeholder" in n for n in r.notes)


def test_placeholders_never_replace_delivered_accounts(listed, kk_layout):
    d = read_delivery(listed)
    r = build_replica(d, KK)
    assert not any("placeholder" in n for n in r.notes)
    assert all(c["segment1"] != "X" for c in r.rows["gl.gl_code_combinations"])


def test_unmatched_invoices_are_left_out_on_request(listed):
    d = read_delivery(listed)
    d.tables["AP_SUPPLIERS"].rows = [s for s in d.rows("AP_SUPPLIERS") if num(s["VENDOR_ID"]) != 5003]
    gone = unmatched_invoices(d)
    assert gone and all(num(x["VENDOR_ID"]) == 5003 for x in d.rows("AP_INVOICES_ALL")
                        if num(x["INVOICE_ID"]) in gone)
    kept = {x["invoice_id"] for x in build_replica(d, KK, skip_unmatched=True).rows["ap.ap_invoices_all"]}
    assert not kept & gone
    assert gone <= {x["invoice_id"] for x in build_replica(d, KK).rows["ap.ap_invoices_all"]}


# ------------------------------------------------------------------ history
def test_gl_from_history(listed):
    d = read_delivery(listed)
    cutoff = date(2025, 2, 15)
    gl = gl_from_history(d, cutoff, min_share=0.9, min_invoices=2)
    assert gl.history_distributions and gl.test_distributions
    assert gl.test_amount > 0
    # the distribution-set supplier: every line is the same set -> coded without review, always right
    s = next(x for x in gl.suppliers if x.key == str(SET_SUPPLIER))
    assert s.auto and s.accuracy == 1.0 and s.top_share == 1.0
    assert gl.set_amount > 0
    # two accounts per supplier elsewhere: top-1 is not enough, top-3 always is
    assert gl.recall_amount[3] == gl.test_amount - gl.no_history_amount
    assert gl.auto_correct_amount <= gl.auto_amount <= gl.test_amount


def test_default_cutoff_is_the_last_gl_month(listed):
    d = read_delivery(listed)
    last = max(date.fromisoformat(str(x["GL_DATE"])[:10]) for x in d.rows("AP_INVOICES_ALL"))
    assert default_cutoff(d) == last.replace(day=1)


def test_defaults_vs_keyed(listed):
    d = read_delivery(listed)
    res = defaults_vs_keyed(d)
    assert res.fields["terms"]["different"] == 0 and res.fields["terms"]["same"] > 0
    assert res.fields["payment method"]["different"] == 0
    # the fixture keys no pay group on invoices
    assert res.fields["pay group"]["no default"] > 0


def test_history_shape_and_report(listed):
    d = read_delivery(listed)
    shape = history_shape(d)
    assert shape["invoices"] == 13                  # 14 keyed, one cancelled
    assert shape["lines_with_distribution_set"] > 0
    gl = gl_from_history(d, date(2025, 2, 15))
    text = markdown(gl, defaults_vs_keyed(d), shape, "2026-11-09")
    assert "## 1. GL coding from history" in text and "## 2. Site defaults" in text
    assert Decimal(0) <= gl.correct_amount <= gl.test_amount
