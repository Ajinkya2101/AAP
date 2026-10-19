"""Initial value maps (functional-team owned, ARCHITECTURE 6.5). Loaded once by migrate."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aap.common.text import text_key
from aap.db.models import ValueMap

DEFAULT_VALUE_MAPS: list[tuple[str, str, str]] = [
    # (map_type, source value, oracle value)
    ("terms", "Due upon receipt", "IMMEDIATE"),
    ("terms", "Due on receipt", "IMMEDIATE"),
    ("terms", "Payable on receipt", "IMMEDIATE"),
    ("terms", "Immediate", "IMMEDIATE"),
    ("terms", "Cash on delivery", "IMMEDIATE"),
    ("uom", "ea", "Each"), ("uom", "each", "Each"), ("uom", "pcs", "Each"), ("uom", "pc", "Each"),
    ("uom", "nos", "Each"), ("uom", "no", "Each"), ("uom", "unit", "Each"), ("uom", "units", "Each"),
    ("uom", "stk", "Each"), ("uom", "stück", "Each"), ("uom", "bx", "Box"), ("uom", "box", "Box"),
    ("uom", "hr", "Hour"), ("uom", "hrs", "Hour"), ("uom", "hours", "Hour"), ("uom", "h", "Hour"),
    ("uom", "mo", "Month"), ("uom", "month", "Month"), ("uom", "mth", "Month"), ("uom", "kg", "Kilogram"),
    ("uom", "l", "Liter"), ("uom", "ltr", "Liter"), ("uom", "m", "Meter"), ("uom", "pk", "Pack"),
    ("uom", "pack", "Pack"), ("uom", "set", "Set"), ("uom", "dz", "Dozen"), ("uom", "doz", "Dozen"),
]


def load_default_value_maps(session: Session) -> int:
    if session.scalar(select(func.count()).select_from(ValueMap)):
        return 0
    for map_type, source, oracle in DEFAULT_VALUE_MAPS:
        session.add(ValueMap(map_type=map_type, source_value=source, source_key=text_key(source),
                             vendor_id=None, oracle_value=oracle, status="active", created_by="seed",
                             approved_by="seed"))
    return len(DEFAULT_VALUE_MAPS)
