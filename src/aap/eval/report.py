"""Plain-text eval report."""

from __future__ import annotations

import uuid
from typing import Any

NOT_TIERS = {"gates", "by_supplier", "holdout", "system"}


def _pct(v: float | None) -> str:
    return "  -  " if v is None else f"{v * 100:5.1f}%"


def _shadow(sh: dict[str, Any]) -> list[str]:
    out = ["Oracle-ready JSON vs the truth (shadow comparison)",
           f"  invoices {sh['invoices']}   equal {_pct(sh['equal'])}   ready {_pct(sh['ready'])}   "
           f"ready and equal {_pct(sh['ready_and_equal'])}",
           "  states: " + ", ".join(f"{k} {v}" for k, v in sorted(sh["states"].items()))]
    out += [f"  {k:26s}{_pct(v):>8s}" for k, v in sh["fields"].items()]
    if sh["ready_but_different"]:
        out.append("  READY BUT DIFFERENT (would have been created wrong): " + ", ".join(sh["ready_but_different"]))
    return out


def _gl(g: dict[str, Any]) -> list[str]:
    out = ["GL coding vs the keyed distributions (non-PO invoices)",
           f"  invoices {g['invoices']}   all amount on the keyed account {_pct(g['invoices_all_amount_on_keyed_account'])}",
           f"  amount on the right account: full combination {_pct(g['amount_accuracy_full'])}, "
           f"natural account {_pct(g['amount_accuracy_natural'])}",
           f"  lines {g['lines']} (no account {g['lines_uncoded']}), coded without review {_pct(g['no_review_share'])}",
           f"  lines coded without review correct: full {_pct(g['no_review_accuracy_full'])}, "
           f"natural {_pct(g['no_review_accuracy_natural'])} (not scorable {g['no_review_not_scorable']})"]
    out += [f"    wrong without review: {w}" for w in g["no_review_wrong"][:10]]
    return out


def _supplier_table(by: dict[str, dict[str, Any]], held: set[str]) -> list[str]:
    cols = ["docs", "header", "line F1", "JSON =", "ready", "GL amt", "GL auto ok"]
    out = ["Per supplier", f"  {'supplier':14s}" + "".join(f"{c:>11s}" for c in cols)]
    for v, b in by.items():
        sh, g = b.get("shadow") or {}, b.get("gl") or {}
        cells = [f"{b['documents']:>11d}", f"{_pct(b['header_accuracy']):>11s}", f"{_pct(b['line_f1']):>11s}",
                 f"{_pct(sh.get('equal')):>11s}", f"{_pct(sh.get('ready')):>11s}",
                 f"{_pct(g.get('amount_accuracy_full')):>11s}", f"{_pct(g.get('no_review_accuracy_full')):>11s}"]
        out.append(f"  {(v + (' *' if v in held else '')):14s}" + "".join(cells))
    if held:
        out.append("  * held-out supplier (never in training or history)")
    return out


def render(run_id: uuid.UUID, name: str, m: dict[str, Any]) -> str:
    tiers = [k for k in m if k not in NOT_TIERS]
    out = [f"Eval run {name} ({run_id})", ""]
    out.append(f"{'':28s}" + "".join(f"{t:>12s}" for t in tiers))
    for key in ("documents", "reached_validated"):
        out.append(f"{key:28s}" + "".join(f"{m[t][key]:>12d}" for t in tiers))
    for key in ("header_accuracy", "line_precision", "line_recall", "line_f1"):
        out.append(f"{key:28s}" + "".join(f"{_pct(m[t][key]):>12s}" for t in tiers))
    out.append(f"{'line_values_not_on_invoice':28s}" + "".join(f"{m[t]['line_values_not_on_invoice']:>12d}" for t in tiers))
    out.append(f"{'extract_ms_median':28s}" + "".join(f"{m[t]['extract_ms_median'] or 0:>12d}" for t in tiers))
    out.append("")
    for f in m["all"]["fields"]:
        out.append(f"{f:28s}" + "".join(f"{_pct(m[t]['fields'].get(f)):>12s}" for t in tiers))
    out.append("")
    if m["all"].get("shadow"):
        out += _shadow(m["all"]["shadow"]) + [""]
    if m["all"].get("gl"):
        out += _gl(m["all"]["gl"]) + [""]
    if m["all"].get("decide"):
        d = m["all"]["decide"]
        out += [f"Adapter B ({m.get('system') or 'decide'}): {d['invoices']} invoices, agrees with the code on "
                f"{_pct(d['agree_invoices'])} of invoices and {_pct(d['agree_lines'])} of {d['lines']} lines; "
                f"{d['no_account_lines']} line(s) without an account; {d['fallback_invoices']} invoice(s) without "
                f"history" + (f"; header differences: {d['header_differences']}" if d["header_differences"] else ""),
                ""]
    if m.get("holdout"):
        h = m["holdout"]
        out += [f"Held-out suppliers: {h['documents']} invoices, header {_pct(h['header_accuracy'])}, "
                f"line F1 {_pct(h['line_f1'])}, JSON equal {_pct((h.get('shadow') or {}).get('equal'))}", ""]
    if m.get("by_supplier"):
        out += _supplier_table(m["by_supplier"], set((m.get("holdout") or {}).get("suppliers") or [])) + [""]
    for g, v in m["gates"].items():
        out.append(f"gate {g}: {_pct(v['value'])} (threshold {v['threshold'] * 100:.0f}%) -> "
                   f"{'PASS' if v['pass'] else 'FAIL'}")
    if m["all"]["failed"]:
        out.append(f"not VALIDATED: {', '.join(m['all']['failed'])}")
    return "\n".join(out)
