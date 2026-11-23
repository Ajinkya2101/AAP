"""Invoice files for keyed invoices (real or synthetic) in their supplier's layout (PLAN-MODEL-JSON T2c).

    python -m tools.invoicegen.keyed --dataset test-data-kk/<adapter B dataset> \
        --from client-data/kk/<earlier> --delivery client-data/kk/<delivery> \
        --templates client-data/kk/invoice-templates --out test-data-kk/<invoice set> [--workers 6] [--limit N]

Every example of an adapter B dataset (aap.finetune.chat decide) gets a PDF, except the invoices with a
real file (`has_file`): the real invoices from the deliveries, the synthetic ones from the dataset's
`synthetic/*.csv`. Runs where WeasyPrint runs (the app image).

What is printed comes from the keyed record and the supplier's profile, never from anything else:

- the invoice number, date and total as keyed; the due date and terms text as the supplier prints them
  (profile: `due_days`, `terms_text`; the keyed terms are the site default, not what is printed);
- the items: one block per non-zero keyed line when there are several, else the line amount broken
  into the supplier's usual items (profile `items`, summing exactly to the line); printed tax is what
  the header has above the lines;
- the location (ship-to / consignee / client) from the cost-center segment of the line's account:
  a store prints as the store with its city and state, any other cost center as the party the profile
  names for it (`parties`), else its description. This is how the real samples look (2026-11-23: on all
  20 sample invoices the printed ship-to is the keyed cost center), and it is the reason scores on these
  files are reported apart from the real files;
- decorations (BOL, order and PO numbers, weights, routes): seeded by the invoice ID, noise only.

Supplier templates and profiles are client data and live outside git (`--templates`):
`<dir>/profiles.json` = {"<supplier number>": {"template": "x.html.j2", "scanned": false, ...}, "default": {...}}
and the Jinja templates next to it. A template gets `inv` (the printed values), `vendor`, `site`,
`location`, `profile` and `fake` (seeded decorations), plus the filters `money` and `d` (dates).

Output (the eval format, like aap.clientdata.goldset): `pdf/<invoice_id>.pdf`, `truth/<sha>.truth.json`
(source = the printed values, expected = the keyed values), `manifest.json` (split, label, supplier,
parent, layout, scanned; the real-file invoices listed with `real_file: true` and no PDF).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

CENT = Decimal("0.01")
STREETS = ["Main St", "Oak Ave", "Commerce Dr", "Market St", "Park Blvd", "Highland Ave", "Center St", "Elm St",
           "Lakeview Dr", "Industrial Pkwy", "Broad St", "Church St", "Pine St", "Maple Ave", "Union St"]
CITY_STATE = re.compile(r"^(?P<city>.+?)\s+(?P<state>[A-Z]{2})(?:\s+-?\s*\d+)?$")
ZIP3 = {"MA": (10, 27), "RI": (28, 29), "NH": (30, 38), "ME": (39, 49), "VT": (50, 59), "CT": (60, 69), "NJ": (70, 89),
        "NY": (100, 149), "PA": (150, 196), "DE": (197, 199), "DC": (200, 205), "MD": (206, 219), "VA": (220, 246),
        "WV": (247, 268), "NC": (270, 289), "SC": (290, 299), "GA": (300, 319), "FL": (320, 349), "AL": (350, 369),
        "TN": (370, 385), "MS": (386, 397), "KY": (400, 427), "OH": (430, 458), "IN": (460, 479), "MI": (480, 499),
        "IA": (500, 528), "WI": (530, 549), "MN": (550, 567), "SD": (570, 577), "ND": (580, 588), "MT": (590, 599),
        "IL": (600, 629), "MO": (630, 658), "KS": (660, 679), "NE": (680, 693), "LA": (700, 714), "AR": (716, 729),
        "OK": (730, 749), "TX": (750, 799), "CO": (800, 816), "WY": (820, 831), "ID": (832, 838), "UT": (840, 847),
        "AZ": (850, 865), "NM": (870, 884), "NV": (889, 898), "CA": (900, 961), "HI": (967, 968), "OR": (970, 979),
        "WA": (980, 994), "AK": (995, 999)}


US_STATES = set(ZIP3)


def title(text: str) -> str:
    """"1ST STREET" -> "1st Street" (str.title would give "1St")."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in text.split())


def fake_zip(state: str, rnd: random.Random) -> str:
    lo, hi = ZIP3.get(state, (100, 999))
    return f"{rnd.randint(lo, hi):03d}{rnd.randint(1, 99):02d}"


def parse_place(desc: str) -> tuple[str | None, str, str] | None:
    """(street, city, state) from a cost-center description: "NEW CASTLE DE 00373", "LOGAN UT - 1295",
    "MURFREESBORO, TN", "WILSHIRE AVE SW, CONCORD, NC, USA"; None when it names no US place."""
    text = " ".join(desc.upper().split())
    if "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip() and p.strip() != "USA"]
        for i, p in enumerate(parts):
            if p in US_STATES and i > 0:
                return (parts[i - 2] if i >= 2 else None), parts[i - 1], p
        return None
    m = CITY_STATE.match(text)
    if m and m.group("state") in US_STATES:
        return None, m.group("city"), m.group("state")
    return None


# ------------------------------------------------------------------ helpers
class Fake:
    """Seeded decorations for one invoice (never part of the truth)."""

    def __init__(self, seed: str) -> None:
        self.rnd = random.Random(seed)

    def digits(self, n: int) -> str:
        return str(self.rnd.randint(1, 9)) + "".join(str(self.rnd.randint(0, 9)) for _ in range(n - 1))

    def int(self, lo: int, hi: int) -> int:
        return self.rnd.randint(lo, hi)

    def choice(self, seq: list[Any]) -> Any:
        return self.rnd.choice(seq)

    def days(self, d: date, lo: int, hi: int) -> date:
        return d + timedelta(days=self.rnd.randint(lo, hi))


def money(v: Any, places: int = 2, symbol: str = "") -> str:
    d = Decimal(str(v)).quantize(Decimal(1) / (Decimal(10) ** places), rounding=ROUND_HALF_UP)
    s = f"{abs(d):,.{places}f}"
    return f"-{symbol}{s}" if d < 0 else f"{symbol}{s}"


def fmt_date(v: Any, pattern: str = "%m/%d/%Y") -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    out = v.strftime(pattern)
    return out.replace("{d}", str(v.day)).replace("{m}", str(v.month))


def split_amount(amount: Decimal, entries: list[dict[str, Any]], rnd: random.Random) -> list[dict[str, Any]]:
    """The line amount as the supplier's items (profile `items`): fixed amounts first (left out when they
    would take half the line or more), then shares, the `rest` entry takes what is left (split over its
    picks). Always sums to `amount` exactly."""
    out: list[dict[str, Any]] = []
    left = amount
    rest = None
    for e in entries:
        if e.get("rest"):
            rest = e
            continue
        if "fixed" in e:
            a = Decimal(str(e["fixed"]))
            if a * 2 >= amount:
                continue
        elif "share" in e:
            a = (amount * Decimal(str(round(rnd.uniform(*e["share"]), 4)))).quantize(CENT, rounding=ROUND_HALF_UP)
        else:
            continue
        if e.get("optional") and rnd.random() < float(e["optional"]):
            continue
        out.append(_item(e, a, rnd))
        left -= a
    if rest is None:
        rest = {"desc": "Charges"}
    picks = _descs(rest, rnd)
    weights = [rnd.uniform(0.5, 1.5) for _ in picks]
    parts, acc = [], Decimal(0)
    for i, w in enumerate(weights):
        a = left - acc if i == len(weights) - 1 else (left * Decimal(str(w / sum(weights)))).quantize(CENT)
        acc += a
        parts.append(a)
    rest_items = [_item({**rest, "desc": dsc}, a, rnd) for dsc, a in zip(picks, parts, strict=True)]
    position = rest.get("position", "first")
    return rest_items + out if position == "first" else out + rest_items


def _descs(e: dict[str, Any], rnd: random.Random) -> list[str]:
    desc = e.get("desc") or "Charges"
    if isinstance(desc, str):
        return [desc]
    lo, hi = e.get("pick", [1, 1])
    return rnd.sample(desc, min(len(desc), rnd.randint(lo, hi)))


def _item(e: dict[str, Any], amount: Decimal, rnd: random.Random) -> dict[str, Any]:
    desc = e["desc"] if isinstance(e["desc"], str) else rnd.choice(e["desc"])
    item: dict[str, Any] = {"description": desc, "amount": amount, "quantity": None, "unit_price": None,
                            "uom": e.get("uom"), "code": None}
    if e.get("qty"):
        q = rnd.randint(*e["qty"])
        item["quantity"] = q
        item["unit_price"] = (amount / q).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if e.get("code"):
        item["code"] = str(rnd.randint(10 ** (e["code"] - 1), 10 ** e["code"] - 1))
    return item


# ------------------------------------------------------------------ data
@dataclass
class Keyed:
    """One keyed invoice as rows (delivery or synthetic CSV)."""
    header: dict[str, Any]
    lines: list[dict[str, Any]]
    dists: list[dict[str, Any]]


@dataclass
class Sources:
    invoices: dict[int, Keyed] = field(default_factory=dict)
    suppliers: dict[int, dict[str, Any]] = field(default_factory=dict)
    sites: dict[int, dict[str, Any]] = field(default_factory=dict)
    combos: dict[int, dict[str, Any]] = field(default_factory=dict)
    flex: dict[tuple[int, str], str] = field(default_factory=dict)        # (segment number, value) -> description
    terms: dict[int, str] = field(default_factory=dict)


def _rows_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: (v if v != "" else None) for k, v in r.items()} for r in csv.DictReader(f)]


def load_sources(deliveries: list[Path], dataset: Path) -> Sources:
    from aap.clientdata.readers import num
    from aap.ebs_mock.client_load import merge_deliveries

    d = merge_deliveries(deliveries)
    s = Sources()
    s.suppliers = {num(x.get("VENDOR_ID")): x for x in d.rows("AP_SUPPLIERS")}           # type: ignore[misc]
    s.sites = {num(x.get("VENDOR_SITE_ID")): x for x in d.rows("AP_SUPPLIER_SITES_ALL")}  # type: ignore[misc]
    s.combos = {num(x.get("CODE_COMBINATION_ID")): x for x in d.rows("GL_CODE_COMBINATIONS")}  # type: ignore[misc]
    value_set = {str(x.get("FLEX_VALUE_SET_NAME")): num(str(x.get("APPLICATION_COLUMN_NAME")).replace("SEGMENT", ""))
                 for x in d.rows("FND_ID_FLEX_SEGMENTS")}
    for x in d.rows("FND_FLEX_VALUES"):
        n = value_set.get(str(x.get("FLEX_VALUE_SET_NAME")))
        if n is not None and x.get("DESCRIPTION"):
            s.flex[(n, str(x.get("FLEX_VALUE")))] = str(x["DESCRIPTION"])
    s.terms = {num(x.get("TERM_ID")): str(x.get("NAME")) for x in d.rows("AP_TERMS")}  # type: ignore[misc]
    tables = {"AP_INVOICES_ALL": d.rows("AP_INVOICES_ALL"), "AP_INVOICE_LINES_ALL": d.rows("AP_INVOICE_LINES_ALL"),
              "AP_INVOICE_DISTRIBUTIONS_ALL": d.rows("AP_INVOICE_DISTRIBUTIONS_ALL")}
    syn = dataset / "synthetic"
    for name in tables:
        if (syn / f"{name}.csv").exists():
            tables[name] = tables[name] + _rows_csv(syn / f"{name}.csv")
    for h in tables["AP_INVOICES_ALL"]:
        i = num(h.get("INVOICE_ID"))
        if i is not None:
            s.invoices.setdefault(i, Keyed(h, [], []))
    for x in tables["AP_INVOICE_LINES_ALL"]:
        k = s.invoices.get(num(x.get("INVOICE_ID")))  # type: ignore[arg-type]
        if k is not None and (x.get("DISCARDED_FLAG") or "N") != "Y" and (x.get("CANCELLED_FLAG") or "N") != "Y":
            k.lines.append(x)
    for x in tables["AP_INVOICE_DISTRIBUTIONS_ALL"]:
        k = s.invoices.get(num(x.get("INVOICE_ID")))  # type: ignore[arg-type]
        if k is not None and (x.get("REVERSAL_FLAG") or "N") != "Y":
            k.dists.append(x)
    return s


def dataset_items(dataset: Path) -> list[dict[str, Any]]:
    items = []
    for split in ("train", "dev", "test"):
        path = dataset / f"{split}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            m = r["meta"]
            invoice_id = int(r["id"][4:]) if r["id"].startswith("syn-") else int(r["id"])
            items.append({"invoice_id": invoice_id, "split": split, "label": m["label"], "supplier": m["supplier"],
                          "parent_invoice_id": m.get("parent_invoice_id"), "has_file": bool(m.get("has_file")),
                          "holdout": bool(m.get("holdout"))})
    return items


# ------------------------------------------------------------------ context
def _segments(s: Sources, ccid: Any) -> list[str] | None:
    from aap.clientdata.readers import num

    c = s.combos.get(num(ccid))  # type: ignore[arg-type]
    if c is None:
        return None
    return [str(c.get(f"SEGMENT{i}")) for i in range(1, 31) if c.get(f"SEGMENT{i}") is not None]


def location_of(s: Sources, ccid: Any, profile: dict[str, Any], cc_segment: int = 3, project_segment: int = 5
                ) -> dict[str, Any]:
    """Where the invoice is printed as going, from the cost center of the line's account."""
    segs = _segments(s, ccid) or []
    cc = segs[cc_segment - 1] if len(segs) >= cc_segment else None
    project = segs[project_segment - 1] if len(segs) >= project_segment else None
    desc = s.flex.get((cc_segment, cc or ""), "") if cc else ""
    out: dict[str, Any] = {"cost_center": cc, "description": desc, "project": project,
                           "project_description": s.flex.get((project_segment, project or "")),
                           "kind": "party", "name": title(desc) if desc else None, "store": None, "city": None,
                           "state": None, "street": None, "zip": None}
    party = (profile.get("parties") or {}).get(cc or "") or (profile.get("parties") or {}).get("*")
    place = parse_place(desc) if desc else None
    if cc and cc.isdigit() and int(cc) < 90000 and place:
        n = int(cc)
        rnd = random.Random(f"store:{cc}")
        street = title(place[0]) if place[0] else rnd.choice(STREETS)
        out.update(kind="store", store=str(n), city=title(place[1]), state=place[2],
                   street=f"{rnd.randint(100, 9899)} {street}", zip=fake_zip(place[2], rnd),
                   name=str(profile.get("store_name", "Store #{n}")).format(
                       n=n, city=title(place[1]), CITY=place[1], state=place[2]))
    elif party:
        out.update(kind="party", **{k: v for k, v in party.items() if k in ("name", "street", "city", "state", "zip")})
    return out


def _non_tax(k: Keyed) -> list[dict[str, Any]]:
    from aap.clientdata.readers import num

    return sorted((x for x in k.lines if str(x.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper() != "TAX"),
                  key=lambda x: num(x.get("LINE_NUMBER")) or 0)


def _account_of_line(k: Keyed, line: dict[str, Any]) -> Any:
    from aap.clientdata.readers import dec, num

    own = [x for x in k.dists if num(x.get("INVOICE_LINE_NUMBER")) == num(line.get("LINE_NUMBER"))]
    top = max(own, key=lambda x: abs(dec(x.get("AMOUNT")) or 0), default=None)
    return top.get("DIST_CODE_COMBINATION_ID") if top else None


def printed_values(s: Sources, k: Keyed, profile: dict[str, Any], fake: Fake) -> dict[str, Any]:
    """The values the page shows (the truth's `source`), from the keyed rows and the profile."""
    from aap.clientdata.readers import day, dec

    h = k.header
    inv_date = day(h.get("INVOICE_DATE"))
    total = dec(h.get("INVOICE_AMOUNT")) or Decimal(0)
    lines = [x for x in _non_tax(k) if (dec(x.get("AMOUNT")) or 0) != 0] or _non_tax(k)
    rnd = fake.rnd
    items: list[dict[str, Any]] = []
    if len(lines) == 1:
        items = split_amount(dec(lines[0].get("AMOUNT")) or Decimal(0), profile.get("items") or [], rnd)
    else:
        multi = profile.get("multi_line_desc") or ["Charges"]
        for i, ln in enumerate(lines):
            text = str(ln.get("DESCRIPTION") or "").strip() or multi[i % len(multi)]
            items.append({"description": text, "amount": dec(ln.get("AMOUNT")) or Decimal(0), "quantity": None,
                          "unit_price": None, "uom": None, "code": None})
    subtotal = sum((i["amount"] for i in items), Decimal(0))
    tax = total - subtotal
    biggest = max(lines, key=lambda x: abs(dec(x.get("AMOUNT")) or 0)) if lines else None
    location = location_of(s, _account_of_line(k, biggest) if biggest else None, profile)
    due = inv_date + timedelta(days=int(profile.get("due_days", 30))) if inv_date else None
    po = None
    if profile.get("po") == "project" and location.get("project") and location["project"] != "00000":
        po = location["project"]
    elif profile.get("po") == "digits":
        po = fake.digits(int(profile.get("po_digits", 6)))
    return {"invoice_number": str(h.get("INVOICE_NUM")), "invoice_date": inv_date, "due_date": due,
            "terms_text": profile.get("terms_text"), "po_number": po, "lines": items, "subtotal": subtotal,
            "tax": tax if tax > 0 else None, "total": total, "currency": h.get("INVOICE_CURRENCY_CODE") or "USD",
            "location": location}


def source_truth(p: dict[str, Any], vendor_name: str | None) -> dict[str, Any]:
    loc = p["location"]
    ship = ", ".join(x for x in (loc.get("name"), loc.get("street"), loc.get("city"), loc.get("state")) if x) or None
    return {"vendor_name": vendor_name, "invoice_number": p["invoice_number"],
            "invoice_date": p["invoice_date"].isoformat() if p["invoice_date"] else None,
            "due_date": p["due_date"].isoformat() if p["due_date"] else None, "po_number": p["po_number"],
            "payment_terms": p["terms_text"], "currency": p["currency"],
            "subtotal": str(p["subtotal"]) if p["tax"] else None, "tax_amount": str(p["tax"]) if p["tax"] else None,
            "total": str(p["total"]), "ship_to": ship,
            "lines": [{"description": i["description"], "quantity": None if i["quantity"] is None else str(i["quantity"]),
                       "unit_price": None if i["unit_price"] is None else str(i["unit_price"]),
                       "amount": str(i["amount"]), "item_code": i["code"], "uom": i["uom"]} for i in p["lines"]]}


def expected_values(s: Sources, k: Keyed) -> dict[str, Any]:
    """The keyed values (as aap.clientdata.goldset writes them)."""
    from aap.clientdata.readers import day, dec, num

    h = k.header

    def account(ccid: Any) -> str | None:
        segs = _segments(s, ccid)
        return ".".join(segs) if segs else None

    return {"vendor_id": num(h.get("VENDOR_ID")), "vendor_site_id": num(h.get("VENDOR_SITE_ID")),
            "org_id": num(h.get("ORG_ID")), "currency_code": h.get("INVOICE_CURRENCY_CODE"),
            "invoice_num": str(h.get("INVOICE_NUM")), "invoice_date": str(day(h.get("INVOICE_DATE")) or "") or None,
            "invoice_amount": str(dec(h.get("INVOICE_AMOUNT"))), "terms_id": num(h.get("TERMS_ID")),
            "terms_name": s.terms.get(num(h.get("TERMS_ID"))),  # type: ignore[arg-type]
            "invoice_type": h.get("INVOICE_TYPE_LOOKUP_CODE"), "gl_date": str(day(h.get("GL_DATE")) or "") or None,
            "lines": [{"line_number": num(x.get("LINE_NUMBER")), "line_type": x.get("LINE_TYPE_LOOKUP_CODE"),
                       "amount": str(dec(x.get("AMOUNT"))), "description": x.get("DESCRIPTION"),
                       "distribution_set_id": num(x.get("DISTRIBUTION_SET_ID")),
                       "distributions": [{"amount": str(dec(y.get("AMOUNT"))),
                                          "account": account(y.get("DIST_CODE_COMBINATION_ID"))}
                                         for y in k.dists if num(y.get("INVOICE_LINE_NUMBER")) == num(x.get("LINE_NUMBER"))]}
                      for x in sorted(k.lines, key=lambda y: num(y.get("LINE_NUMBER")) or 0)],
            "expect_fail": [], "expect_warn": [], "expect_flags": []}


# ------------------------------------------------------------------ render
_ENV: Any = None
_SRC: Sources | None = None
_PROFILES: dict[str, Any] = {}


def _env(templates: Path) -> Any:
    from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape

    from .render import TEMPLATES

    env = Environment(loader=ChoiceLoader([FileSystemLoader(str(templates)), FileSystemLoader(str(TEMPLATES))]),
                      autoescape=select_autoescape(["html", "j2"]), undefined=StrictUndefined)
    env.filters["money"] = money
    env.filters["d"] = fmt_date
    return env


def _init(deliveries: list[str], dataset: str, templates: str) -> None:
    global _ENV, _SRC, _PROFILES
    _SRC = load_sources([Path(p) for p in deliveries], Path(dataset))
    _PROFILES = json.loads((Path(templates) / "profiles.json").read_text(encoding="utf-8"))
    _ENV = _env(Path(templates))


def profile_for(profiles: dict[str, Any], supplier: str) -> dict[str, Any]:
    return {**profiles.get("default", {}), **profiles.get(supplier, {})}


def render_one(item: dict[str, Any], out: str) -> dict[str, Any]:
    from aap.clientdata.readers import num

    from .render import html_to_pdf, scan_noise

    assert _SRC is not None and _ENV is not None
    k = _SRC.invoices[item["invoice_id"]]
    profile = profile_for(_PROFILES, item["supplier"])
    fake = Fake(f"invoice:{item['invoice_id']}")
    p = printed_values(_SRC, k, profile, fake)
    vendor = _SRC.suppliers.get(num(k.header.get("VENDOR_ID")), {})  # type: ignore[arg-type]
    site = _SRC.sites.get(num(k.header.get("VENDOR_SITE_ID")), {})  # type: ignore[arg-type]
    html = _ENV.get_template(profile["template"]).render(inv=p, vendor=vendor, site=site, location=p["location"],
                                                         profile=profile, fake=fake)
    pdf = html_to_pdf(html)
    scanned = bool(profile.get("scanned"))
    if scanned:
        pdf = scan_noise(pdf, random.Random(f"scan:{item['invoice_id']}"))
    root = Path(out)
    name = f"{item['invoice_id']}.pdf"
    (root / "pdf" / name).write_bytes(pdf)
    sha = hashlib.sha256(pdf).hexdigest()
    truth = {"sha256": sha, "generator": "tools.invoicegen.keyed", "layout": profile["template"], "scanned": scanned,
             "pages": None, "file_kind": "pdf", "invoice_id": item["invoice_id"], "vendor_number": item["supplier"],
             "split": item["split"], "label": item["label"], "parent_invoice_id": item["parent_invoice_id"],
             "supplier_holdout": item["holdout"], "location_printed": p["location"]["kind"],
             "source": source_truth(p, vendor.get("VENDOR_NAME")), "expected": expected_values(_SRC, k)}
    (root / "truth" / f"{sha}.truth.json").write_text(json.dumps(truth, indent=1, default=str) + "\n", encoding="utf-8")
    return {"kind": "pdf", "file": f"pdf/{name}", "split": item["split"], "label": item["label"],
            "supplier": item["supplier"], "invoice_id": item["invoice_id"], "parent_invoice_id": item["parent_invoice_id"],
            "layout": profile["template"], "scanned": scanned, "location_printed": p["location"]["kind"],
            "documents": [{"pdf": f"pdf/{name}", "sha256": sha, "layout": profile["template"], "scanned": scanned,
                           "expected": truth["expected"]}]}


def build(deliveries: list[Path], dataset: Path, templates: Path, out: Path, *, workers: int = 6,
          limit: int | None = None, only: set[str] | None = None) -> dict[str, Any]:
    items = dataset_items(dataset)
    real_files = [i for i in items if i["has_file"]]
    todo = [i for i in items if not i["has_file"] and (only is None or i["supplier"] in only)][:limit]
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    (out / "truth").mkdir(parents=True, exist_ok=True)
    args = ([str(p) for p in deliveries], str(dataset), str(templates))
    manifest_items: list[dict[str, Any]] = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=args) as pool:
            for m in pool.map(render_one, todo, [str(out)] * len(todo), chunksize=8):
                manifest_items.append(m)
    else:
        _init(*args)
        manifest_items = [render_one(i, str(out)) for i in todo]
    manifest_items += [{"kind": "real_file", "real_file": True, "split": i["split"], "label": i["label"],
                        "supplier": i["supplier"], "invoice_id": i["invoice_id"]} for i in real_files]
    counts: dict[str, dict[str, int]] = {}
    for m in manifest_items:
        c = counts.setdefault(m["supplier"], {})
        key = "real_file" if m.get("real_file") else f"{m['label']}_{m['split']}"
        c[key] = c.get(key, 0) + 1
    manifest = {"generator": "tools.invoicegen.keyed", "dataset": dataset.name,
                "deliveries": [p.name for p in deliveries], "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "rendered": len(todo), "real_files": len(real_files),
                "rule": "location printed from the keyed cost center (2026-11-23): report scores on these files "
                        "apart from the real files",
                "per_supplier": dict(sorted(counts.items())), "items": manifest_items}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.invoicegen.keyed", description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--from", dest="earlier", type=Path, action="append", default=[])
    ap.add_argument("--delivery", type=Path, required=True)
    ap.add_argument("--templates", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="supplier numbers, comma-separated (template work)")
    a = ap.parse_args(argv)
    only = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
    m = build([*a.earlier, a.delivery], a.dataset, a.templates, a.out, workers=a.workers, limit=a.limit, only=only)
    print(f"[keyed] {m['rendered']} files in {a.out} ({m['real_files']} real files listed, not rendered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
