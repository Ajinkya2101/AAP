"""Seeding context: deterministic randomness, ID allocation and the row buffer."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from faker import Faker

CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Scale:
    suppliers: int
    pos: int
    invoices: int
    items_per_category: int
    rate_days: int


SCALES = {
    "small": Scale(suppliers=40, pos=150, invoices=700, items_per_category=2, rate_days=120),
    "default": Scale(suppliers=150, pos=900, invoices=6000, items_per_category=7, rate_days=730),
    "large": Scale(suppliers=400, pos=3000, invoices=20000, items_per_category=12, rate_days=730),
}

# Sequence name -> first value (must match the DDL START values).
SEQUENCES = {
    "fnd.fnd_user_s": 1100,
    "fnd.fnd_concurrent_requests_s": 7340000,
    "fnd.fnd_flex_value_sets_s": 1002000,
    "fnd.fnd_flex_values_s": 110000,
    "hr.hr_locations_s": 14000,
    "hr.hr_organization_units_s": 1000,
    "hr.hr_organization_information_s": 30000,
    "hr.per_people_s": 25000,
    "gl.gl_ledgers_s": 2021,
    "gl.gl_code_combinations_s": 12000,
    "hz.hz_parties_s": 1020000,
    "iby.iby_ext_bank_accounts_s": 50000,
    "iby.iby_account_owners_s": 60000,
    "zx.zx_rates_b_s": 10000,
    "ap.ap_suppliers_s": 30000,
    "ap.ap_supplier_sites_s": 7000,
    "ap.ap_terms_s": 10000,
    "ap.ap_tolerance_templates_s": 1,
    "ap.ap_invoices_s": 880000,
    "ap.ap_invoice_distributions_s": 3200000,
    "ap.ap_holds_s": 90000,
    "po.po_headers_s": 110000,
    "po.po_lines_s": 220000,
    "po.po_line_locations_s": 330000,
    "po.po_distributions_s": 440000,
    "po.po_segment1_s": 4500200,
    "po.po_line_types_s": 1000,
    "po.rcv_shipment_headers_s": 900000,
    "po.rcv_shipment_lines_s": 1900000,
    "po.rcv_transactions_s": 2900000,
    "po.rcv_receipt_num_s": 17000,
    "inv.mtl_system_items_b_s": 150000,
    "inv.mtl_categories_s": 5000,
}

# Insert order (FK safe).
TABLE_ORDER = [
    "fnd.fnd_currencies",
    "fnd.fnd_lookup_values",
    "fnd.fnd_user",
    "fnd.fnd_concurrent_programs",
    "fnd.fnd_flex_value_sets",
    "fnd.fnd_flex_values",
    "fnd.fnd_flex_values_tl",
    "hr.hr_locations_all",
    "hr.hr_all_organization_units",
    "hr.hr_organization_information",
    "hr.per_all_people_f",
    "po.po_agents",
    "gl.gl_ledgers",
    "gl.gl_code_combinations",
    "gl.gl_period_statuses",
    "gl.gl_daily_rates",
    "hz.hz_parties",
    "iby.iby_ext_bank_accounts",
    "iby.iby_account_owners",
    "zx.zx_rates_b",
    "ap.ap_terms_tl",
    "ap.ap_terms_lines",
    "ap.ap_tolerance_templates",
    "inv.mtl_units_of_measure_tl",
    "inv.mtl_categories_b",
    "inv.mtl_categories_tl",
    "inv.mtl_system_items_b",
    "inv.mtl_item_categories",
    "ap.ap_suppliers",
    "ap.ap_supplier_sites_all",
    "po.po_line_types_b",
    "po.po_headers_all",
    "po.po_lines_all",
    "po.po_line_locations_all",
    "po.po_distributions_all",
    "po.rcv_shipment_headers",
    "po.rcv_shipment_lines",
    "po.rcv_transactions",
    "ap.ap_invoices_all",
    "ap.ap_invoice_lines_all",
    "ap.ap_invoice_distributions_all",
    "ap.ap_holds_all",
]

SEED_USER_ID = 1318  # AAP_INTEGRATION / seed runs


@dataclass
class SeedContext:
    seed: int
    scale: Scale
    anchor: date
    rnd: random.Random = field(init=False)
    fake: Faker = field(init=False)
    fake_in: Faker = field(init=False)
    fake_de: Faker = field(init=False)
    fake_gb: Faker = field(init=False)
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    _next: dict[str, int] = field(default_factory=dict)
    _reserved: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    # Cross-generator lookups (filled by the generators).
    ref: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rnd = random.Random(self.seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(self.seed)
        self.fake_in = Faker("en_IN")
        self.fake_in.seed_instance(self.seed + 1)
        self.fake_de = Faker("de_DE")
        self.fake_de.seed_instance(self.seed + 2)
        self.fake_gb = Faker("en_GB")
        self.fake_gb.seed_instance(self.seed + 3)

    # ------------------------------------------------------------ ids
    def reserve(self, seq: str, value: int) -> int:
        self._reserved[seq].add(value)
        return value

    def next_id(self, seq: str, step: int = 1) -> int:
        value = self._next.get(seq, SEQUENCES[seq])
        while value in self._reserved[seq]:
            value += 1
        self._next[seq] = value + step
        return value

    def sequence_high_water(self) -> dict[str, int]:
        """Last value used (or reserved) per sequence, for setval()."""
        out: dict[str, int] = {}
        for seq in set(self._next) | set(self._reserved):
            used = self._next.get(seq, SEQUENCES[seq]) - 1
            reserved = max(self._reserved[seq], default=0)
            out[seq] = max(used, reserved, SEQUENCES[seq])
        return out

    # ----------------------------------------------------------- rows
    def add(self, table: str, **row: Any) -> dict[str, Any]:
        if table not in TABLE_ORDER:
            raise KeyError(f"unknown seed table {table}")
        self.rows[table].append(row)
        return row

    # ---------------------------------------------------------- dates
    def ts(self, d: date | datetime) -> datetime:
        if isinstance(d, datetime):
            return d.replace(microsecond=0)
        return datetime(d.year, d.month, d.day)

    def days_ago(self, days: int) -> date:
        return self.anchor - timedelta(days=days)

    def random_date(self, start_days_ago: int, end_days_ago: int = 0) -> date:
        return self.days_ago(self.rnd.randint(end_days_ago, start_days_ago))

    def chance(self, p: float) -> bool:
        return self.rnd.random() < p
