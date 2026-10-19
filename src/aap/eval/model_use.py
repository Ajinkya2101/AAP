"""python -m aap.eval.model_use [--eval-run <id>] [--include-eval] [--json out.json]

How often the pipeline had to ask the model, per decision point, on invoices the real model
extracted (stub runs are left out: the stub answers every choice with the first candidate).
This is the measurement to look at before fine-tuning a decision point: a node the model is
rarely asked at is not worth a tuned model.

Default: invoices that were uploaded. --eval-run: the invoices of one eval run
(python -m aap.eval --agents ...). --include-eval: both.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from aap.db.session import session_scope

# Decision point -> what one "unit" is, for the rate.
NODES = {
    "map.supplier": "invoices", "map.site": "invoices", "map.terms": "invoices", "map.category": "non-PO lines",
    "agent.pair_lines": "PO lines", "agent.gl_code": "non-PO lines",
}

SCOPE = """
WITH docs AS (
  SELECT d.id FROM document d
  WHERE d.file_type NOT IN ('eml', 'msg') AND {where}
    AND (SELECT e.backend FROM extraction_run e WHERE e.document_id = d.id AND e.status = 'ok'
           AND e.backend <> 'user' ORDER BY e.created_at DESC LIMIT 1) = 'vllm'
)"""


def collect(eval_run: uuid.UUID | None = None, include_eval: bool = False) -> dict[str, Any]:
    if eval_run:
        where, params = "d.uploaded_by = :by", {"by": f"eval:{eval_run}"}
    elif include_eval:
        where, params = "true", {}
    else:
        where, params = "d.source <> 'eval'", {}
    scope = SCOPE.format(where=where)
    with session_scope() as s:
        invoices = s.execute(text(scope + " SELECT count(*) FROM docs"), params).scalar() or 0
        # Lines of each invoice's newest mapped version; a line with a PO number is a PO line.
        po_lines, non_po_lines = s.execute(text(scope + """
            , latest AS (SELECT DISTINCT ON (v.document_id) v.canonical_invoice inv FROM invoice_version v
                         JOIN docs ON docs.id = v.document_id WHERE v.kind = 'mapped'
                         ORDER BY v.document_id, v.version DESC)
            SELECT count(*) FILTER (WHERE ln->'po_number'->>'value' IS NOT NULL),
                   count(*) FILTER (WHERE ln->'po_number'->>'value' IS NULL)
            FROM latest, jsonb_array_elements(inv->'lines') ln WHERE ln->>'line_type' = 'ITEM'"""), params).one()
        agents_ran = s.execute(text(scope + """ SELECT count(DISTINCT r.document_id) FROM agent_run r
            JOIN docs ON docs.id = r.document_id WHERE r.status = 'done'"""), params).scalar() or 0
        rows = s.execute(text(scope + """
            SELECT a.node, count(*) calls, count(DISTINCT a.document_id) docs,
                   count(*) FILTER (WHERE a.chosen->>'choice' IN ('none', 'review')) no_pick
            FROM agent_decision a JOIN docs ON docs.id = a.document_id
            WHERE a.prompt IS NOT NULL AND a.model NOT LIKE 'stub%' GROUP BY 1"""), params).all()
        fields = s.execute(text(scope + """
            , latest AS (SELECT DISTINCT ON (v.document_id) v.id FROM invoice_version v
                         JOIN docs ON docs.id = v.document_id WHERE v.kind = 'mapped'
                         ORDER BY v.document_id, v.version DESC)
            SELECT fe.method, count(*) FROM field_evidence fe JOIN latest ON latest.id = fe.invoice_version_id
            GROUP BY 1 ORDER BY 2 DESC"""), params).all()
    units = {"invoices": invoices, "PO lines": po_lines or 0, "non-PO lines": non_po_lines or 0}
    by_node = {r.node: r for r in rows}
    nodes = []
    for node, unit in NODES.items():
        r = by_node.get(node)
        calls = r.calls if r else 0
        nodes.append({"node": node, "unit": unit, "units": units[unit], "calls": calls, "invoices": r.docs if r else 0,
                      "no_pick": r.no_pick if r else 0,
                      "rate": round(calls / units[unit], 4) if units[unit] else None})
    return {"invoices": invoices, "agents_ran_on": agents_ran, "po_lines": units["PO lines"],
            "non_po_lines": units["non-PO lines"], "nodes": nodes,
            "field_methods": {m: n for m, n in fields}}


def render(m: dict[str, Any]) -> str:
    out = [f"Invoices extracted by the real model: {m['invoices']} "
           f"(agents ran on {m['agents_ran_on']}; {m['po_lines']} PO lines, {m['non_po_lines']} non-PO lines)", "",
           f"{'decision point':<18}{'model calls':>12}{'of':>8}  {'unit':<14}{'rate':>8}{'no pick':>9}{'invoices':>10}"]
    for n in m["nodes"]:
        rate = f"{n['rate'] * 100:.1f}%" if n["rate"] is not None else "-"
        out.append(f"{n['node']:<18}{n['calls']:>12}{n['units']:>8}  {n['unit']:<14}{rate:>8}{n['no_pick']:>9}"
                   f"{n['invoices']:>10}")
    total = sum(m["field_methods"].values())
    if total:
        out += ["", "How each field value was set (newest mapped version):"]
        out += [f"  {k:<12}{v:>8}  {v / total * 100:5.1f}%" for k, v in m["field_methods"].items()]
    if m["invoices"] and m["agents_ran_on"] < m["invoices"]:
        out += ["", f"Note: the agents ran on {m['agents_ran_on']} of {m['invoices']} invoices, so the agent.* rows "
                    "undercount."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aap.eval.model_use")
    p.add_argument("--eval-run", type=uuid.UUID, default=None, help="only the invoices of this eval run")
    p.add_argument("--include-eval", action="store_true", help="uploaded invoices and every eval run")
    p.add_argument("--json", type=Path, default=None)
    a = p.parse_args(argv)
    m = collect(a.eval_run, a.include_eval)
    print(render(m))
    if a.json:
        a.json.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
