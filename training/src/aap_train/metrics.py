"""Scores for choosing a checkpoint: the generated JSON against the target, field by field.

This is a quick proxy computed inside training. The decision to use an adapter is made with the
application's own evaluation (aap.eval, pipeline 1) on the served model.

- header fields: every scalar key of the target (and of nested objects such as `basis`) that is not
  null; equal after trimming and, for numbers, numerically;
- lists of objects (`lines`, `taxes`): F1 of the items, an item matching when all its non-null target
  fields are equal;
- invented values: keys the target has as null but the answer fills;
- valid: the answer parses as a JSON object.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any


def _norm(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int | float):
        return Decimal(str(v))
    if isinstance(v, str):
        s = " ".join(v.split())
        try:
            return Decimal(s) if s.replace(".", "", 1).replace("-", "", 1).isdigit() else s.casefold()
        except InvalidOperation:
            return s.casefold()
    return v


def same(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


def flat(obj: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Scalar fields with dotted keys; lists are left out (scored as items)."""
    out: dict[str, Any] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flat(v, f"{key}."))
        elif not isinstance(v, list):
            out[key] = v
    return out


def _item_match(pred: dict[str, Any], target: dict[str, Any]) -> bool:
    keys = [k for k, v in flat(target).items() if v is not None]
    p = flat(pred)
    return bool(keys) and all(same(p.get(k), flat(target)[k]) for k in keys)


def list_f1(pred: Iterable[dict[str, Any]], target: Iterable[dict[str, Any]]) -> tuple[int, int, int]:
    """(matched, predicted, expected): each target item matched at most once."""
    pred, target = [x for x in pred if isinstance(x, dict)], [x for x in target if isinstance(x, dict)]
    used: set[int] = set()
    matched = 0
    for t in target:
        for i, p in enumerate(pred):
            if i not in used and _item_match(p, t):
                used.add(i)
                matched += 1
                break
    return matched, len(pred), len(target)


def parse(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def score_one(answer: str, target: dict[str, Any]) -> dict[str, Any]:
    pred = parse(answer)
    t_flat = flat(target)
    labelled = {k: v for k, v in t_flat.items() if v is not None}
    out: dict[str, Any] = {"valid": pred is not None, "fields": len(labelled), "fields_correct": 0, "invented": 0,
                           "items_matched": 0, "items_predicted": 0, "items_expected": 0}
    lists = [k for k, v in target.items() if isinstance(v, list)]
    if pred is None:
        out["items_expected"] = sum(len(target[k]) for k in lists)
        return out
    p_flat = flat(pred)
    out["fields_correct"] = sum(same(p_flat.get(k), v) for k, v in labelled.items())
    out["invented"] = sum(1 for k, v in t_flat.items() if v is None and p_flat.get(k) not in (None, ""))
    for k in lists:
        m, p, e = list_f1(pred.get(k) or [], target[k])
        out["items_matched"] += m
        out["items_predicted"] += p
        out["items_expected"] += e
    return out


def summarise(scores: list[dict[str, Any]]) -> dict[str, float]:
    n = len(scores) or 1
    fields = sum(s["fields"] for s in scores)
    pred, exp = sum(s["items_predicted"] for s in scores), sum(s["items_expected"] for s in scores)
    matched = sum(s["items_matched"] for s in scores)
    precision = matched / pred if pred else (1.0 if not exp else 0.0)
    recall = matched / exp if exp else 1.0
    return {
        "examples": len(scores),
        "valid_json": sum(s["valid"] for s in scores) / n,
        "field_accuracy": sum(s["fields_correct"] for s in scores) / fields if fields else 0.0,
        "item_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "invented_per_example": sum(s["invented"] for s in scores) / n,
    }


def headline(summary: dict[str, float]) -> float:
    """One number to rank checkpoints: field accuracy and item F1, an invalid answer counting as zero."""
    return summary["valid_json"] * (0.7 * summary["field_accuracy"] + 0.3 * summary["item_f1"])
