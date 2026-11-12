"""Adapter B's dataset from keyed records (aap.finetune.chat decide, PLAN-MODEL-JSON T2), its offline
evaluation (aap.decide.evaluate) and the learning-curve runner (aap.decide.curve, E5). Made-up data."""

import json

import pytest

from aap.clientdata.fixtures import build
from aap.clientdata.goldset import build_goldset
from aap.common.coa import CoaLayout
from aap.decide.request import parse_user_text
from aap.decide.schema import check_answer

KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)


@pytest.fixture(scope="module")
def delivery(tmp_path_factory):
    return build(tmp_path_factory.mktemp("kk") / "delivery")


@pytest.fixture(scope="module")
def dataset(delivery, tmp_path_factory):
    from aap.finetune.chat import decide_from_delivery

    out = tmp_path_factory.mktemp("b") / "decide"
    decide_from_delivery([delivery], out, coa=KK, holdout={"70003"})
    return out


def _rows(dataset, split):
    return [json.loads(x) for x in (dataset / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]


def test_dataset_follows_the_contract_and_the_split_rule(dataset):
    meta = json.loads((dataset / "dataset.json").read_text(encoding="utf-8"))
    assert meta["contract"] == "aap-train-chat/1" and meta["task"] == "decide"
    assert meta["prompt_version"] == "decide/1" and meta["context_pack_version"] == "kk/1"
    assert sum(meta["counts"].values()) == 13 and meta["counts"]["test"] >= 4     # held-out supplier: all test
    assert meta["learning_curve"]["100%"] == meta["counts"]["train"]
    assert all(r["meta"]["supplier"] == "70003" for r in _rows(dataset, "test") if r["meta"]["holdout"])
    assert not any(r["meta"]["supplier"] == "70003" for s in ("train", "dev") for r in _rows(dataset, s))


def test_no_pack_holds_a_test_or_dev_invoice(dataset):
    held = {r["id"] for s in ("test", "dev") for r in _rows(dataset, s)}
    numbers = {}
    for s in ("train", "dev", "test"):
        for r in _rows(dataset, s):
            printed, _ = parse_user_text(r["messages"][1]["content"][0]["text"])
            numbers[r["id"]] = printed["invoice_number"]
    held_numbers = {numbers[i] for i in held}
    for s in ("train", "dev", "test"):
        for r in _rows(dataset, s):
            printed, pack = parse_user_text(r["messages"][1]["content"][0]["text"])
            recent = {x["invoice_num"] for x in pack["recent_invoices"]}
            assert not recent & held_numbers, (r["id"], recent & held_numbers)
            assert printed["invoice_number"] not in recent
            assert all(x["gl_date"] < r["meta"]["as_of"] for x in pack["recent_invoices"])


def test_every_target_fits_its_schema(dataset):
    for s in ("train", "dev", "test"):
        for r in _rows(dataset, s):
            assert check_answer(json.loads(r["target"]), r["schema"]) == []


def test_train_order_is_seeded_and_the_curve_subsets_are_nested(delivery, tmp_path):
    from aap.finetune.chat import decide_from_delivery

    a, b = tmp_path / "a", tmp_path / "b"
    decide_from_delivery([delivery], a, coa=KK, holdout={"70003"})
    decide_from_delivery([delivery], b, coa=KK, holdout={"70003"})
    ids_a = [r["id"] for r in _rows(a, "train")]
    assert ids_a == [r["id"] for r in _rows(b, "train")]
    assert ids_a != sorted(ids_a)                    # shuffled, so limit_train takes a random subset


def test_split_comes_from_the_gold_set(delivery, tmp_path):
    from aap.finetune.chat import decide_from_delivery

    gold = tmp_path / "gold"
    build_goldset(delivery, gold, holdout={"70002"}, coa=KK, pdf_scan=False)
    truths = {json.loads(p.read_text(encoding="utf-8"))["invoice_id"]: json.loads(p.read_text(encoding="utf-8"))["split"]
              for p in (gold / "truth").glob("*.truth.json")}
    out = tmp_path / "b"
    decide_from_delivery([delivery], out, coa=KK, goldset=gold)
    got = {int(r["id"]): s for s in ("train", "dev", "test") for r in _rows(out, s)}
    assert truths and all(got[i] == split for i, split in truths.items() if i in got)
    assert json.loads((out / "dataset.json").read_text())["holdout_suppliers"] == 1     # 70002, from the gold set


def test_score_counts_exact_lines_and_abstentions():
    from aap.decide.evaluate import score

    target = {"invoice_type_lookup_code": "STANDARD", "terms_name": "30 NET", "payment_method_code": "EFT",
              "pay_group_lookup_code": None, "liability_account": None,
              "basis": {"invoice_type_lookup_code": "printed", "terms_name": "site_default", "payment_method_code":
                        "site_default", "pay_group_lookup_code": None, "liability_account": None},
              "lines": [{"line_number": 1, "line_type_lookup_code": "ITEM", "account": "A", "distribution_set": None,
                         "basis": "history"},
                        {"line_number": 2, "line_type_lookup_code": "ITEM", "account": None, "distribution_set": None,
                         "basis": None}]}
    printed = {"lines": [{"line_number": 1, "amount": "300.00"}, {"line_number": 2, "amount": "100.00"}]}
    wrong = json.loads(json.dumps(target))
    wrong["lines"][0]["account"] = "B"
    m = score([(target, target, printed), (wrong, target, printed)])
    assert m["exact"] == 0.5 and m["lines"]["accuracy"] == 0.5 and m["lines"]["amount_accuracy"] == 0.5
    assert m["abstain"] == {"lines": 2, "answered_null": 1.0} and m["header"]["terms_name"] == 1.0


def test_offline_evaluation_with_the_stub(dataset, monkeypatch):
    from aap.config import get_settings
    from aap.decide.evaluate import evaluate
    from aap.llm import get_llm

    monkeypatch.setenv("LLM_BACKEND", "stub")
    get_settings.cache_clear()
    get_llm.cache_clear()
    try:
        result = evaluate(dataset, split="test", model="stub", log=lambda *_: None)
    finally:
        get_settings.cache_clear()
        get_llm.cache_clear()
    m = result["metrics"]
    assert m["invoices"] == json.loads((dataset / "dataset.json").read_text())["counts"]["test"] and m["failed"] == 0
    assert m["header"]["invoice_type_lookup_code"] == 1.0


def test_curve_dry_run_trains_nested_sizes(dataset):
    from aap.decide.curve import Curve, run_curve

    lines: list[str] = []
    run_curve(Curve(dataset=dataset, fractions=(50, 100)), dry_run=True, log=lines.append)
    n_train = json.loads((dataset / "dataset.json").read_text())["counts"]["train"]
    limits = [x.split("data.limit_train=")[1].split()[0] for x in lines if "data.limit_train=" in x]
    assert limits == [str(max(1, round(n_train / 2))), str(n_train)]
    assert sum("stop vLLM" in x for x in lines) == 2 and sum("--slot decide" in x for x in lines) == 2
