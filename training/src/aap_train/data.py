"""Reader of the `aap-train-chat/1` dataset contract (docs/PLAN-TRAINING-PIPELINE.md §3).

The application writes the datasets (`python -m aap.finetune.chat`); this side only reads them, checks
the contract version and the file hashes, and validates every example before training starts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTRACT = "aap-train-chat/1"
SPLITS = ("train", "dev", "test")


class DatasetError(ValueError):
    pass


@dataclass
class Example:
    id: str
    task: str
    messages: list[dict[str, Any]]
    target: str
    schema: dict[str, Any] | None
    meta: dict[str, Any]
    root: Path

    def image_paths(self) -> list[Path]:
        return [self.root / p["path"] for m in self.messages for p in m["content"] if p.get("type") == "image"]

    @property
    def target_json(self) -> Any:
        return json.loads(self.target)


@dataclass
class Dataset:
    root: Path
    meta: dict[str, Any]
    splits: dict[str, list[Example]] = field(default_factory=dict)

    @property
    def task(self) -> str:
        return self.meta["task"]

    @property
    def max_pixels(self) -> int | None:
        return (self.meta.get("image") or {}).get("max_pixels")

    @property
    def fingerprint(self) -> str:
        """One hash for the dataset: the hashes of its split files."""
        files = self.meta.get("files") or {}
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_example(raw: dict[str, Any], task: str, root: Path, where: str) -> Example:
    for key in ("id", "task", "messages", "target"):
        if key not in raw:
            raise DatasetError(f"{where}: missing {key!r}")
    if raw["task"] != task:
        raise DatasetError(f"{where}: task {raw['task']!r} in a {task!r} dataset")
    msgs = raw["messages"]
    if not msgs or msgs[0].get("role") != "system" or msgs[-1].get("role") != "user":
        raise DatasetError(f"{where}: messages must be system ... user (the target is the assistant turn)")
    for m in msgs:
        for p in m.get("content") or []:
            if p.get("type") == "image":
                if task != "extract":
                    raise DatasetError(f"{where}: image in a text-only {task!r} dataset")
                if not (root / p["path"]).is_file():
                    raise DatasetError(f"{where}: image {p['path']} not found")
            elif p.get("type") != "text":
                raise DatasetError(f"{where}: content part type {p.get('type')!r}")
    try:
        json.loads(raw["target"])
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{where}: target is not JSON ({exc})") from exc
    return Example(id=str(raw["id"]), task=raw["task"], messages=msgs, target=raw["target"],
                   schema=raw.get("schema"), meta=raw.get("meta") or {}, root=root)


def load_dataset(root: Path, *, splits: tuple[str, ...] = ("train", "dev"), verify: bool = True) -> Dataset:
    root = Path(root)
    meta_path = root / "dataset.json"
    if not meta_path.is_file():
        raise DatasetError(f"{root}: no dataset.json (build one with python -m aap.finetune.chat)")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("contract") != CONTRACT:
        raise DatasetError(f"{root}: contract {meta.get('contract')!r}, expected {CONTRACT!r}")
    ds = Dataset(root=root, meta=meta)
    for split in splits:
        path = root / f"{split}.jsonl"
        if not path.is_file():
            ds.splits[split] = []
            continue
        expected = (meta.get("files") or {}).get(path.name)
        if verify and expected and _sha(path) != expected:
            raise DatasetError(f"{path}: content does not match dataset.json (a dataset is never edited)")
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        ds.splits[split] = [_check_example(r, meta["task"], root, f"{path.name}:{i + 1}") for i, r in enumerate(rows)]
    return ds
