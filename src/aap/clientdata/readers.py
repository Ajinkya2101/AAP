"""Read a delivery folder: every CSV / XLSX extract as rows keyed by EBS column names, plus the
attachments. Values are kept as text (stripped; empty -> None), except what Excel stores as numbers
and dates; `num`, `dec` and `day` convert when a check needs it."""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .tables import ATTACHMENTS_DIR, TABLES, TableSpec, table_for

Row = dict[str, Any]
_ATTACHMENT_ID = re.compile(r"^(\d+)(?:[_\-. ].*)?$")
DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y", "%d-%b-%y", "%d-%b-%Y %H:%M:%S", "%m/%d/%Y",
                "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y", "%Y/%m/%d", "%m%d%Y")


@dataclass
class TableData:
    spec: TableSpec
    rows: list[Row]
    files: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


@dataclass
class Attachment:
    invoice_id: int
    path: Path
    sha256: str
    size: int


@dataclass
class Delivery:
    root: Path
    tables: dict[str, TableData] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)
    unknown_files: list[str] = field(default_factory=list)
    unnamed_attachments: list[str] = field(default_factory=list)     # file name without an INVOICE_ID
    files: dict[str, str] = field(default_factory=dict)              # relative path -> sha256

    def rows(self, table: str) -> list[Row]:
        t = self.tables.get(table)
        return t.rows if t else []

    def has(self, table: str) -> bool:
        return table in self.tables


# ------------------------------------------------------------- conversions
def num(v: Any) -> int | None:
    """An ID or count: 70001, "70001", 70001.0 -> 70001."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    d = dec(v)
    return int(d) if d is not None and d == d.to_integral_value() else None


def dec(v: Any) -> Decimal | None:
    """An amount: 1234.5, "1,234.50", "(12.00)" -> Decimal."""
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int | float):
        return Decimal(str(v))
    text = str(v).strip().replace(",", "").replace("$", "")
    neg = text.startswith("(") and text.endswith(")")
    try:
        d = Decimal(text.strip("()"))
    except InvalidOperation:
        return None
    return -d if neg else d


def day(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _clean(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


# ------------------------------------------------------------------ files
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _normalize(spec: TableSpec, header: list[Any]) -> list[str]:
    cols = []
    for h in header:
        name = re.sub(r"\s+", "_", str(h or "").strip()).upper()
        cols.append(spec.columns_alt.get(name, name))
    return cols


def _csv_rows(path: Path) -> list[list[Any]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(text.splitlines(), dialect) if any(c.strip() for c in r)]


def _xlsx_sheets(path: Path) -> list[tuple[str, list[list[Any]]]]:
    import warnings

    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")          # "Workbook contains no default style" (EBS exports)
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        out = []
        for ws in wb.worksheets:
            ws.reset_dimensions()       # SQL Developer exports record the size as A1: read every row
            out.append((ws.title, [list(r) for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]))
        return out
    finally:
        wb.close()


def _add(delivery: Delivery, spec: TableSpec, raw: list[list[Any]], source: str) -> None:
    if not raw:
        return
    cols = _normalize(spec, raw[0])
    rows = [{c: _clean(v) for c, v in zip(cols, r, strict=False) if c} for r in raw[1:]]
    if "LANGUAGE" in cols:            # *_TL extracts: one row per language; keep the base language
        rows = [r for r in rows if (r.get("LANGUAGE") or "US") == "US"]
    t = delivery.tables.setdefault(spec.name, TableData(spec=spec, rows=[], columns=cols))
    t.rows += rows
    t.files.append(source)
    t.columns = list(dict.fromkeys([*t.columns, *cols]))


def read_delivery(root: Path) -> Delivery:
    """Every extract and attachment under `root` (manifest.json and *.md / *.txt notes are skipped)."""
    root = Path(root)
    d = Delivery(root=root)
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in ("manifest.json", "check-report.md") or path.name.startswith(("~$", ".")):
            continue
        d.files[rel] = sha256(path)
        if path.parent.name.lower() == ATTACHMENTS_DIR or ATTACHMENTS_DIR in [p.lower() for p in path.parts[:-1]]:
            m = _ATTACHMENT_ID.match(path.stem)
            if m:
                d.attachments.append(Attachment(int(m.group(1)), path, d.files[rel], path.stat().st_size))
            else:
                d.unnamed_attachments.append(rel)
            continue
        suffix = path.suffix.lower()
        if suffix in (".csv", ".txt") and table_for(path.stem):
            _add(d, table_for(path.stem), _csv_rows(path), rel)  # type: ignore[arg-type]
        elif suffix in (".xlsx", ".xlsm"):
            # SQL Developer exports: the rows in "Export Worksheet", the query in a sheet named "SQL"
            sheets = [(t, raw) for t, raw in _xlsx_sheets(path) if t.strip().upper() != "SQL"]
            for title, raw in sheets:
                # a sheet named like a table, else the file's name when the workbook has one sheet
                spec = table_for(title) or (table_for(path.stem) if len(sheets) == 1 else None)
                if spec:
                    _add(d, spec, raw, f"{rel}#{title}")
                else:
                    d.unknown_files.append(f"{rel}#{title}")
        elif suffix not in (".md", ".txt"):
            d.unknown_files.append(rel)
    return d


__all__ = ["TABLES", "Attachment", "Delivery", "Row", "TableData", "day", "dec", "num", "read_delivery", "sha256"]
