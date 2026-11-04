"""GL metrics (IMPLEMENTATION-PLAN-CLIENT-DATA P8.1): the accounts the Oracle-ready JSON carries
against the distributions AP keyed. Pure functions; the harness passes both in.

Scored only on non-PO invoices whose keyed record has distributions (client gold sets); synthetic
truths have no keyed lines, so they are skipped and the synthetic regression does not change.

- **Amount-weighted accuracy**, at invoice level: the keyed amount per account against the JSON's
  amount per account; the share of the keyed amount that landed on the right account. Compared per
  invoice, not per line, because AP may key one line for a multi-line invoice. Reported for the
  full combination and for the natural account alone (the cost center may be right or wrong on
  its own).
- **Lines coded without review**: ITEM / MISCELLANEOUS lines with an account and no `gl_review`
  issue in the JSON, i.e. what would post without a clerk looking. Their accuracy is the gate (the
  plan: >= 98 %, otherwise raise GL_SIM_AUTO). A line is paired with the keyed line of the same
  amount; when there is none and the invoice was keyed to a single account, that account is the
  answer; otherwise the line is counted as not scorable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aap.common.coa import CoaLayout

GL_LINE_TYPES = {"ITEM", "MISCELLANEOUS"}
ZERO = Decimal(0)


@dataclass
class GlLine:
    line_number: int
    amount: Decimal | None
    predicted: str | None
    expected: str | None            # None: no keyed account could be paired with this line
    auto: bool                      # coded without review


@dataclass
class GlResult:
    keyed_amount: Decimal = ZERO    # absolute keyed amount on GL lines
    full_amount_ok: Decimal = ZERO
    natural_amount_ok: Decimal = ZERO
    lines: list[GlLine] = field(default_factory=list)

    @property
    def full_equal(self) -> bool:
        return self.keyed_amount > 0 and self.full_amount_ok == self.keyed_amount


def _dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except ArithmeticError:
        return None


def keyed_accounts(expected: dict[str, Any]) -> dict[str, Decimal]:
    """Keyed amount per account over the GL lines' distributions (absolute amounts)."""
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for ln in expected.get("lines") or []:
        if (ln.get("line_type") or "ITEM") not in GL_LINE_TYPES:
            continue
        for d in ln.get("distributions") or []:
            amt = _dec(d.get("amount"))
            if d.get("account") and amt is not None:
                out[d["account"]] += abs(amt)
    return dict(out)


def _overlap(keyed: dict[str, Decimal], predicted: dict[str, Decimal]) -> Decimal:
    return sum((min(v, predicted.get(k, ZERO)) for k, v in keyed.items()), ZERO)


def _by(accounts: dict[str, Decimal], key) -> dict[str, Decimal]:  # noqa: ANN001
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for a, v in accounts.items():
        out[key(a) or a] += v
    return dict(out)


def gl_result(expected: dict[str, Any], oracle_json: dict[str, Any] | None, coa: CoaLayout) -> GlResult | None:
    """None when the invoice is not scored for GL (PO-matched, no keyed distributions, no JSON)."""
    if oracle_json is None or expected.get("po_matched"):
        return None
    keyed = keyed_accounts(expected)
    if not keyed:
        return None
    review_lines = {i["field"].split("]")[0] for i in (oracle_json.get("status") or {}).get("issues") or []
                    if i.get("code") == "gl_review"}
    predicted: dict[str, Decimal] = defaultdict(lambda: ZERO)
    json_lines = []
    for n, ln in enumerate(oracle_json.get("lines") or [], start=1):
        if ln.get("line_type_lookup_code") not in GL_LINE_TYPES or ln.get("account_source") != "line":
            continue
        amt, acct = _dec(ln.get("amount")), ln.get("dist_code_concatenated")
        if acct and amt is not None:
            predicted[acct] += abs(amt)
        json_lines.append((n, ln, amt, acct, f"lines[{n}" not in review_lines and bool(acct)))
    r = GlResult(keyed_amount=sum(keyed.values(), ZERO), full_amount_ok=_overlap(keyed, predicted),
                 natural_amount_ok=_overlap(_by(keyed, coa.natural_account), _by(predicted, coa.natural_account)))

    # keyed lines that carry a single account, for pairing by amount
    pool: list[tuple[Decimal, str]] = []
    for ln in expected.get("lines") or []:
        if (ln.get("line_type") or "ITEM") not in GL_LINE_TYPES:
            continue
        accts = {d.get("account") for d in ln.get("distributions") or [] if d.get("account")}
        amt = _dec(ln.get("amount"))
        if len(accts) == 1 and amt is not None:
            pool.append((abs(amt), accts.pop()))
    only = next(iter(keyed)) if len(keyed) == 1 else None
    for n, ln, amt, acct, auto in json_lines:
        hit = next((i for i, (a, _) in enumerate(pool) if amt is not None and a == abs(amt)), None)
        expected_acct = pool.pop(hit)[1] if hit is not None else only
        r.lines.append(GlLine(line_number=ln.get("line_number") or n, amount=amt, predicted=acct,
                              expected=expected_acct, auto=auto))
    return r


def summarize_gl(results: list[GlResult], coa: CoaLayout) -> dict[str, Any] | None:
    """The GL block of the eval metrics; None when no invoice was scored for GL."""
    if not results:
        return None

    def ratio(a: Decimal | int, b: Decimal | int) -> float | None:
        return round(float(a) / float(b), 4) if b else None

    keyed = sum((r.keyed_amount for r in results), ZERO)
    lines = [ln for r in results for ln in r.lines]
    auto = [ln for ln in lines if ln.auto]
    scored = [ln for ln in auto if ln.expected is not None]
    return {
        "invoices": len(results),
        "invoices_all_amount_on_keyed_account": ratio(sum(r.full_equal for r in results), len(results)),
        "amount_accuracy_full": ratio(sum((r.full_amount_ok for r in results), ZERO), keyed),
        "amount_accuracy_natural": ratio(sum((r.natural_amount_ok for r in results), ZERO), keyed),
        "lines": len(lines),
        "lines_uncoded": sum(1 for ln in lines if not ln.predicted),
        "no_review_share": ratio(len(auto), len(lines)),
        "no_review_accuracy_full": ratio(sum(ln.predicted == ln.expected for ln in scored), len(scored)),
        "no_review_accuracy_natural": ratio(sum(coa.natural_account(ln.predicted) == coa.natural_account(ln.expected)
                                                for ln in scored), len(scored)),
        "no_review_not_scorable": len(auto) - len(scored),
        "no_review_wrong": [f"line {ln.line_number}: {ln.predicted} (keyed {ln.expected})"
                            for ln in scored if ln.predicted != ln.expected][:50],
    }
