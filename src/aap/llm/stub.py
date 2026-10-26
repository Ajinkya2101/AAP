"""Stub LLM backend (LLM_BACKEND=stub).

- extract:      returns the invoice generator's ground truth from
                <TRUTH_DIR>/<sha256>.truth.json; without a sidecar it falls back to a
                small regex extractor over the page text (header fields only).
- choose:       picks the first real candidate of the enum (plan section 7).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from aap.config import Settings

from .client import LlmError, LlmRequest, LlmResponse, _validate

NON_CHOICES = {"none", "review", None}


class StubClient:
    backend = "stub"

    def __init__(self, settings: Settings):
        self.settings = settings

    def ask(self, req: LlmRequest) -> LlmResponse:
        t0 = time.monotonic()
        handler = getattr(self, f"_{req.task}", self._generic)
        data = handler(req)
        _validate(req.schema, data)
        return LlmResponse(data=data, raw=json.dumps(data, ensure_ascii=False), model=f"stub:{req.task}",
                           backend=self.backend, duration_ms=int((time.monotonic() - t0) * 1000))

    # ----------------------------------------------------------- extract
    def _extract(self, req: LlmRequest) -> dict[str, Any]:
        truth = load_truth(self.settings.truth_path, req.context.get("sha256"))
        grounded = "evidence" in req.schema.get("properties", {})
        if truth is not None:
            source = dict(truth["source"])
            lines = truth["source"]["lines"]
            chunk = req.context.get("chunk")
            if chunk:
                # Multi-request extraction: only the lines printed on this chunk's pages.
                pages = set(chunk["pages"])
                lines = [ln for ln in lines if ln.get("_page", 1) in pages]
            source["lines"] = [{k: v for k, v in ln.items() if not k.startswith("_")} for ln in lines]
            if grounded:
                source.setdefault("evidence", [])
            else:
                source.pop("evidence", None)
            return _fill_missing(source, req.schema)
        from aap.extraction.heuristic import heuristic_extract

        data = heuristic_extract("\n\n".join(req.context.get("page_texts") or []))
        if grounded:
            data["evidence"] = []
        return _fill_missing(data, req.schema)

    # ------------------------------------------------------------ choose
    def _choose(self, req: LlmRequest) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, sub in req.schema.get("properties", {}).items():
            if "enum" in sub:
                real = [v for v in sub["enum"] if v not in NON_CHOICES]
                out[key] = real[0] if real else sub["enum"][0]
            elif sub.get("type") == "string":
                out[key] = "stub backend: first candidate"
            elif sub.get("type") == "number":
                out[key] = 0.5
        return out

    def _generic(self, req: LlmRequest) -> dict[str, Any]:
        raise LlmError(f"stub backend has no handler for task {req.task!r}")


def _fill_missing(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Truth files and the regex fallback predate newer schema keys: answer those with null
    (an empty list for list keys), as the model does for a value that is not printed."""
    for key, sub in schema.get("properties", {}).items():
        data.setdefault(key, [] if sub.get("type") == "array" else None)
    return data


def load_truth(truth_dir: Path, sha256: str | None) -> dict[str, Any] | None:
    if not sha256:
        return None
    path = Path(truth_dir) / f"{sha256}.truth.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
