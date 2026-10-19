"""Payment terms, tolerance templates (IP-04: placeholder values) and tax rates."""

from __future__ import annotations

from datetime import datetime

from .context import SeedContext

START = datetime(2010, 1, 1)

# name, description, lines: (due_percent, due_days, due_day_of_month, months_fwd, disc%, disc_days)
TERMS = [
    ("IMMEDIATE", "Due upon receipt", [(100, 0, None, None, None, None)]),
    ("7 NET", "Net 7 days", [(100, 7, None, None, None, None)]),
    ("10 NET", "Net 10 days", [(100, 10, None, None, None, None)]),
    ("15 NET", "Net 15 days", [(100, 15, None, None, None, None)]),
    ("30 NET", "Net 30 days", [(100, 30, None, None, None, None)]),
    ("45 NET", "Net 45 days", [(100, 45, None, None, None, None)]),
    ("60 NET", "Net 60 days", [(100, 60, None, None, None, None)]),
    ("90 NET", "Net 90 days", [(100, 90, None, None, None, None)]),
    ("2/10 NET 30", "2% discount within 10 days, net 30", [(100, 30, None, None, 2, 10)]),
    ("1/15 NET 45", "1% discount within 15 days, net 45", [(100, 45, None, None, 1, 15)]),
    ("NET 30 EOM", "Due 30 days after end of month", [(100, None, 30, 1, None, None)]),
    ("50/50 30-60", "50% in 30 days, 50% in 60 days", [(50, 30, None, None, None, None), (50, 60, None, None, None, None)]),
]

# name, type, price%, qty%, qty_received%, description
TOLERANCES = [
    ("Standard", "GOODS", 2, 0, 0, "Standard goods tolerance"),
    ("Strict", "GOODS", 0, 0, 0, "No tolerance"),
    ("Services", "SERVICES", 5, 10, 10, "Services tolerance"),
]

# code, regime, tax, status, percentage
TAX_RATES = [
    ("US SALES EXEMPT", "US-SALES-TAX", "SALES TAX", "EXEMPT", 0),
    ("US SALES 6%", "US-SALES-TAX", "SALES TAX", "STANDARD", 6),
    ("US SALES 7.25%", "US-SALES-TAX", "SALES TAX", "STANDARD", 7.25),
    ("US SALES 8%", "US-SALES-TAX", "SALES TAX", "STANDARD", 8),
    ("US SALES 8.25%", "US-SALES-TAX", "SALES TAX", "STANDARD", 8.25),
    ("US SALES 8.875%", "US-SALES-TAX", "SALES TAX", "STANDARD", 8.875),
    ("GST 0%", "IN-GST", "GST", "STANDARD", 0),
    ("GST 5%", "IN-GST", "GST", "STANDARD", 5),
    ("GST 12%", "IN-GST", "GST", "STANDARD", 12),
    ("GST 18%", "IN-GST", "GST", "STANDARD", 18),
    ("GST 28%", "IN-GST", "GST", "STANDARD", 28),
    ("IGST 18%", "IN-GST", "IGST", "STANDARD", 18),
    ("VAT 19% DE", "EU-VAT", "VAT", "STANDARD", 19),
    ("VAT 20% GB", "GB-VAT", "VAT", "STANDARD", 20),
    ("VAT REVERSE CHARGE", "EU-VAT", "VAT", "REVERSE", 0),
]


def generate(ctx: SeedContext) -> None:
    ctx.ref["terms"] = {}
    for rank, (name, desc, lines) in enumerate(TERMS, start=1):
        term_id = ctx.next_id("ap.ap_terms_s")
        ctx.ref["terms"][name] = term_id
        ctx.add("ap.ap_terms_tl", term_id=term_id, language="US", source_lang="US", name=name,
                description=desc, enabled_flag="Y", type="STANDARD", rank=rank,
                start_date_active=START)
        for seq, (pct, days, dom, fwd, disc, disc_days) in enumerate(lines, start=1):
            ctx.add("ap.ap_terms_lines", term_id=term_id, sequence_num=seq, due_percent=pct,
                    due_days=days, due_day_of_month=dom, due_months_forward=fwd,
                    discount_percent=disc, discount_days=disc_days)

    ctx.ref["tolerances"] = {}
    for name, ttype, price, qty, qty_rec, desc in TOLERANCES:
        tol_id = ctx.next_id("ap.ap_tolerance_templates_s")
        ctx.ref["tolerances"][name] = tol_id
        ctx.add("ap.ap_tolerance_templates", tolerance_id=tol_id, tolerance_name=name,
                tolerance_type=ttype, description=desc, price_tolerance=price,
                quantity_tolerance=qty, qty_received_tolerance=qty_rec)

    ctx.ref["tax_rates"] = {}
    for code, regime, tax, status, pct in TAX_RATES:
        rate_id = ctx.next_id("zx.zx_rates_b_s")
        ctx.ref["tax_rates"][code] = pct
        ctx.add("zx.zx_rates_b", tax_rate_id=rate_id, tax_rate_code=code, tax_regime_code=regime,
                tax=tax, tax_status_code=status, percentage_rate=pct, active_flag="Y",
                default_rate_flag="N", effective_from=START)
