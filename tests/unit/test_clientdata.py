"""Client data intake (aap.clientdata): reading deliveries, the checker, term lines from names, rows
for the mock EBS and the gold set. Runs on the made-up fixture delivery; no client data."""

import json
from decimal import Decimal

import openpyxl
import pytest

from aap.clientdata.check import check_delivery, main
from aap.clientdata.fixtures import build
from aap.clientdata.goldset import build_goldset
from aap.clientdata.readers import day, dec, num, read_delivery
from aap.clientdata.terms import derive_term_line, payment_method_rows, term_rows
from aap.common.coa import CoaLayout

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
def delivery(tmp_path):
    return build(tmp_path / "2026-11-10")


def _messages(report, level):
    return {f.message: f for f in report.findings if f.level == level}


# ----------------------------------------------------------------- readers
def test_conversions():
    assert num("70001") == 70001 and num(70001.0) == 70001 and num("") is None and num("1.5") is None
    assert dec("1,234.50") == Decimal("1234.50") and dec("(12.00)") == Decimal("-12.00")
    assert str(day("18-Nov-2025")) == "2025-11-18" and str(day("11012025")) == "2025-11-01"


def test_sql_developer_workbook(tmp_path):
    """pay_terms.xlsx / pay_method.xlsx layout: rows in "Export Worksheet", the query in "SQL"; *_TL rows
    in several languages."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export Worksheet"
    ws.append(["PAYMENT_METHOD_CODE", "LANGUAGE", "PAYMENT_METHOD_NAME"])
    for code in ("CHECK", "EFT"):
        for lang in ("US", "F", "ESA"):
            ws.append([code, lang, f"{code} {lang}"])
    wb.create_sheet("SQL").append(["select * from iby_payment_methods_tl"])
    wb.save(tmp_path / "pay_method.xlsx")
    d = read_delivery(tmp_path)
    assert [r["PAYMENT_METHOD_CODE"] for r in d.rows("IBY_PAYMENT_METHODS")] == ["CHECK", "EFT"]
    assert not d.unknown_files
    rows = payment_method_rows(d)
    assert rows["iby.iby_payment_methods_tl"][0]["payment_method_name"] == "CHECK US"


# ----------------------------------------------------------------- checker
def test_clean_delivery_passes(delivery, kk_layout):
    report, d = check_delivery(delivery)
    assert report.passed and not report.warnings, [f.message for f in report.findings]
    assert report.tables["AP_INVOICES_ALL"] == 14 and report.attachments == 12
    info = _messages(report, "info")
    assert info["cancelled invoices (left out of the checks below and of the history)"].count == 1
    assert info["reversed distributions (pairs net to zero; left out of the GL history)"].count == 2
    azteca = next(s for s in report.suppliers if s.vendor_num == "70003")
    assert azteca.scanned_pdfs == 1 and azteca.with_attachment == 4


def test_broken_delivery_is_reported(tmp_path, kk_layout):
    report, _ = check_delivery(build(tmp_path / "broken", broken=True))
    errors = _messages(report, "error")
    assert errors["attachments without a keyed invoice (AP_INVOICES_ALL)"].examples == ["7999999"]
    assert errors["invoice lines without their invoice"].examples == ["7999998"]
    assert errors["distribution accounts (DIST_CODE_COMBINATION_ID) not in GL_CODE_COMBINATIONS"].examples == \
        ["99999999"]
    assert "invoice amount is not the sum of its lines" in _messages(report, "warning")
    assert not report.passed


def test_wrong_segment_count_is_a_warning(delivery):
    report, _ = check_delivery(delivery, pdf_scan=False)          # settings: the mock's 4 segments
    assert "code combinations whose segment count is not GL_SEGMENT_COUNT=4" in _messages(report, "warning")


def test_missing_columns_and_duplicates(tmp_path):
    (tmp_path / "ap_suppliers.csv").write_text("VENDOR_ID,VENDOR_NAME\n1,A\n1,B\n", encoding="utf-8")
    (tmp_path / "ap_terms.csv").write_text("TERM_ID,NAME\n10,30 NET\n10,30 NET\n", encoding="utf-8")
    report, _ = check_delivery(tmp_path, pdf_scan=False)
    errors = _messages(report, "error")
    assert "AP_SUPPLIERS: required columns missing: SEGMENT1" in errors
    assert errors["AP_TERMS: duplicate TERM_ID"].examples == ["10"]


def test_cli_writes_manifest_and_report(delivery, kk_layout):
    assert main([str(delivery), "--no-pdf"]) == 0
    manifest = json.loads((delivery / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["result"] == "pass" and manifest["tables"]["AP_TERMS"]["rows"] == 5
    assert len(manifest["files"]["ap_invoices_all.csv"]) == 64          # SHA-256
    assert "## Per supplier" in (delivery / "check-report.md").read_text(encoding="utf-8")
    assert main([str(delivery), "--no-pdf"]) == 0                       # manifest / report are not re-read


# -------------------------------------------------------------- term lines
@pytest.mark.parametrize("name,description,expected", [
    ("30 NET", "NET DUE IN 30 DAYS", (30, None, None, None, None)),
    ("45 NET", "NET DUE IN 45 DYAS", (45, None, None, None, None)),
    ("30 NET 1.5%10", "NET DUE IN 30 DAYS 1.5% DISCOUNT IN 10 DAYS", (30, None, None, Decimal("1.5"), 10)),
    ("30 NET .75%15", "NET DUE IN 30 DAYS .75% DISCOUNT IN 15 DAYS", (30, None, None, Decimal("0.75"), 15)),
    ("30 NET 2% 14", "NET DUE IN 30 DAYS 2% DISCOUNT", (30, None, None, Decimal("2"), 14)),
    ("15 EOM", "NET DUE ON THE 15TH AFTER MONTH END", (None, 15, 1, None, None)),
    ("TAX TERM", "NET DUE ON THE 21ST AFTER CALENDAR MONTH END", (None, 21, 1, None, None)),
    ("6 Months", "6 Months 25th of Month", (None, 25, 6, None, None)),
    ("30 DIAS", "30 DIAS", (30, None, None, None, None)),
    ("CONTADO", "CONTADO", (0, None, None, None, None)),
    ("Immediate", "Scheduled for payment immediately", (0, None, None, None, None)),
])
def test_term_line_from_name(name, description, expected):
    t = derive_term_line(name, description)
    assert (t.due_days, t.due_day_of_month, t.due_months_forward, t.discount_percent, t.discount_days) == expected


def test_term_line_unknown():
    assert derive_term_line("90 EOM", "NET DUE ON THE 90TH AFTER MONTH END") is None   # no 90th of a month
    assert derive_term_line("SPECIAL", "As agreed") is None


def test_term_rows_prefer_the_real_lines(delivery):
    d = read_delivery(delivery)
    rows, notes = term_rows(d)
    assert len(rows["ap.ap_terms_tl"]) == 5 and not notes                 # the fixture delivers AP_TERMS_LINES
    (delivery / "ap_terms_lines.csv").unlink()
    rows, notes = term_rows(read_delivery(delivery))
    eom = next(r for r in rows["ap.ap_terms_lines"] if r["term_id"] == 103)
    assert eom["due_day_of_month"] == 15 and len(notes) == 5


# ----------------------------------------------------------------- gold set
def test_goldset_split_and_history_exclusion(delivery, tmp_path):
    out = tmp_path / "gold"
    m = build_goldset(delivery, out, holdout={"70003"}, coa=KK, pdf_scan=False)
    assert m["counts"]["test"] >= 4 and sum(m["counts"].values()) == 12       # every attached invoice
    truths = [json.loads(p.read_text(encoding="utf-8")) for p in (out / "truth").glob("*.truth.json")]
    azteca = [t for t in truths if t["vendor_number"] == "70003"]
    assert azteca and all(t["split"] == "test" and t["supplier_holdout"] for t in azteca)
    t = next(t for t in truths if t["invoice_id"] == 7000103)                 # the re-keyed distribution
    assert [x["account"] for x in t["expected"]["lines"][0]["distributions"]] == \
        ["10.20.00110.61210.00000.000.00.000"]
    assert set(t["source"]) == {"invoice_number", "total"}                      # only what the keyed record gives
    exclude = json.loads((out / "history_exclude.json").read_text(encoding="utf-8"))
    assert exclude["holdout_suppliers"] == ["70003"]
    assert exclude["history_cutoff"] > "2025-01-06"
    again = build_goldset(delivery, tmp_path / "gold2", holdout={"70003"}, coa=KK, pdf_scan=False)
    assert [i["split"] for i in again["items"]] == [i["split"] for i in m["items"]]   # deterministic
