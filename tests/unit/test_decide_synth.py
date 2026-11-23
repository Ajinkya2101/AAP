"""Adapter B's dataset scope and synthetic copies (aap.decide.synth, aap.finetune.chat decide with
--suppliers / --file-invoices / --per-supplier / --synthetic, 2026-11-23), the account segment view
(aap.clientdata.views) and the forced-test split (aap.clientdata.goldset). Made-up data."""

import csv
import json
import random
from datetime import date
from decimal import Decimal

import pytest

from aap.clientdata.fixtures import build
from aap.clientdata.goldset import GoldItem, split_items
from aap.clientdata.readers import read_delivery
from aap.common.coa import CoaLayout
from aap.decide import synth
from aap.decide.request import parse_user_text
from aap.decide.schema import check_answer

KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)


# ------------------------------------------------------------------ account segment view
def test_account_segment_view_becomes_the_chart_of_accounts(tmp_path):
    rows = [
        {"INVOICE_DISTRIBUTION_ID": 1, "INVOICE_ID": 10, "DIST_CODE_COMBINATION_ID": 501, "COMPANY": "10",
         "BUSINESS_UNIT": "20", "COST_CENTER": "70010", "NATURAL_ACCOUNT": "65020", "PROJECT": "00000",
         "LOB": "000", "INTERCOMPANY": "00", "FUTURE": "000", "SEG1": "EXAMPLE CO", "SEG3": "DISTRIBUTION CENTER",
         "SEG4": "FREIGHT IN"},
        {"INVOICE_DISTRIBUTION_ID": 2, "INVOICE_ID": 11, "DIST_CODE_COMBINATION_ID": 501, "COMPANY": "10",
         "BUSINESS_UNIT": "20", "COST_CENTER": "70010", "NATURAL_ACCOUNT": "65020", "PROJECT": "00000",
         "LOB": "000", "INTERCOMPANY": "00", "FUTURE": "000", "SEG1": "EXAMPLE CO", "SEG3": "DISTRIBUTION CENTER",
         "SEG4": "FREIGHT IN"},
        {"INVOICE_DISTRIBUTION_ID": 3, "INVOICE_ID": 12, "DIST_CODE_COMBINATION_ID": 502, "COMPANY": "10",
         "BUSINESS_UNIT": "30", "COST_CENTER": "00110", "NATURAL_ACCOUNT": "61210", "PROJECT": "00000",
         "LOB": "000", "INTERCOMPANY": "00", "FUTURE": "000", "SEG1": "EXAMPLE CO", "SEG3": "STORE 110",
         "SEG4": "PACKAGING SUPPLIES"},
    ]
    with (tmp_path / "AP_DIST1.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    d = read_delivery(tmp_path)
    assert "ACCOUNT_SEGMENT_VIEW" not in d.tables
    combos = {r["CODE_COMBINATION_ID"]: r for r in d.rows("GL_CODE_COMBINATIONS")}
    assert set(combos) == {501, 502}
    assert [combos[502][f"SEGMENT{i}"] for i in range(1, 9)] == ["10", "30", "00110", "61210", "00000", "000", "00", "000"]
    names = {s["APPLICATION_COLUMN_NAME"]: s["SEGMENT_NAME"] for s in d.rows("FND_ID_FLEX_SEGMENTS")}
    assert names["SEGMENT3"] == "Cost Center" and names["SEGMENT4"] == "Natural Account"
    values = {(v["FLEX_VALUE_SET_NAME"], v["FLEX_VALUE"]): v["DESCRIPTION"] for v in d.rows("FND_FLEX_VALUES")}
    assert values[("DELIVERED SEGMENT3", "00110")] == "STORE 110"
    assert values[("DELIVERED SEGMENT4", "65020")] == "FREIGHT IN"

    from aap.decide.history import from_delivery

    store = from_delivery(d, KK)
    assert store.account_descriptions["10.30.00110.61210.00000.000.00.000"] == "STORE 110 / PACKAGING SUPPLIES"


# ------------------------------------------------------------------ split
def test_file_invoices_are_forced_into_test():
    items = [GoldItem(invoice_id=i, vendor_id=1, vendor_number="1", invoice_date=f"2026-09-{i:02d}", pdf=None)
             for i in range(1, 21)]
    split_items(items, holdout=set(), seed=7, force_test={1, 2})
    by_id = {i.invoice_id: i.split for i in items}
    assert by_id[1] == by_id[2] == "test"                 # the oldest: test only because they have a file
    assert by_id[20] == "test"                            # the most recent ~15 % stay test


# ------------------------------------------------------------------ clone
def _parent():
    header = {"INVOICE_ID": 100, "VENDOR_ID": 5, "VENDOR_SITE_ID": 6, "INVOICE_NUM": "INV-0420", "SOURCE": "MANUAL",
              "INVOICE_TYPE_LOOKUP_CODE": "STANDARD", "TERMS_ID": 10, "PAY_GROUP_LOOKUP_CODE": "STD",
              "INVOICE_DATE": date(2026, 9, 20), "GL_DATE": date(2026, 9, 22), "TERMS_DATE": date(2026, 9, 20),
              "INVOICE_AMOUNT": Decimal("300.00"), "AMOUNT_PAID": Decimal("300.00"), "INVOICE_CURRENCY_CODE": "USD"}
    lines = [{"INVOICE_ID": 100, "LINE_NUMBER": 1, "LINE_TYPE_LOOKUP_CODE": "ITEM", "AMOUNT": Decimal("200.00"),
              "DESCRIPTION": "mats"},
             {"INVOICE_ID": 100, "LINE_NUMBER": 2, "LINE_TYPE_LOOKUP_CODE": "ITEM", "AMOUNT": Decimal("100.00"),
              "DESCRIPTION": None, "DISTRIBUTION_SET_ID": 77}]
    dists = [{"INVOICE_DISTRIBUTION_ID": 1, "INVOICE_ID": 100, "INVOICE_LINE_NUMBER": 1, "AMOUNT": Decimal("120.00"),
              "DIST_CODE_COMBINATION_ID": 501, "ACCTS_PAY_CODE_COMBINATION_ID": 9},
             {"INVOICE_DISTRIBUTION_ID": 2, "INVOICE_ID": 100, "INVOICE_LINE_NUMBER": 1, "AMOUNT": Decimal("80.00"),
              "DIST_CODE_COMBINATION_ID": 502, "ACCTS_PAY_CODE_COMBINATION_ID": 9},
             {"INVOICE_DISTRIBUTION_ID": 3, "INVOICE_ID": 100, "INVOICE_LINE_NUMBER": 2, "AMOUNT": Decimal("100.00"),
              "DIST_CODE_COMBINATION_ID": 503, "ACCTS_PAY_CODE_COMBINATION_ID": 9},
             {"INVOICE_DISTRIBUTION_ID": 4, "INVOICE_ID": 100, "INVOICE_LINE_NUMBER": 2, "AMOUNT": Decimal("5.00"),
              "DIST_CODE_COMBINATION_ID": 504, "REVERSAL_FLAG": "Y"}]
    return synth.Invoice(header, lines, dists)


def test_a_copy_keeps_the_oracle_values_and_changes_identity_dates_and_amounts():
    parent = _parent()
    pool = synth.description_pool([parent])
    window = (date(2026, 9, 10), date(2026, 10, 30))
    for k in range(25):
        rnd = random.Random(k)
        inv = synth.clone(parent, invoice_id=900_000_000 + k, first_dist_id=900_000_000, rnd=rnd, window=window,
                          descriptions=pool, taken_numbers={"INV-0420"})
        h = inv.header
        for col in ("VENDOR_ID", "VENDOR_SITE_ID", "SOURCE", "INVOICE_TYPE_LOOKUP_CODE", "TERMS_ID",
                    "PAY_GROUP_LOOKUP_CODE", "INVOICE_CURRENCY_CODE"):
            assert h[col] == parent.header[col]
        assert h["INVOICE_ID"] == 900_000_000 + k and h["INVOICE_NUM"] != "INV-0420"
        assert h["INVOICE_NUM"].startswith("INV-") and len(h["INVOICE_NUM"]) == len("INV-0420")
        assert parent.header["INVOICE_DATE"] <= h["INVOICE_DATE"]                    # never earlier
        assert parent.header["GL_DATE"] <= h["GL_DATE"] <= window[1]
        assert (h["GL_DATE"] - h["INVOICE_DATE"]).days == 2                          # the parent's gap
        assert h["AMOUNT_PAID"] is None
        assert (h["SYNTHETIC"], h["PARENT_INVOICE_ID"], h["GEN_VERSION"]) == ("Y", 100, synth.GEN_VERSION)
        assert [x["DIST_CODE_COMBINATION_ID"] for x in inv.dists] == [501, 502, 503]  # the reversal is left out
        assert {x["ACCTS_PAY_CODE_COMBINATION_ID"] for x in inv.dists} == {9}
        assert inv.lines[1]["DISTRIBUTION_SET_ID"] == 77
        for ln in inv.lines:
            own = [x["AMOUNT"] for x in inv.dists if x["INVOICE_LINE_NUMBER"] == ln["LINE_NUMBER"]]
            assert sum(own) == ln["AMOUNT"]                                          # distributions add up
        assert h["INVOICE_AMOUNT"] == sum(ln["AMOUNT"] for ln in inv.lines)
        assert Decimal("0.6") * 300 - 1 <= h["INVOICE_AMOUNT"] <= Decimal("1.4") * 300 + 1
        assert inv.lines[0]["DESCRIPTION"] == "mats" and inv.lines[1]["DESCRIPTION"] is None  # from the pool


def test_parents_are_used_in_turn():
    got = synth.parents_for(["a", "b", "c"], 7, random.Random(1))
    assert sorted(got.count(x) for x in "abc") == [2, 2, 3]


# ------------------------------------------------------------------ dataset
@pytest.fixture(scope="module")
def delivery(tmp_path_factory):
    return build(tmp_path_factory.mktemp("kk") / "delivery")


@pytest.fixture(scope="module")
def built(delivery, tmp_path_factory):
    from aap.finetune.chat import decide_from_delivery

    root = tmp_path_factory.mktemp("b")
    kw = {"coa": KK, "suppliers": {"70001", "70002"}, "holdout": {"70003"}, "file_invoices": {7000101},
          "per_supplier": 6}
    decide_from_delivery([delivery], root / "syn", synthetic=True, **kw)
    decide_from_delivery([delivery], root / "real", **kw)
    return root


def _rows(root, split):
    return [json.loads(x) for x in (root / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]


def test_every_supplier_gets_exactly_n_train_examples(built):
    meta = json.loads((built / "syn" / "dataset.json").read_text(encoding="utf-8"))
    train = _rows(built / "syn", "train")
    for supplier in ("70001", "70002"):
        assert sum(1 for r in train if r["meta"]["supplier"] == supplier) == 6
    assert not any(r["meta"]["supplier"] == "70003" for r in train)            # held out: test only
    assert meta["per_supplier"]["70003"]["holdout"] and meta["per_supplier"]["70003"]["test"] >= 4
    assert meta["labels"]["synthetic"] == sum(v.get("train_synthetic", 0) for v in meta["per_supplier"].values())
    assert meta["scope"]["per_supplier"] == 6 and meta["scope"]["synthetic"]


def test_synthetic_examples_are_train_only_and_never_in_a_pack(built):
    rows = {s: _rows(built / "syn", s) for s in ("train", "dev", "test")}
    assert all(r["meta"]["label"] == "keyed" for s in ("dev", "test") for r in rows[s])
    numbers = {}
    for rs in rows.values():
        for r in rs:
            printed, _ = parse_user_text(r["messages"][1]["content"][0]["text"])
            numbers[r["id"]] = printed["invoice_number"]
    synthetic = {numbers[r["id"]] for r in rows["train"] if r["meta"]["label"] == "synthetic"}
    assert synthetic
    for rs in rows.values():
        for r in rs:
            _, pack = parse_user_text(r["messages"][1]["content"][0]["text"])
            recent = {x["invoice_num"] for x in pack["recent_invoices"]}
            assert not recent & synthetic
            if r["meta"]["label"] == "synthetic":
                assert numbers[str(r["meta"]["parent_invoice_id"])] not in recent   # the parent is left out
            bad = check_answer(json.loads(r["target"]), r["schema"])
            assert not bad, (r["id"], bad)


def test_a_copy_gets_its_parents_candidates(built):
    rows = _rows(built / "syn", "train")
    real = {r["id"]: r for r in rows if r["meta"]["label"] == "keyed"}
    for r in rows:
        if r["meta"]["label"] != "synthetic":
            continue
        parent = real[str(r["meta"]["parent_invoice_id"])]
        assert r["schema"] == parent["schema"] and r["meta"]["as_of"] == parent["meta"]["as_of"]


def test_the_control_set_has_the_same_dev_and_test(built):
    for split in ("dev", "test"):
        assert (built / "syn" / f"{split}.jsonl").read_bytes() == (built / "real" / f"{split}.jsonl").read_bytes()
    assert {r["id"] for r in _rows(built / "real", "train")} == {
        r["id"] for r in _rows(built / "syn", "train") if r["meta"]["label"] == "keyed"}
    test = _rows(built / "syn", "test")
    assert any(r["id"] == "7000101" and r["meta"]["has_file"] for r in test)      # forced in by its file


def test_synthetic_rows_are_written_with_their_markers(built):
    meta = json.loads((built / "syn" / "dataset.json").read_text(encoding="utf-8"))
    assert set(meta["synthetic_tables"]) == {"synthetic/AP_INVOICES_ALL.csv", "synthetic/AP_INVOICE_LINES_ALL.csv",
                                             "synthetic/AP_INVOICE_DISTRIBUTIONS_ALL.csv"}
    with (built / "syn" / "synthetic" / "AP_INVOICES_ALL.csv").open(encoding="utf-8") as f:
        headers = list(csv.DictReader(f))
    assert len(headers) == meta["labels"]["synthetic"]
    assert all(h["SYNTHETIC"] == "Y" and h["PARENT_INVOICE_ID"] and int(h["INVOICE_ID"]) >= synth.SYNTHETIC_ID_BASE
               for h in headers)
    assert not (built / "real" / "synthetic").exists()
