"""Stage 8 Validate (ARCHITECTURE 6.6, F-07).

Each rule returns pass / warning / fail (or skip when it does not apply).
fail blocks approval; warning must be examined. Rules are plain code: the LLM
does no validation (P-03).

Rule ids: MVP V-01..V-07 and WORKED-EXAMPLE C1..C12 where they coincide:
  C1  required fields (V-01)        C7  tax ID matches supplier master
  C2  line maths                    C8  supplier and site active (V-04)
  C3  lines add up to subtotal      C9  invoice date sensible (V-07)
  C4  tax maths                     C10 duplicate invoice (V-06)
  C5  grand total (V-03)            C11 bank details unchanged (fraud)
  C6  tax ID format / checksum      C12 source check
  V-05 mapping complete  V-PO purchase order  V-GL open GL period
  V-FX exchange rate     V-REF reference data age  V-SENDER known sender
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from aap.adapters.base import ErpReadPort
from aap.db.models import Document
from aap.mapping.reference import Reference
from aap.schemas.canonical import CanonicalInvoice

PASS, WARN, FAIL, SKIP = "pass", "warning", "fail", "skip"


@dataclass
class RuleResult:
    rule_id: str
    name: str
    result: str
    message: str = ""
    field_paths: list[str] = field(default_factory=list)
    details: dict[str, Any] | None = None


@dataclass
class RuleContext:
    inv: CanonicalInvoice
    session: Session
    ref: Reference
    adapter: ErpReadPort | None
    document: Document
    today: date


Rule = Callable[[RuleContext], list[RuleResult]]
RULES: list[tuple[str, str, Rule]] = []


def rule(rule_id: str, name: str) -> Callable[[Rule], Rule]:
    def deco(fn: Rule) -> Rule:
        RULES.append((rule_id, name, fn))
        return fn
    return deco


def result(rule_id: str, name: str, outcome: str, message: str = "", *paths: str, **details: Any) -> RuleResult:
    return RuleResult(rule_id=rule_id, name=name, result=outcome, message=message, field_paths=list(paths),
                      details=details or None)


def run_rules(ctx: RuleContext) -> list[RuleResult]:
    from . import rules  # noqa: F401  (registers the rules)

    out: list[RuleResult] = []
    for rule_id, name, fn in RULES:
        try:
            out.extend(fn(ctx))
        except Exception as exc:  # noqa: BLE001 - a broken rule must be visible, not silent
            out.append(RuleResult(rule_id=rule_id, name=name, result=FAIL,
                                  message=f"rule error: {type(exc).__name__}: {exc}"))
    return out


def summarize(results: list[RuleResult]) -> dict[str, list[str]]:
    return {
        "fail": sorted({r.rule_id for r in results if r.result == FAIL}),
        "warning": sorted({r.rule_id for r in results if r.result == WARN}),
    }
