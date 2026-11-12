"""The context pack (PLAN-MODEL-JSON §4): the facts the model gets with an invoice so it can choose the
Oracle fields instead of remembering them.

    pack = build_context_pack(store, vendor_id=..., vendor_site_id=..., as_of=<GL date>,
                              exclude_invoice_ids={<the invoice itself>}, printed_store="Store 0316")

**The "as of" rule.** Only history keyed strictly before `as_of` (the invoice's GL date; its invoice
date when the GL date is unknown) is used, and never the invoice itself (`exclude_invoice_ids`).
Without it a training or test example would contain its own answer. `leaks()` checks a built pack.

Parts, with their limits:

| Part | Content | Limit |
|---|---|---|
| site | supplier number and name, site code, OU, currency, terms, pay group, payment method, liability account, distribution set | 1 site |
| accounts | accounts used before the date on lines without a distribution set: share of amount, invoices, last used, typical line texts | top 15 by amount |
| distribution_sets | sets used before the date (or the site's default), with the accounts their lines were keyed to | all of the supplier's |
| recent_invoices | the supplier's last keyed invoices: date, type, amount, lines with account or set | last 5 |
| lists | the instance's enabled invoice types, line types, sources, terms, payment methods, pay groups | – |
| stores | store table rows matching the printed store | matching rows |

History is the site's when the site has any, else the supplier's (`history_scope`). Without any
history, `accounts` falls back to the most used accounts across suppliers and `fallback` is true:
those lines are never `ready` on a guess.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .history import HistLine, HistoryStore, store_key

CONTEXT_PACK_VERSION = "kk/1"
TOP_ACCOUNTS = 15
RECENT_INVOICES = 5
TEXT_SAMPLES = 3
ZERO = Decimal(0)


def _money(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01")))


def _share(part: Decimal, whole: Decimal) -> float | None:
    return round(float(part / whole), 3) if whole else None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def usable_history(store: HistoryStore, vendor_id: int, as_of: date, exclude: set[int]) -> list[HistLine]:
    """The supplier's history the rule allows: dated strictly before `as_of`, not an excluded invoice."""
    return [ln for ln in store.lines_of(vendor_id)
            if ln.date is not None and ln.date < as_of and (ln.invoice_id is None or ln.invoice_id not in exclude)]


def build_context_pack(store: HistoryStore, *, vendor_id: int, vendor_site_id: int | None, as_of: date,
                       exclude_invoice_ids: set[int] | frozenset[int] = frozenset(),
                       printed_store: str | None = None) -> dict[str, Any]:
    exclude = set(exclude_invoice_ids)
    hist = usable_history(store, vendor_id, as_of, exclude)
    site_hist = [ln for ln in hist if vendor_site_id is not None and ln.vendor_site_id == vendor_site_id]
    scope, used = ("site", site_hist) if site_hist else ("supplier", hist) if hist else ("none", [])
    non_tax = [ln for ln in used if not ln.is_tax]
    total = sum((abs(ln.amount) for ln in non_tax), ZERO)

    accounts = _accounts([ln for ln in non_tax if not ln.distribution_set_id], total, store)
    fallback = not non_tax
    if fallback:
        accounts = [{"account": a, "description": store.account_descriptions.get(a), "share": None,
                     "invoices": n, "last_used": None, "typical_text": [], "fallback": True}
                    for a, _, n in store.fallback_accounts(as_of, exclude, TOP_ACCOUNTS)]
    site = store.sites.get(vendor_site_id) if vendor_site_id is not None else None
    supplier = store.suppliers.get(vendor_id)
    return {
        "context_pack_version": CONTEXT_PACK_VERSION,
        "as_of": as_of.isoformat(),
        "history_scope": scope,
        "fallback": fallback,
        "site": _site(supplier, site, store),
        "accounts": accounts,
        "distribution_sets": _sets([ln for ln in non_tax if ln.distribution_set_id], total, store,
                                   site.distribution_set_id if site else None),
        "recent_invoices": _recent(used),
        "lists": {k: list(v) for k, v in sorted(store.lists.items())},
        "stores": _stores(store, printed_store),
    }


def _site(supplier: Any, site: Any, store: HistoryStore) -> dict[str, Any]:
    def pick(name: str) -> Any:
        v = getattr(site, name, None) if site else None
        return v if v is not None else getattr(supplier, name, None) if supplier else None

    dist_set = site.distribution_set_id if site else None
    return {"vendor_number": supplier.vendor_number if supplier else None,
            "vendor_name": supplier.vendor_name if supplier else None,
            "vendor_site_code": site.vendor_site_code if site else None,
            "operating_unit": site.operating_unit if site else None,
            "currency": pick("currency"), "terms": pick("terms"), "pay_group": pick("pay_group"),
            "payment_method": pick("payment_method"),
            "liability_account": site.liability_account if site else None,
            "distribution_set": _set_name(store, dist_set) if dist_set else None,
            "terms_date_basis": site.terms_date_basis if site else None}


def _set_name(store: HistoryStore, set_id: int) -> str:
    ds = store.distribution_sets.get(set_id)
    return ds.name if ds and ds.name else f"set {set_id}"


def _accounts(lines: list[HistLine], total: Decimal, store: HistoryStore) -> list[dict[str, Any]]:
    amount: Counter = Counter()
    invoices: dict[str, set] = defaultdict(set)
    last: dict[str, date] = {}
    texts: dict[str, Counter] = defaultdict(Counter)
    for ln in lines:
        if not ln.account:
            continue
        amount[ln.account] += abs(ln.amount)
        invoices[ln.account].add(ln.invoice_id)
        if ln.date and (ln.account not in last or ln.date > last[ln.account]):
            last[ln.account] = ln.date
        if ln.description.strip():
            texts[ln.account][ln.description.strip()] += 1
    ranked = sorted(amount, key=lambda a: (-amount[a], a))[:TOP_ACCOUNTS]
    return [{"account": a, "description": store.account_descriptions.get(a), "share": _share(amount[a], total),
             "invoices": len(invoices[a]), "last_used": _iso(last.get(a)),
             "typical_text": [t for t, _ in texts[a].most_common(TEXT_SAMPLES)], "fallback": False}
            for a in ranked]


def _sets(lines: list[HistLine], total: Decimal, store: HistoryStore, site_default: int | None) -> list[dict[str, Any]]:
    amount: Counter = Counter()
    invoices: dict[int, set] = defaultdict(set)
    last: dict[int, date] = {}
    keyed: dict[int, Counter] = defaultdict(Counter)
    for ln in lines:
        sid = ln.distribution_set_id
        amount[sid] += abs(ln.amount)
        invoices[sid].add(ln.invoice_id)
        if ln.date and (sid not in last or ln.date > last[sid]):
            last[sid] = ln.date
        if ln.account:
            keyed[sid][ln.account] += abs(ln.amount)
    ids = sorted(amount, key=lambda s: (-amount[s], s))
    if site_default and site_default not in amount:
        ids.append(site_default)
    out = []
    for sid in ids:
        ds = store.distribution_sets.get(sid)
        set_total = sum(keyed[sid].values(), ZERO)
        out.append({"name": _set_name(store, sid), "description": ds.description if ds else None,
                    "share": _share(amount[sid], total) if sid in amount else None, "invoices": len(invoices[sid]),
                    "last_used": _iso(last.get(sid)), "site_default": sid == site_default,
                    "lines": list(ds.lines) if ds else [],
                    "keyed_accounts": [{"account": a, "share": _share(v, set_total)}
                                       for a, v in keyed[sid].most_common()]})
    return out


def _recent(lines: list[HistLine]) -> list[dict[str, Any]]:
    by_invoice: dict[Any, list[HistLine]] = defaultdict(list)
    for ln in lines:
        by_invoice[ln.invoice_id if ln.invoice_id is not None else (ln.invoice_num, ln.date)].append(ln)
    ordered = sorted(by_invoice.values(), key=lambda g: (max(x.date for x in g if x.date), str(g[0].invoice_num)),
                     reverse=True)[:RECENT_INVOICES]
    out = []
    for g in ordered:
        first = g[0]
        per_line: dict[Any, list[HistLine]] = defaultdict(list)
        for ln in g:
            per_line[ln.line_number].append(ln)
        out.append({
            "invoice_num": first.invoice_num, "invoice_date": _iso(first.invoice_date), "gl_date": _iso(first.gl_date),
            "invoice_type": first.invoice_type, "amount": _money(sum((x.amount for x in g), ZERO)),
            "lines": [{"line_number": n, "line_type": ls[0].line_type,
                       "amount": _money(sum((x.amount for x in ls), ZERO)),
                       "description": ls[0].description or None,
                       "distribution_set_id": ls[0].distribution_set_id,
                       "accounts": sorted({x.account for x in ls if x.account})}
                      for n, ls in sorted(per_line.items(), key=lambda kv: kv[0] or 0)]})
    return out


def _stores(store: HistoryStore, printed: str | None) -> list[dict[str, str]]:
    key = store_key(printed)
    if key is None:
        return []
    return [{"store": s, "cost_center": cc} for s, cc in sorted(store.stores.items()) if store_key(s) == key]


def leaks(pack: dict[str, Any], *, as_of: date, invoice_num: str | None = None) -> list[str]:
    """What in a built pack breaks the "as of" rule: a date on or after `as_of`, or the invoice itself
    among the recent invoices. Empty when the pack is clean (tests and the dataset builder check it)."""
    problems = []
    for a in pack.get("accounts") or []:
        if a.get("last_used") and a["last_used"] >= as_of.isoformat():
            problems.append(f"account {a['account']} last used {a['last_used']}")
    for s in pack.get("distribution_sets") or []:
        if s.get("last_used") and s["last_used"] >= as_of.isoformat():
            problems.append(f"set {s['name']} last used {s['last_used']}")
    for inv in pack.get("recent_invoices") or []:
        d = inv.get("gl_date") or inv.get("invoice_date")
        if d and d >= as_of.isoformat():
            problems.append(f"recent invoice {inv['invoice_num']} dated {d}")
        if invoice_num is not None and inv.get("invoice_num") == invoice_num:
            problems.append(f"the invoice itself ({invoice_num}) is in the pack")
    return problems
