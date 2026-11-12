"""Stub LLM backend (LLM_BACKEND=stub).

- extract:      returns the invoice generator's ground truth from
                <TRUTH_DIR>/<sha256>.truth.json; without a sidecar it falls back to a
                small regex extractor over the page text (header fields only).
- extract_taxes: the truth file's tax rows.
- choose:       picks the first real candidate of the enum (plan section 7).
- decide:       adapter B's answer from the context pack alone: site defaults, most used account.
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
            lines = truth["source"].get("lines") or []      # a client gold set has no printed lines
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

    def _extract_taxes(self, req: LlmRequest) -> dict[str, Any]:
        """The tax rows of the truth file (none without one: the re-read finds nothing new)."""
        truth = load_truth(self.settings.truth_path, req.context.get("sha256"))
        return _fill_missing({"taxes": list((truth or {}).get("source", {}).get("taxes") or [])}, req.schema)

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

    # ------------------------------------------------------------ decide
    def _decide(self, req: LlmRequest) -> dict[str, Any]:
        """Adapter B without a model: the site's defaults, and on every line the supplier's most used
        account (or its most used distribution set when it has no plain account), as a baseline that
        is right whenever a supplier always codes the same way."""
        props = req.schema["properties"]
        if "pack" not in req.context:          # a stored request (dataset): the facts are in its text
            from aap.decide.request import parse_user_text

            printed, packed = parse_user_text(req.content[0]["text"])
            req.context = {**req.context, "printed": printed, "pack": packed}
        pack = req.context.get("pack") or {}
        site = pack.get("site") or {}

        def allowed(key: str, value: Any, schema: dict[str, Any] | None = None) -> Any:
            return value if value in (schema or props[key])["enum"] else None

        item = props["lines"]["items"]["properties"]
        accounts = [a["account"] for a in pack.get("accounts") or [] if a["account"] in item["account"]["enum"]]
        sets = [s["name"] for s in pack.get("distribution_sets") or [] if s["name"] in item["distribution_set"]["enum"]]
        header = {"invoice_type_lookup_code": "CREDIT" if (req.context.get("printed") or {}).get("document_type")
                  in ("credit memo", "CREDIT") else "STANDARD",
                  "terms_name": site.get("terms"), "payment_method_code": site.get("payment_method"),
                  "pay_group_lookup_code": site.get("pay_group"), "liability_account": site.get("liability_account")}
        out: dict[str, Any] = {k: allowed(k, v) for k, v in header.items()}
        out["basis"] = {k: (("printed" if k == "invoice_type_lookup_code" else "site_default")
                            if out[k] is not None else None) for k in header}
        lines = []
        for ln in (req.context.get("printed") or {}).get("lines") or []:
            kind = allowed("line_type_lookup_code", ln.get("printed_as") or "ITEM", item["line_type_lookup_code"])
            account = accounts[0] if accounts else None
            dist_set = sets[0] if not account and sets else None
            lines.append({"line_number": ln["line_number"], "line_type_lookup_code": kind or "ITEM",
                          "account": account, "distribution_set": dist_set,
                          "basis": "history" if account else "distribution_set" if dist_set else None})
        out["lines"] = lines
        return out

    def _generic(self, req: LlmRequest) -> dict[str, Any]:
        raise LlmError(f"stub backend has no handler for task {req.task!r}")


def _fill_missing(data: dict[str, Any], schema: dict[str, Any], root: dict[str, Any] | None = None) -> dict[str, Any]:
    """Truth files and the regex fallback predate newer schema keys: answer those with null
    (an empty list for list keys), as the model does for a value that is not printed. Also inside
    the items of a list (a line's po_number)."""
    root = root or schema
    for key, sub in schema.get("properties", {}).items():
        data.setdefault(key, [] if sub.get("type") == "array" else None)
        ref = (sub.get("items") or {}).get("$ref")
        if ref and isinstance(data[key], list):
            item_schema = root.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})
            for item in data[key]:
                if isinstance(item, dict):
                    _fill_missing(item, item_schema, root)
    return data


def load_truth(truth_dir: Path, sha256: str | None) -> dict[str, Any] | None:
    if not sha256:
        return None
    path = Path(truth_dir) / f"{sha256}.truth.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
