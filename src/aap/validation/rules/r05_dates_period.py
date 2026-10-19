"""C9 / V-07 invoice date sensible; V-GL GL date in an open AP period."""

from __future__ import annotations

from datetime import timedelta

from aap.config import get_settings
from aap.schemas.fields import Flag

from ..engine import FAIL, PASS, SKIP, WARN, RuleContext, RuleResult, result, rule


@rule("C9", "Invoice date sensible")
def invoice_date(ctx: RuleContext) -> list[RuleResult]:
    f = ctx.inv.invoice_date
    if Flag.two_date_formats in f.flags:
        return [result("C9", "Invoice date sensible", WARN,
                       f"{f.source_value!r} can be read two ways (day/month); check the date", "invoice_date")]
    if f.value is None:
        return [result("C9", "Invoice date sensible", SKIP, "no invoice date")]
    d = f.value
    if d > ctx.today + timedelta(days=1):
        return [result("C9", "Invoice date sensible", FAIL, f"invoice date {d} is in the future", "invoice_date")]
    max_age = get_settings().invoice_max_age_days
    if d < ctx.today - timedelta(days=max_age):
        return [result("C9", "Invoice date sensible", WARN, f"invoice date {d} is older than {max_age} days",
                       "invoice_date")]
    return [result("C9", "Invoice date sensible", PASS)]


@rule("V-GL", "GL date in open period")
def gl_period(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    gl_date = inv.gl_date.value
    ou = ctx.ref.operating_units.get(inv.org_id.value) if inv.org_id.value else None
    if gl_date is None or ou is None:
        return [result("V-GL", "GL date in open period", SKIP, "GL date or operating unit unknown")]
    period = next((p for p in ctx.ref.periods if p.ledger_id == ou.ledger_id and p.start_date <= gl_date <= p.end_date),
                  None)
    if period is None:
        return [result("V-GL", "GL date in open period", FAIL, f"no AP period for {gl_date}", "gl_date")]
    if period.closing_status not in ("O", "F"):
        return [result("V-GL", "GL date in open period", FAIL,
                       f"period {period.period_name} is not open (status {period.closing_status})", "gl_date")]
    return [result("V-GL", "GL date in open period", PASS, period.period_name)]
