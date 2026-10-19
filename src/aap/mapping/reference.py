"""In-memory view of the reference copy (ref_*) and value maps for one mapping run."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aap.common.text import text_key
from aap.db.models import (
    GlHistoryLine,
    RefCategory,
    RefCcid,
    RefCurrency,
    RefGlPeriod,
    RefOperatingUnit,
    RefSupplier,
    RefSupplierBank,
    RefSupplierSite,
    RefTaxRate,
    RefTerms,
    RefTolerance,
    RefUom,
    ValueMap,
)


@dataclass
class Reference:
    operating_units: dict[int, RefOperatingUnit]
    suppliers: dict[int, RefSupplier]
    sites: dict[int, RefSupplierSite]
    sites_by_vendor: dict[int, list[RefSupplierSite]]
    banks_by_vendor: dict[int, list[RefSupplierBank]]
    terms: dict[int, RefTerms]
    currencies: dict[str, RefCurrency]
    uoms: dict[str, RefUom]
    categories: dict[int, RefCategory]
    tax_rates: list[RefTaxRate]
    tolerances: dict[int, RefTolerance]
    periods: list[RefGlPeriod]
    value_maps: dict[tuple[str, str, int | None], ValueMap] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.suppliers

    def value_map(self, map_type: str, source: str | None, vendor_id: int | None = None) -> ValueMap | None:
        if not source:
            return None
        key = text_key(source)
        return self.value_maps.get((map_type, key, vendor_id)) or self.value_maps.get((map_type, key, None))

    def ccid(self, session: Session, ccid: int) -> RefCcid | None:
        return session.get(RefCcid, ccid)


def load_reference(session: Session) -> Reference:
    sites = list(session.scalars(select(RefSupplierSite)))
    by_vendor: dict[int, list[RefSupplierSite]] = defaultdict(list)
    for s in sites:
        by_vendor[s.vendor_id].append(s)
    banks: dict[int, list[RefSupplierBank]] = defaultdict(list)
    for b in session.scalars(select(RefSupplierBank)):
        banks[b.vendor_id].append(b)
    ref = Reference(
        operating_units={o.org_id: o for o in session.scalars(select(RefOperatingUnit))},
        suppliers={s.vendor_id: s for s in session.scalars(select(RefSupplier))},
        sites={s.vendor_site_id: s for s in sites},
        sites_by_vendor=dict(by_vendor),
        banks_by_vendor=dict(banks),
        terms={t.term_id: t for t in session.scalars(select(RefTerms))},
        currencies={c.currency_code: c for c in session.scalars(select(RefCurrency))},
        uoms={u.unit_of_measure: u for u in session.scalars(select(RefUom))},
        categories={c.category_id: c for c in session.scalars(select(RefCategory))},
        tax_rates=list(session.scalars(select(RefTaxRate))),
        tolerances={t.tolerance_id: t for t in session.scalars(select(RefTolerance))},
        periods=list(session.scalars(select(RefGlPeriod))),
    )
    for vm in session.scalars(select(ValueMap).where(ValueMap.status == "active")):
        ref.value_maps[(vm.map_type, vm.source_key, vm.vendor_id)] = vm
    return ref


def history_category(session: Session, vendor_id: int, description: str) -> tuple[str | None, int]:
    """Most common category code this vendor used for exactly this line text."""
    rows = session.execute(
        select(GlHistoryLine.category_code, func.count())
        .where(GlHistoryLine.vendor_id == vendor_id,
               func.lower(GlHistoryLine.description) == description.lower(),
               GlHistoryLine.category_code.is_not(None))
        .group_by(GlHistoryLine.category_code)).all()
    if not rows:
        return None, 0
    counts = Counter({code: n for code, n in rows})
    code, n = counts.most_common(1)[0]
    return code, n


def as_candidates(items: list[tuple[Any, str]]) -> list[dict[str, Any]]:
    return [{"id": str(i), "label": label} for i, label in items]
