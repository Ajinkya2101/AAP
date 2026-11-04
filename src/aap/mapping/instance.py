"""Value map from the instance the reference data comes from (Krispy Kreme) to the instance the
invoice is created in (the internal instance, later the client's).

Reference data, history and the keyed invoices all come from the Krispy Kreme instance, so the
pipeline works in its values. If the target instance is set up with the same values (supplier
numbers, site codes, term names, payment methods, account segments), no map is needed and the
Oracle-ready JSON goes in as is. If some differ, the differences are listed in a CSV, one row per
value, and the JSON is translated when it is built:

    kind,scope,source,target
    supplier,,70001,10345            supplier number
    site,70001,NWP-MAIN,NWP-01     site code (scope = the supplier number in the source)
    terms,,30 NET,Net 30
    payment_method,,EFT,ELECTRONIC
    pay_group,,STANDARD,Standard
    operating_unit,,KK US OU,Vision Operations
    invoice_type,,STANDARD,STANDARD
    line_type,,FREIGHT,FREIGHT
    account,,10.20.00110.61210.00000.000.00.000,01-110-7110-0000-000   a whole account
    segment3,,00110,00950            one segment value (same layout in both instances)

`account` rows win over `segment<N>` rows. A kind with no rows at all is taken to be the same in both
instances. A value of a kind that has rows, but not this value, passes through unchanged and the JSON
lists it so it can be checked (see oracle_json.py).
Setting: INSTANCE_MAP_FILE (unset = identity).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from aap.common.coa import CoaLayout

KINDS = {"supplier", "site", "terms", "payment_method", "pay_group", "operating_unit", "invoice_type", "line_type",
         "account"}
_SEGMENT = re.compile(r"segment([1-9]|[12][0-9]|30)")


class InstanceMapError(ValueError):
    pass


@dataclass
class InstanceMap:
    name: str = "same values as the source instance"
    rows: dict[tuple[str, str, str], str] = field(default_factory=dict)   # (kind, scope, source) -> target
    from_file: bool = False

    @property
    def identity(self) -> bool:
        return not self.rows

    def covers(self, kind: str) -> bool:
        """True when the map has rows of this kind (accounts: account or segment rows)."""
        if kind == "account":
            return any(k == "account" or _SEGMENT.fullmatch(k) for k, _, _ in self.rows)
        return any(k == kind for k, _, _ in self.rows)

    def lookup(self, kind: str, value: str | None, scope: str | None = None) -> tuple[str | None, bool]:
        """(target value, found). A value without a row comes back unchanged with found=False."""
        if value is None:
            return None, True
        key = value.strip().upper()
        for s in ((scope or "").strip().upper(), ""):
            hit = self.rows.get((kind, s, key))
            if hit is not None:
                return hit, True
        return value, not self.covers(kind)

    def account(self, account: str | None, coa: CoaLayout) -> tuple[str | None, bool]:
        """A whole-account row, else segment rows one by one (both instances in the same layout)."""
        if account is None:
            return None, True
        whole = self.rows.get(("account", "", account.strip().upper()))
        if whole is not None:
            return whole, True
        parts = coa.split(account)
        if parts is None:
            return account, not self.covers("account")
        found = not self.covers("account")
        out = []
        for n, value in enumerate(parts, start=1):
            target = self.rows.get((f"segment{n}", "", value.upper()))
            found = found or target is not None
            out.append(target if target is not None else value)
        return coa.join(out), found


def load_instance_map(path: Path) -> InstanceMap:
    """Read a kind,scope,source,target CSV (scope may be empty or the column left out)."""
    rows: dict[tuple[str, str, str], str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = {c.strip().lower() for c in reader.fieldnames or []}
        if not {"kind", "source", "target"} <= cols:
            raise InstanceMapError(f"{path}: needs the columns kind, source, target (and optionally scope)")
        for n, raw in enumerate(reader, start=2):
            r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            kind = r["kind"].lower()
            if kind not in KINDS and not _SEGMENT.fullmatch(kind):
                raise InstanceMapError(f"{path} line {n}: unknown kind {kind!r}")
            if not r["source"] or not r["target"]:
                raise InstanceMapError(f"{path} line {n}: source and target are required")
            key = (kind, r.get("scope", "").upper(), r["source"].upper())
            if key in rows and rows[key] != r["target"]:
                raise InstanceMapError(f"{path} line {n}: {kind} {r['source']!r} is mapped twice")
            rows[key] = r["target"]
    return InstanceMap(name=path.stem, rows=rows, from_file=True)


def instance_map_from_settings() -> InstanceMap:
    from aap.config import get_settings

    path = get_settings().instance_map_file
    return load_instance_map(path) if path else InstanceMap()
