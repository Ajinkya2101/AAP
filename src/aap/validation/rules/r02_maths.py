"""C2 line maths, C3 lines = subtotal, C4 tax maths, C5 / V-03 grand total."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..engine import FAIL, PASS, SKIP, RuleContext, RuleResult, result, rule

CENT = Decimal("0.01")


def _tol(ctx: RuleContext, n: int = 1) -> Decimal:
    from aap.config import get_settings

    return get_settings().amount_tolerance * n


def q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


@rule("C2", "Line maths")
def line_maths(ctx: RuleContext) -> list[RuleResult]:
    bad, checked = [], 0
    for ln in ctx.inv.item_lines():
        qty, price, amount = ln.quantity.value, ln.unit_price.value, ln.amount.value
        if qty is None or price is None or amount is None:
            continue
        checked += 1
        expected = q(Decimal(qty) * Decimal(price))
        if abs(expected - Decimal(amount)) > _tol(ctx):
            bad.append((ln.line_number, str(qty), str(price), str(amount), str(expected)))
    if bad:
        msg = "; ".join(f"line {n}: {qt} x {p} = {e}, invoice shows {a}" for n, qt, p, a, e in bad)
        return [result("C2", "Line maths", FAIL, msg, *[f"lines[{b[0]}].amount" for b in bad])]
    if not checked:
        return [result("C2", "Line maths", SKIP, "no lines with quantity and unit price")]
    return [result("C2", "Line maths", PASS, f"{checked} line(s) checked")]


@rule("C3", "Lines add up to subtotal")
def lines_subtotal(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    lines = inv.item_lines()
    if not lines or inv.subtotal.value is None or any(ln.amount.value is None for ln in lines):
        return [result("C3", "Lines add up to subtotal", SKIP, "subtotal or line amounts not available")]
    total = sum((Decimal(ln.amount.value) for ln in lines), Decimal(0))
    if abs(total - Decimal(inv.subtotal.value)) > _tol(ctx, len(lines)):
        return [result("C3", "Lines add up to subtotal", FAIL,
                       f"lines add up to {q(total)}, subtotal is {inv.subtotal.value}", "subtotal")]
    return [result("C3", "Lines add up to subtotal", PASS)]


@rule("C4", "Tax maths")
def tax_maths(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    rate, sub, tax = inv.tax_rate.value, inv.subtotal.value, inv.tax_amount.value
    if rate is None or sub is None or tax is None:
        return [result("C4", "Tax maths", SKIP, "no printed tax rate")]
    expected = q(Decimal(sub) * Decimal(rate) / 100)
    if abs(expected - Decimal(tax)) > _tol(ctx):
        return [result("C4", "Tax maths", FAIL, f"{sub} x {rate}% = {expected}, invoice shows tax {tax}",
                       "tax_amount", expected=str(expected), printed=str(tax))]
    return [result("C4", "Tax maths", PASS)]


@rule("C5", "Grand total")
def grand_total(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    total = inv.invoice_amount.value
    if total is None:
        return [result("C5", "Grand total", SKIP, "no total")]
    base = inv.subtotal.value
    if base is None:
        return [result("C5", "Grand total", SKIP, "no subtotal")]
    expected = Decimal(base) + Decimal(inv.tax_amount.value or 0) + Decimal(inv.freight_amount.value or 0)
    if abs(q(expected) - Decimal(total)) > _tol(ctx):
        return [result("C5", "Grand total", FAIL,
                       f"subtotal + tax + freight = {q(expected)}, total is {total}", "invoice_amount")]
    return [result("C5", "Grand total", PASS)]
