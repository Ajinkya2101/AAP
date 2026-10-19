"""Plain-text eval report."""

from __future__ import annotations

import uuid
from typing import Any


def _pct(v: float | None) -> str:
    return "  -  " if v is None else f"{v * 100:5.1f}%"


def render(run_id: uuid.UUID, name: str, m: dict[str, Any]) -> str:
    tiers = [k for k in m if k not in ("gates",)]
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
    for g, v in m["gates"].items():
        out.append(f"gate {g}: {_pct(v['value'])} (threshold {v['threshold'] * 100:.0f}%) -> "
                   f"{'PASS' if v['pass'] else 'FAIL'}")
    if m["all"]["failed"]:
        out.append(f"not VALIDATED: {', '.join(m['all']['failed'])}")
    return "\n".join(out)
