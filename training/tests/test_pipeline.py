"""aap_train without a GPU or downloads: config refusals, the dataset contract, label masking, LoRA
targets, scoring, adapter checks and the run folder."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from aap_train.collate import IGNORE, Collator, chat_messages, mask_prompt, pad
from aap_train.config import RunCfg, load_config
from aap_train.data import CONTRACT, DatasetError, load_dataset
from aap_train.methods import is_projector, is_vision, lm_target_regex, lora_targets
from aap_train.metrics import headline, list_f1, parse, score_one, summarise
from aap_train.package import check_adapter
from aap_train.tracking import WANDB_ENV, RunDir, wandb_settings
from aap_train.train import keep_best


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()


# ------------------------------------------------------------------ fixtures
def write_dataset(root: Path, task: str = "extract", rows: list[dict] | None = None, image: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if rows is None:
        content = [{"type": "text", "text": "Page 1 image:"}]
        if image:
            (root / "images" / "a").mkdir(parents=True)
            (root / "images" / "a" / "1.png").write_bytes(PNG)
            content.append({"type": "image", "path": "images/a/1.png"})
        rows = [{"id": "a", "task": task, "target": json.dumps({"invoice_number": "INV-1", "lines": []}),
                 "messages": [{"role": "system", "content": [{"type": "text", "text": "You extract."}]},
                              {"role": "user", "content": content}], "meta": {"split": "train"}}]
    files = {}
    for split, data in (("train", rows), ("dev", rows[:1])):
        p = root / f"{split}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in data), encoding="utf-8")
        files[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    (root / "dataset.json").write_text(json.dumps({"contract": CONTRACT, "task": task, "files": files,
                                                   "image": {"max_pixels": 1003520} if task == "extract" else None}),
                                       encoding="utf-8")
    return root


def cfg(tmp_path: Path, **over) -> RunCfg:
    data = {"name": "t", "task": "extract", "data": {"dataset": str(tmp_path)},
            "logging": {"runs_dir": str(tmp_path / "runs")}}
    for k, v in over.items():
        data[k] = {**data.get(k, {}), **v} if isinstance(v, dict) else v
    return RunCfg.model_validate(data)


# -------------------------------------------------------------------- config
def test_full_fine_tune_of_the_7b_is_refused(tmp_path):
    with pytest.raises(ValueError, match="method full on a 8.3B model"):
        cfg(tmp_path, model={"size_b": 8.3}, method={"name": "full"})
    assert cfg(tmp_path, model={"size_b": 3.8}, method={"name": "full"}).merged_serving
    assert cfg(tmp_path, model={"size_b": 8.3}, method={"name": "full", "allow_large": True})


def test_bf16_lora_on_the_7b_with_images_is_refused(tmp_path):
    with pytest.raises(ValueError, match="bf16 lora"):
        cfg(tmp_path, model={"size_b": 8.3}, method={"name": "lora"})
    assert cfg(tmp_path, task="decide", model={"size_b": 8.3}, method={"name": "lora"})   # text only fits


def test_method_properties(tmp_path):
    q = cfg(tmp_path)
    assert q.quantized and not q.merged_serving and q.optimizer == "paged_adamw_8bit"
    p = cfg(tmp_path, method={"name": "lora_projector"})
    assert p.quantized and p.merged_serving
    assert cfg(tmp_path, model={"size_b": 3}, method={"name": "lora"}).optimizer == "adamw_torch"


def test_unknown_keys_and_bad_names_are_refused(tmp_path):
    with pytest.raises(ValueError):
        cfg(tmp_path, train={"lerning_rate": 1})
    with pytest.raises(ValueError, match="run name"):
        cfg(tmp_path, name="a b/c")


def test_load_config_with_overrides(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"name": "x", "task": "decide", "data": {"dataset": str(tmp_path)}}), encoding="utf-8")
    c = load_config(path, {"train.max_steps": 3, "method.rank": 8})
    assert c.train.max_steps == 3 and c.method.rank == 8 and c.data.dataset == tmp_path.resolve()


def test_shipped_configs_are_valid():
    for path in Path("configs").glob("*.yaml"):
        load_config(path)


# ---------------------------------------------------------------------- data
def test_dataset_is_read_and_checked(tmp_path):
    ds = load_dataset(write_dataset(tmp_path / "d"))
    assert ds.task == "extract" and ds.max_pixels == 1003520 and len(ds.splits["train"]) == 1
    ex = ds.splits["train"][0]
    assert ex.image_paths()[0].read_bytes() == PNG and ex.target_json["invoice_number"] == "INV-1"
    assert len(ds.fingerprint) == 16


def test_edited_dataset_is_refused(tmp_path):
    root = write_dataset(tmp_path / "d")
    (root / "train.jsonl").write_text((root / "train.jsonl").read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="never edited"):
        load_dataset(root)


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda r: r.pop("target"), "missing 'target'"),
    (lambda r: r.update(target="not json"), "not JSON"),
    (lambda r: r["messages"].reverse(), "system ... user"),
    (lambda r: r["messages"][1]["content"].append({"type": "image", "path": "images/none.png"}), "not found"),
])
def test_broken_examples_are_refused(tmp_path, mutate, message):
    row = {"id": "a", "task": "extract", "target": "{}", "meta": {},
           "messages": [{"role": "system", "content": [{"type": "text", "text": "s"}]},
                        {"role": "user", "content": [{"type": "text", "text": "u"}]}]}
    mutate(row)
    with pytest.raises(DatasetError, match=message):
        load_dataset(write_dataset(tmp_path / "d", rows=[row]))


def test_images_in_a_decide_dataset_are_refused(tmp_path):
    row = {"id": "a", "task": "decide", "target": "{}", "meta": {},
           "messages": [{"role": "system", "content": [{"type": "text", "text": "s"}]},
                        {"role": "user", "content": [{"type": "image", "path": "images/a/1.png"}]}]}
    with pytest.raises(DatasetError, match="image in a text-only"):
        load_dataset(write_dataset(tmp_path / "d", task="decide", rows=[row]))


def test_wrong_contract_is_refused(tmp_path):
    root = write_dataset(tmp_path / "d")
    (root / "dataset.json").write_text(json.dumps({"contract": "other/9", "task": "extract"}), encoding="utf-8")
    with pytest.raises(DatasetError, match="contract"):
        load_dataset(root)


# ------------------------------------------------------------------- collate
def test_chat_messages_and_answer(tmp_path):
    ex = load_dataset(write_dataset(tmp_path / "d")).splits["train"][0]
    prompt, images = chat_messages(ex, with_answer=False)
    full, _ = chat_messages(ex, with_answer=True)
    assert [m["role"] for m in prompt] == ["system", "user"] and full[-1]["role"] == "assistant"
    assert {"type": "image"} in prompt[1]["content"] and images == [str(tmp_path / "d" / "images/a/1.png")]
    assert full[-1]["content"][0]["text"] == ex.target


def test_only_the_answer_is_learned():
    assert mask_prompt([1, 2, 3, 4, 5], 3) == [IGNORE, IGNORE, IGNORE, 4, 5]
    with pytest.raises(ValueError, match="no answer tokens"):
        mask_prompt([1, 2, 3], 3)


def test_collator_pads_and_masks_padding():
    torch = pytest.importorskip("torch")
    batch = Collator(pad_token_id=0)([
        {"input_ids": [5, 6, 7], "attention_mask": [1, 1, 1], "labels": [IGNORE, 6, 7]},
        {"input_ids": [5, 6], "attention_mask": [1, 1], "labels": [IGNORE, 6],
         "pixel_values": torch.ones(4, 3), "image_grid_thw": torch.tensor([[1, 2, 2]])}])
    assert batch["input_ids"].tolist() == [[5, 6, 7], [5, 6, 0]]
    assert batch["attention_mask"].tolist() == [[1, 1, 1], [1, 1, 0]]
    assert batch["labels"].tolist() == [[IGNORE, 6, 7], [IGNORE, 6, IGNORE]]
    assert batch["pixel_values"].shape == (4, 3) and batch["image_grid_thw"].shape == (1, 3)
    assert pad([[1], [1, 2]], 9) == [[1, 9], [1, 2]]


# ------------------------------------------------------------------- methods
QWEN_VL_MODULES = [
    "model.language_model.layers.0.self_attn.q_proj", "model.language_model.layers.0.mlp.gate_proj",
    "model.language_model.layers.0.mlp.down_proj", "model.visual.blocks.0.attn.qkv",
    "model.visual.blocks.0.mlp.gate_proj", "model.visual.blocks.0.mlp.down_proj",
    "model.visual.merger.mlp.0", "lm_head", "model.layers.3.self_attn.v_proj",   # older layout
]


def test_lora_targets_skip_the_vision_tower():
    got = lora_targets(QWEN_VL_MODULES, ["q_proj", "v_proj", "gate_proj", "down_proj"])
    assert got == ["model.language_model.layers.0.self_attn.q_proj", "model.language_model.layers.0.mlp.gate_proj",
                   "model.language_model.layers.0.mlp.down_proj", "model.layers.3.self_attn.v_proj"]
    assert "visual" in lm_target_regex(["q_proj"])


def test_vision_and_projector_names():
    assert is_vision("model.visual.blocks.0.attn.qkv") and not is_vision("model.visual.merger.mlp.0")
    assert is_projector("model.visual.merger.mlp.0") and not is_projector("model.language_model.layers.0.mlp.up_proj")


# ------------------------------------------------------------------- metrics
def test_score_one():
    target = {"invoice_number": "INV-1", "total": "$10.00", "po_number": None,
              "lines": [{"description": "Box", "amount": "10.00", "uom": None}]}
    good = json.dumps({"invoice_number": "inv-1 ", "total": "$10.00", "po_number": None,
                       "lines": [{"description": "Box", "amount": "10.00", "uom": "EA"}]})
    s = score_one(good, target)
    assert s["valid"] and s["fields"] == 2 and s["fields_correct"] == 2 and s["invented"] == 0
    assert (s["items_matched"], s["items_expected"]) == (1, 1)
    bad = score_one(json.dumps({"invoice_number": "INV-2", "total": "$10.00", "po_number": "4500", "lines": []}), target)
    assert bad["fields_correct"] == 1 and bad["invented"] == 1 and bad["items_matched"] == 0
    assert not score_one("not json", target)["valid"]


def test_nested_fields_are_scored():
    target = {"vendor_id": 7, "basis": {"vendor_id": "chosen", "org_id": "site_default"}}
    s = score_one(json.dumps({"vendor_id": 7, "basis": {"vendor_id": "chosen", "org_id": "printed"}}), target)
    assert (s["fields"], s["fields_correct"]) == (3, 2)


def test_summary_and_headline():
    t = {"a": "1", "lines": [{"x": "1"}]}
    summary = summarise([score_one(json.dumps(t), t), score_one("{", t)])
    assert summary["valid_json"] == 0.5 and summary["field_accuracy"] == 0.5
    assert 0 < headline(summary) < 1
    assert list_f1([{"x": "1"}, {"x": "2"}], [{"x": "1"}]) == (1, 2, 1)
    assert parse("```json\n{\"a\": 1}\n```") == {"a": 1}


# ------------------------------------------------------------------- package
def test_adapter_checks():
    lora = ["base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_A.weight"]
    assert check_adapter({"r": 16}, lora) == []
    problems = check_adapter({"r": 32, "modules_to_save": ["merger"]},
                             [*lora, "base_model.model.model.visual.merger.mlp.0.weight"])
    assert any("modules_to_save" in p for p in problems) and any("rank 32" in p for p in problems)
    assert any("non-LoRA" in p for p in problems) and any("vision tower" in p for p in problems)


# ------------------------------------------------------------------- tracking
def test_run_folder(tmp_path):
    c = cfg(tmp_path)
    run = RunDir.create(c, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    assert run.path.name == "t-20261109T120000Z"
    assert yaml.safe_load((run.path / "config.yaml").read_text(encoding="utf-8"))["name"] == "t"
    run.log(event="log", step=1, loss=1.5)
    run.log(event="dev", step=1, score=0.5)
    lines = [json.loads(x) for x in (run.path / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["event"] for x in lines] == ["log", "dev"] and "time" in lines[0]
    run.describe(dataset={"fingerprint": "abc"})
    assert run.read_json("run.json")["dataset"]["fingerprint"] == "abc"
    with pytest.raises(FileExistsError):
        RunDir.create(c, now=datetime(2026, 11, 9, 12, 0, tzinfo=UTC))


def test_wandb_is_off_by_default_and_metrics_only_when_on(tmp_path, monkeypatch):
    for k in [*WANDB_ENV, "WANDB_MODE", "WANDB_DISABLED"]:
        monkeypatch.delenv(k, raising=False)
    assert wandb_settings(cfg(tmp_path)) == []
    import os

    assert os.environ["WANDB_DISABLED"] == "true"
    pytest.importorskip("wandb")
    assert wandb_settings(cfg(tmp_path, logging={"wandb": True})) == ["wandb"]
    assert os.environ["WANDB_MODE"] == "offline" and os.environ["WANDB_LOG_MODEL"] == "false"


def test_keep_best_copies_weights_only(tmp_path):
    ckpt = tmp_path / "checkpoints" / "checkpoint-5"
    ckpt.mkdir(parents=True)
    for name in ("adapter_model.safetensors", "adapter_config.json", "optimizer.pt", "trainer_state.json"):
        (ckpt / name).write_bytes(b"x")
    keep_best(ckpt, tmp_path / "best")
    assert sorted(p.name for p in (tmp_path / "best").iterdir()) == ["adapter_config.json", "adapter_model.safetensors"]


def test_vision_tower_and_projector_are_never_quantized():
    """skip_modules() against transformers' own matching rule (prefix / regex from the start / suffix)."""
    rule = pytest.importorskip("transformers.quantizers.quantizers_utils").should_convert_module
    from aap_train.methods import skip_modules

    skip = skip_modules()
    for name in ("model.visual.blocks.0.attn.qkv", "model.visual.blocks.3.mlp.gate_proj", "model.visual.merger.mlp.0",
                 "visual.blocks.0.attn.proj", "lm_head"):
        assert not rule(name, skip), name
    for name in ("model.language_model.layers.0.self_attn.q_proj", "model.language_model.layers.27.mlp.down_proj"):
        assert rule(name, skip), name


@pytest.mark.gpu
@pytest.mark.skipif(not __import__("os").environ.get("AAP_TRAIN_GPU_TESTS"), reason="set AAP_TRAIN_GPU_TESTS=1")
@pytest.mark.parametrize("method", ["lora", "qlora", "lora_projector", "full"])
def test_tiny_model_end_to_end(tmp_path, method):
    """Every step with the tiny random Qwen2.5-VL: train, dev evaluation, best checkpoint, package."""
    from aap_train.package import main as package
    from aap_train.train import train

    ds = write_dataset(tmp_path / "d")
    c = RunCfg.model_validate({
        "name": f"tiny-{method}".replace("_", "-"), "task": "extract",
        "model": {"base": "trl-internal-testing/tiny-Qwen2_5_VLForConditionalGeneration", "size_b": 0.01},
        "method": {"name": method, "rank": 8, "alpha": 16}, "data": {"dataset": str(ds), "max_seq_len": 4096},
        "train": {"max_steps": 2, "save_steps": 2, "grad_accum": 1, "logging_steps": 1},
        "eval": {"limit": 1, "max_new_tokens": 8}, "logging": {"runs_dir": str(tmp_path / "runs")}})
    run = train(c, skip_envcheck=True)
    assert (run / "best.json").exists() and (run / "final").exists()
    assert package(["--run", str(run)]) == 0
    assert (run / ("model" if c.merged_serving else "adapter")).is_dir()
