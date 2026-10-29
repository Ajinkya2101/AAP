"""Store / location -> GL cost center table (functional-team owned; PLAN-REAL-INVOICES R1b.4, R2.1).

    python -m aap.mapping.locations stores.csv [--by NAME] [--dry-run]

The file has a header row with a location column (location, store, store_number, location_ref)
and a cost-center column (cost_center, cost_centre, cc, segment3); any of , ; tab | separates.
Each row becomes an active `location` value map: map_location gives the invoice's printed store
("STORE 316", "KRS 51", "#0368") its cost center, and GL coding puts it into the account
(GL_COST_CENTER_SEGMENT). A store number is keyed without leading zeros, as map_location looks it
up. Loading again replaces the active map of the same location.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.common.text import text_key
from aap.db.models import ValueMap

LOCATION_COLUMNS = ("location", "store", "store_number", "store_no", "location_ref")
COST_CENTER_COLUMNS = ("cost_center", "cost_centre", "cc", "segment3")


class LocationTableError(ValueError):
    pass


def _column(header: list[str], names: tuple[str, ...]) -> int:
    keys = [h.strip().lower().replace(" ", "_").replace("-", "_") for h in header]
    for name in names:
        if name in keys:
            return keys.index(name)
    raise LocationTableError(f"no column named one of {', '.join(names)} (header: {', '.join(header)})")


def location_key(location: str) -> str:
    """"0368" -> "368" (map_location looks a printed store up by its number without leading zeros)."""
    loc = location.strip()
    return (loc.lstrip("0") or "0") if loc.isdigit() else loc


def read_table(text: str) -> list[tuple[str, str]]:
    """(location, cost center) rows of the file; blank rows skipped, a location twice is an error."""
    lines = [ln for ln in text.lstrip("﻿").splitlines() if ln.strip()]
    if not lines:
        raise LocationTableError("the file is empty")
    dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t|")
    rows = list(csv.reader(io.StringIO("\n".join(lines)), dialect))
    loc_i, cc_i = _column(rows[0], LOCATION_COLUMNS), _column(rows[0], COST_CENTER_COLUMNS)
    out: dict[str, tuple[str, str]] = {}
    for n, row in enumerate(rows[1:], start=2):
        loc = row[loc_i].strip() if loc_i < len(row) else ""
        cc = row[cc_i].strip() if cc_i < len(row) else ""
        if not loc or not cc:
            raise LocationTableError(f"row {n}: location and cost center are both required")
        key = text_key(location_key(loc))
        if key in out and out[key][1] != cc:
            raise LocationTableError(f"row {n}: location {loc!r} is listed twice with different cost centers")
        out[key] = (location_key(loc), cc)
    return list(out.values())


def load_locations(session: Session, rows: list[tuple[str, str]], by: str) -> tuple[int, int]:
    """Active `location` value maps for the rows. Returns (added, replaced)."""
    added = replaced = 0
    for location, cost_center in rows:
        key = text_key(location)
        current = list(session.scalars(select(ValueMap).where(
            ValueMap.map_type == "location", ValueMap.source_key == key, ValueMap.vendor_id.is_(None),
            ValueMap.status == "active")))
        if any(vm.oracle_value == cost_center for vm in current):
            continue
        for vm in current:
            vm.status = "retired"
            replaced += 1
        session.add(ValueMap(map_type="location", source_value=location, source_key=key, vendor_id=None,
                             oracle_value=cost_center, status="active", created_by=by, approved_by=by))
        added += 1
    return added, replaced


def main(argv: list[str] | None = None) -> int:
    from aap.db.session import session_scope

    p = argparse.ArgumentParser(prog="python -m aap.mapping.locations", description=__doc__.split("\n\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--by", default="functional-team", help="who supplied the table (value_map.created_by)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        rows = read_table(a.file.read_text(encoding="utf-8-sig"))
    except LocationTableError as exc:
        print(f"{a.file}: {exc}", file=sys.stderr)
        return 1
    if a.dry_run:
        for loc, cc in rows:
            print(f"{loc} -> {cc}")
        print(f"{len(rows)} location(s); nothing written (--dry-run)")
        return 0
    with session_scope() as session:
        added, replaced = load_locations(session, rows, a.by)
    print(f"{len(rows)} location(s): {added} added, {replaced} replaced, {len(rows) - added} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
