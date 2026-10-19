"""V-FX: a foreign-currency invoice needs a Corporate rate for its date (live)."""

from __future__ import annotations

from ..engine import PASS, SKIP, WARN, RuleContext, RuleResult, result, rule


@rule("V-FX", "Exchange rate available")
def exchange_rate(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    cur, ledger_cur, d = inv.currency_code.value, inv.ledger_currency.value, inv.invoice_date.value
    if not cur or not ledger_cur or cur == ledger_cur:
        return [result("V-FX", "Exchange rate available", SKIP, "invoice in ledger currency")]
    if d is None or ctx.adapter is None:
        return [result("V-FX", "Exchange rate available", WARN, "cannot check the exchange rate")]
    rate = ctx.adapter.get_exchange_rate(cur, ledger_cur, d)
    if rate is None:
        return [result("V-FX", "Exchange rate available", WARN,
                       f"no Corporate rate {cur}->{ledger_cur} for {d}; enter a rate before approval",
                       "currency_code")]
    return [result("V-FX", "Exchange rate available", PASS, f"{cur}->{ledger_cur} {rate} on {d}", rate=str(rate))]
