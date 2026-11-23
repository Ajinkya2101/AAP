"""aap_train.sweep (HPO) and the container's model folder (AAP_TRAIN_MODEL_DIR), without a GPU: the trials
a sweep makes, resuming, failures, the leaderboard. Training itself is replaced by a fake runner."""

import json
from pathlib import Path

import pytest
import yaml

from aap_train.config import load_config, local_base
from aap_train.sweep import leaderboard, run_sweep, trials


def test_grid_is_every_combination_with_linked_keys():
    spec = {"strategy": "grid", "fixed": {"train.epochs": 1},
            "space": {"train.learning_rate": [1e-4, 2e-4],
                      "lora": [{"method.rank": 8, "method.alpha": 16}, {"method.rank": 16, "method.alpha": 32}]}}
    got = trials(spec)
    assert [t["id"] for t in got] == ["t01", "t02", "t03", "t04"]
    assert got[0]["overrides"] == {"train.epochs": 1, "train.learning_rate": 1e-4, "method.rank": 8, "method.alpha": 16}
    assert {(t["overrides"]["train.learning_rate"], t["overrides"]["method.rank"]) for t in got} == {
        (1e-4, 8), (1e-4, 16), (2e-4, 8), (2e-4, 16)}


def test_random_is_seeded_distinct_and_in_range():
    spec = {"strategy": "random", "trials": 6, "seed": 3,
            "space": {"train.learning_rate": {"loguniform": [1e-5, 1e-3]}, "train.epochs": [1, 2, 3],
                      "train.warmup_ratio": {"uniform": [0.0, 0.1]}, "method.rank": {"int": [4, 64]}}}
    a, b = trials(spec), trials(spec)
    assert a == b and len(a) == 6
    assert len({json.dumps(t["overrides"], sort_keys=True) for t in a}) == 6
    for t in a:
        o = t["overrides"]
        assert 1e-5 <= o["train.learning_rate"] <= 1e-3 and o["train.epochs"] in (1, 2, 3)
        assert 0.0 <= o["train.warmup_ratio"] <= 0.1 and 4 <= o["method.rank"] <= 64


def test_distributions_are_refused_in_a_grid():
    with pytest.raises(ValueError, match="random only"):
        trials({"strategy": "grid", "space": {"train.learning_rate": {"uniform": [0.0, 1.0]}}})


def _sweep_file(tmp_path: Path) -> Path:
    data = tmp_path / "ds"
    data.mkdir()
    (tmp_path / "base.yaml").write_text(yaml.safe_dump({"name": "b", "task": "decide", "data": {"dataset": str(data)}}),
                                        encoding="utf-8")
    sweep = tmp_path / "s.yaml"
    sweep.write_text(yaml.safe_dump({"name": "s", "base": "base.yaml", "strategy": "grid",
                                     "space": {"train.learning_rate": [1e-4, 2e-4, 3e-4]}}), encoding="utf-8")
    return sweep


def _fake_runner(scores: dict[float, float | None]):
    """Writes the run folder a training run would leave (best.json, metrics.jsonl), or fails."""
    calls = []

    def run(cmd: list[str]) -> int:
        sets = dict(cmd[i + 1].split("=", 1) for i, x in enumerate(cmd) if x == "--set")
        calls.append(sets)
        lr = json.loads(sets["train.learning_rate"])
        if scores.get(lr) is None:
            return 1
        folder = Path(sets["logging.runs_dir"]) / f"{sets['name']}-20261123T000000Z"
        folder.mkdir(parents=True)
        (folder / "best.json").write_text(json.dumps({"score": scores[lr], "step": 50}), encoding="utf-8")
        (folder / "metrics.jsonl").write_text(
            json.dumps({"event": "log", "loss": 0.5, "peak_gpu_gb": 13.1}) + "\n"
            + json.dumps({"event": "dev", "score": scores[lr]}) + "\n", encoding="utf-8")
        return 0

    run.calls = calls
    return run


def test_a_sweep_runs_ranks_records_failures_and_resumes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sweep = _sweep_file(tmp_path)
    runner = _fake_runner({1e-4: -0.40, 2e-4: -0.30, 3e-4: None})       # 3e-4 "runs out of memory"
    folder = run_sweep(sweep, runner=runner)
    rows = [json.loads(x) for x in (folder / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in rows] == ["ok", "ok", "failed (1)"]
    assert rows[1]["score"] == -0.30 and rows[1]["peak_gpu_gb"] == 13.1 and rows[1]["best_step"] == 50
    board = (folder / "leaderboard.md").read_text(encoding="utf-8")
    assert board.index("| 1 | t02 | 0.3000") < board.index("| 2 | t01 | 0.4000") < board.index("failed (1)")
    assert all(c["name"].startswith("s-t0") for c in runner.calls)

    again = _fake_runner({1e-4: -0.40, 2e-4: -0.30, 3e-4: -0.20})
    run_sweep(sweep, resume=folder, runner=again)
    assert len(again.calls) == 0                                         # recorded trials are not repeated
    rows = [json.loads(x) for x in (folder / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3


def test_max_trials_and_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    sweep = _sweep_file(tmp_path)
    run_sweep(sweep, dry_run=True)
    assert "3 trials" in capsys.readouterr().out
    runner = _fake_runner({1e-4: -0.4, 2e-4: -0.3, 3e-4: -0.2})
    folder = run_sweep(sweep, max_trials=2, runner=runner)
    assert len(runner.calls) == 2
    later = _fake_runner({1e-4: -0.4, 2e-4: -0.3, 3e-4: -0.2})
    run_sweep(sweep, resume=folder, runner=later)
    assert len(later.calls) == 1 and json.loads(later.calls[0]["train.learning_rate"]) == 3e-4


def test_leaderboard_lists_failures_last():
    board = leaderboard([{"id": "t01", "overrides": {"a": 1}, "status": "failed (1)"},
                         {"id": "t02", "overrides": {"a": 2}, "score": -0.25, "status": "ok"}])
    assert board.index("t02") < board.index("t01")


def test_shipped_sweeps_are_valid():
    for path in Path("configs/sweeps").glob("*.yaml"):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        base = (path.parent / spec["base"]).resolve()
        for t in trials(spec):
            load_config(base, {**t["overrides"], "name": f"{spec['name']}-{t['id']}"})


def test_hub_names_resolve_to_the_container_model_folder(tmp_path, monkeypatch):
    (tmp_path / "Qwen2.5-VL-7B-Instruct").mkdir()
    (tmp_path / "Qwen2.5-VL-7B-Instruct" / "config.json").write_text("{}", encoding="utf-8")
    assert local_base("Qwen/Qwen2.5-VL-7B-Instruct", str(tmp_path)) == str(tmp_path / "Qwen2.5-VL-7B-Instruct")
    assert local_base("org/not-there", str(tmp_path)) == "org/not-there"
    assert local_base("Qwen/Qwen2.5-VL-7B-Instruct", "") == "Qwen/Qwen2.5-VL-7B-Instruct"
    monkeypatch.setenv("AAP_TRAIN_MODEL_DIR", str(tmp_path))
    data = tmp_path / "ds"
    data.mkdir()
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"name": "x", "task": "decide", "data": {"dataset": str(data)},
                                    "model": {"base": "Qwen/Qwen2.5-VL-7B-Instruct"}}), encoding="utf-8")
    assert load_config(path).model.base == str(tmp_path / "Qwen2.5-VL-7B-Instruct")


def test_the_length_probe_is_a_valid_dataset_at_the_asked_lengths(tmp_path):
    import hashlib

    from aap_train.data import CONTRACT, load_dataset
    from aap_train.probe import build

    src = tmp_path / "src"
    src.mkdir()
    row = {"id": "a", "task": "decide", "target": json.dumps({"vendor_id": 1}),
           "messages": [{"role": "system", "content": [{"type": "text", "text": "You decide."}]},
                        {"role": "user", "content": [{"type": "text", "text": "Printed values: {}"}]}],
           "meta": {"split": "train"}}
    files = {}
    for split in ("train", "dev"):
        p = src / f"{split}.jsonl"
        p.write_text(json.dumps(row) + "\n", encoding="utf-8")
        files[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    (src / "dataset.json").write_text(json.dumps({"contract": CONTRACT, "task": "decide", "files": files}),
                                      encoding="utf-8")
    out = tmp_path / "probe"
    build(src, out, median=2000, maximum=3000, train=5, dev=2)
    ds = load_dataset(out, splits=("train", "dev"))
    assert len(ds.splits["train"]) == 5 and len(ds.splits["dev"]) == 2
    first = json.loads((out / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(first["messages"][1]["content"][0]["text"]) > 4000          # padded towards 3,000 tokens
