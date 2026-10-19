"""C11 bank details unchanged: printed bank details must match the supplier master
(IBY_EXT_BANK_ACCOUNTS). A mismatch is a fraud flag (bank-change scam)."""

from __future__ import annotations

from ..engine import FAIL, PASS, SKIP, RuleContext, RuleResult, result, rule


@rule("C11", "Bank details unchanged")
def bank_details(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    acct, iban = inv.bank_account.value, inv.iban.value
    if not acct and not iban:
        return [result("C11", "Bank details unchanged", SKIP, "no bank details printed")]
    vendor_id = inv.vendor_id.value
    if not vendor_id:
        return [result("C11", "Bank details unchanged", FAIL, "bank details printed but supplier unknown",
                       "bank_account", fraud=True)]
    banks = [b for b in ctx.ref.banks_by_vendor.get(vendor_id, []) if b.end_date is None or b.end_date > ctx.today]
    for b in banks:
        master_acct = "".join(ch for ch in (b.bank_account_num or "") if ch.isdigit())
        if iban and b.iban and iban.replace(" ", "").upper() == b.iban.replace(" ", "").upper():
            return [result("C11", "Bank details unchanged", PASS)]
        if acct and master_acct and (acct == master_acct or acct.endswith(master_acct) or master_acct.endswith(acct)):
            return [result("C11", "Bank details unchanged", PASS)]
    shown = iban or acct
    return [result("C11", "Bank details unchanged", FAIL,
                   f"bank account {shown} is not on the supplier master: possible bank-change fraud, "
                   "confirm with the supplier by phone before paying", "bank_account", "iban", fraud=True)]
