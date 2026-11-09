"""Training datasets in the aap-train-chat/1 contract (aap.finetune.chat), from the committed synthetic set."""

import hashlib
import json
from pathlib import Path

import pytest

from aap.finetune.chat import CONTRACT, decide_smoke, extract_from_eval_set, main
from aap.schemas.source import ExtractedInvoice

EVAL_SET = Path("test-data/invoices-dev-55")
pytestmark = pytest.mark.skipif(not (EVAL_SET / "manifest.json").exists(), reason="synthetic set not present")


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def test_extract_dataset_follows_the_contract(tmp_path):
    meta = extract_from_eval_set(EVAL_SET, tmp_path, limit=6, dev_share=0, test_share=0)
    assert meta["contract"] == CONTRACT and meta["task"] == "extract" and meta["prompt_version"]
    assert meta["image"]["max_pixels"] > 0
    for name, digest in meta["files"].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    rows = _rows(tmp_path / "train.jsonl")
    assert rows and meta["counts"]["train"] == len(rows)
    r = rows[0]
    system, user = r["messages"]
    assert system["role"] == "system" and "extract data from supplier invoices" in system["content"][0]["text"]
    images = [p for p in user["content"] if p["type"] == "image"]
    assert images and all((tmp_path / p["path"]).read_bytes()[:4] == b"\x89PNG" for p in images)
    assert not any("base64" in json.dumps(p) for p in user["content"])
    target = json.loads(r["target"])
    assert list(target) == list(ExtractedInvoice.model_fields)          # every key, in the schema's order
    ExtractedInvoice.model_validate(target)
    assert r["schema"]["properties"].keys() == ExtractedInvoice.model_json_schema()["properties"].keys()


def test_scanned_invoices_are_left_out_and_counted(tmp_path):
    meta = extract_from_eval_set(EVAL_SET, tmp_path)
    assert meta["skipped"].get("image-only pages (evidence boxes not labelled)", 0) > 0


def test_split_is_deterministic(tmp_path):
    a = extract_from_eval_set(EVAL_SET, tmp_path / "a", limit=10, seed=3)
    b = extract_from_eval_set(EVAL_SET, tmp_path / "b", limit=10, seed=3)
    assert a["counts"] == b["counts"] and a["files"] == b["files"]


def test_decide_smoke_dataset(tmp_path):
    meta = decide_smoke(EVAL_SET, tmp_path, limit=8, dev_share=0, test_share=0)
    assert meta["task"] == "decide" and meta["context_pack_version"] == "smoke/0" and meta["image"] is None
    r = _rows(tmp_path / "train.jsonl")[0]
    assert all(p["type"] == "text" for m in r["messages"] for p in m["content"])
    target = json.loads(r["target"])
    context = json.loads(r["messages"][1]["content"][0]["text"].split("Context pack:\n", 1)[1])
    assert target["vendor_id"] in {c["vendor_id"] for c in context["candidates"]}
    assert set(target["basis"]) == {"vendor_id", "vendor_site_id", "org_id", "currency_code"}


def test_cli_refuses_to_overwrite(tmp_path):
    (tmp_path / "train.jsonl").write_text("{}\n", encoding="utf-8")
    assert main(["decide-smoke", "--eval-set", str(EVAL_SET), "--out", str(tmp_path)]) == 2
