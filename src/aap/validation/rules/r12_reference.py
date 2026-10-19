"""V-REF reference data age (ARCHITECTURE 8.3); V-SENDER unknown sender (S15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aap.refsync.sync import last_sync
from aap.schemas.fields import Flag

from ..engine import PASS, WARN, RuleContext, RuleResult, result, rule


@rule("V-REF", "Reference data current")
def reference_age(ctx: RuleContext) -> list[RuleResult]:
    run = last_sync(ctx.session)
    if run is None or run.finished_at is None:
        return [result("V-REF", "Reference data current", WARN, "reference data was never synchronised")]
    age = datetime.now(UTC) - run.finished_at
    if age > timedelta(hours=48):
        return [result("V-REF", "Reference data current", WARN,
                       f"last reference sync was {age.days} day(s) ago")]
    return [result("V-REF", "Reference data current", PASS)]


@rule("V-SENDER", "Known sender")
def known_sender(ctx: RuleContext) -> list[RuleResult]:
    if Flag.unknown_sender in (ctx.document.flags or []):
        return [result("V-SENDER", "Known sender", WARN,
                       "the sender's email domain is not on any supplier site: possible spoofing")]
    return [result("V-SENDER", "Known sender", PASS)]
