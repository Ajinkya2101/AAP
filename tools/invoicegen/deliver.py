"""Deliver a generated set to the pipeline.

    python -m tools.invoicegen.deliver --dir /data/invoicegen --api http://api:8080      (via the API + worker)
    python -m tools.invoicegen.deliver --dir /data/invoicegen --inline                   (in-process, no worker)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.invoicegen.deliver")
    p.add_argument("--dir", type=Path, default=Path("/data/invoicegen"))
    p.add_argument("--api", default=None, help="API base URL; omit with --inline")
    p.add_argument("--inline", action="store_true", help="run the pipeline in this process")
    p.add_argument("--only", default=None, help="comma-separated scenario ids, e.g. S1,S4")
    a = p.parse_args(argv)
    manifest = json.loads((a.dir / "manifest.json").read_text(encoding="utf-8"))
    only = set(a.only.split(",")) if a.only else None
    sent = 0
    for item in manifest["items"]:
        scen = {d["expected"].get("scenario") for d in item["documents"]} | {item.get("expected", {}).get("scenario")}
        if only and not (scen & only):
            continue
        path = a.dir / item["file"]
        data = path.read_bytes()
        if a.inline:
            from aap.db.session import session_scope
            from aap.ingest.intake import intake
            from aap.pipeline.stages import run_pipeline_inline

            with session_scope() as s:
                res = intake(s, data, filename=path.name, source="upload", uploaded_by="invoicegen")
                doc_id = res.document.id
            run_pipeline_inline(doc_id)
        else:
            import httpx

            r = httpx.post(f"{a.api.rstrip('/')}/api/documents", files={"file": (path.name, data)}, timeout=60)
            r.raise_for_status()
        sent += 1
    print(f"delivered {sent} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
