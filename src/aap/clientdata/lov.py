"""List-of-values workbook -> FND_LOOKUP_VALUES rows (IMPLEMENTATION-PLAN-CLIENT-DATA P3.3).

    python -m aap.clientdata.lov <workbook.xlsx> [--out <delivery folder or .csv>]

The functional team sends the eligible values of the invoice screen as a workbook rather than a
table extract (Siva, 2026-11-04: "column name, and below it the eligible values", one tab for the
header, one for the lines). Two layouts are read, sheet by sheet, block by block (blocks are
separated by empty rows):

- **columns**: the block's first row has several names; each column is one list, its values below
  ("Invoice Type" | "Line Type" | "Source", values underneath);
- **sections**: the block's first row has one name (the list's title); the next row is a header
  ("Invoice Type" | "Description" | ...) and the rows after it the values, or the values follow one
  per row (Vikram's "AI AP Excel.xlsx" sheet "LOVs").

Lists named like a Payables lookup become FND_LOOKUP_VALUES rows: invoice type, invoice line type,
invoice source ("Type" on a sheet named for the header or the lines means that level's type).
Values given as Workbench meanings ("Credit Memo", "Freight") become Oracle's codes
(common/lookups.py); a value that is neither a seeded code nor a meaning is kept in upper case and
listed for someone to confirm. The other lists (terms, payment methods, currencies, ...) are listed
in the report only: those come from their own extracts.

The CSV (default: `fnd_lookup_values.csv` next to the workbook) is read by the delivery reader like
any extract; the replica loader then replaces the seeded rows of each delivered lookup type.
"""

from __future__ import annotations

import argparse
import csv
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aap.common.lookups import INVOICE_TYPE, LINE_TYPE, ORACLE_SEEDED, SOURCE, resolve_code
from aap.common.text import text_key

NAME_TYPES = {"invoicetype": INVOICE_TYPE, "invoicetypes": INVOICE_TYPE, "linetype": LINE_TYPE,
              "linetypes": LINE_TYPE, "invoicelinetype": LINE_TYPE, "invoicelinetypes": LINE_TYPE,
              "source": SOURCE, "invoicesource": SOURCE, "sources": SOURCE}
OUT_COLUMNS = ["LOOKUP_TYPE", "LOOKUP_CODE", "MEANING", "DESCRIPTION", "ENABLED_FLAG", "SOURCE_VALUE",
               "CODE_CONFIRMED", "FROM"]


@dataclass
class LovList:
    sheet: str
    name: str
    values: list[tuple[str, str | None]] = field(default_factory=list)   # (value, description)

    @property
    def where(self) -> str:
        return f"{self.sheet}!{self.name}"


@dataclass
class LovResult:
    lists: list[LovList]
    rows: list[dict[str, Any]]
    unconfirmed: list[str]            # "<type>: <value>" not a seeded code or meaning
    other_lists: list[str]            # lists not loaded as lookups


def _key(v: Any) -> str:
    return text_key(str(v)).replace(" ", "") if v not in (None, "") else ""


def _cells(row: tuple) -> list[str]:
    return ["" if c is None else " ".join(str(c).split()) for c in row]


def _blocks(rows: list[list[str]]) -> list[list[list[str]]]:
    out, cur = [], []
    for r in rows:
        if any(r):
            cur.append(r)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def read_lists(path: Path) -> list[LovList]:
    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = [(ws.title, [_cells(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    finally:
        wb.close()
    lists: list[LovList] = []
    for title, rows in sheets:
        for block in _blocks(rows):
            first = [(i, c) for i, c in enumerate(block[0]) if c]
            if len(first) == 1:                                   # sections: a title row
                name, rest = first[0][1], block[1:]
                lst = LovList(title, name)
                if rest and sum(1 for c in rest[0] if c) >= 2:     # header row, then values
                    head = [i for i, c in enumerate(rest[0]) if c]
                    vcol, dcol = head[0], (head[1] if len(head) > 1 else None)
                    for r in rest[1:]:
                        if vcol < len(r) and r[vcol]:
                            lst.values.append((r[vcol], (r[dcol] or None) if dcol is not None and dcol < len(r)
                                               else None))
                else:                                             # one value per row
                    lst.values += [(next(c for c in r if c), None) for r in rest]
                lists.append(lst)
            else:                                                 # columns: one list per column
                for i, name in first:
                    lst = LovList(title, name, [(r[i], None) for r in block[1:] if i < len(r) and r[i]])
                    lists.append(lst)
    return lists


def lookup_type_of(lst: LovList) -> str | None:
    k = _key(lst.name)
    if k in NAME_TYPES:
        return NAME_TYPES[k]
    if k in ("type", "types"):
        sheet = _key(lst.sheet)
        if "header" in sheet or ("invoice" in sheet and "line" not in sheet):
            return INVOICE_TYPE
        if "line" in sheet:
            return LINE_TYPE
    return None


def convert(path: Path) -> LovResult:
    lists = read_lists(path)
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    unconfirmed, other = [], []
    for lst in lists:
        lookup_type = lookup_type_of(lst)
        if lookup_type is None:
            if lst.values:
                other.append(f"{lst.where} ({len(lst.values)} values)")
            continue
        seeded = {c: m for c, m, _ in ORACLE_SEEDED.get(lookup_type, [])}
        for value, description in lst.values:
            code, known = resolve_code(lookup_type, value)
            if not known and lookup_type in ORACLE_SEEDED:
                unconfirmed.append(f"{lookup_type}: {value!r} -> {code}")
            row = seen.setdefault((lookup_type, code), {
                "LOOKUP_TYPE": lookup_type, "LOOKUP_CODE": code, "MEANING": seeded.get(code) or value,
                "DESCRIPTION": description, "ENABLED_FLAG": "Y", "SOURCE_VALUE": value,
                "CODE_CONFIRMED": "Y" if known or lookup_type not in ORACLE_SEEDED else "N", "FROM": lst.where})
            if description and not row["DESCRIPTION"]:
                row["DESCRIPTION"] = description
    rows = sorted(seen.values(), key=lambda r: (r["LOOKUP_TYPE"], r["LOOKUP_CODE"]))
    return LovResult(lists=lists, rows=rows, unconfirmed=unconfirmed, other_lists=other)


def write_csv(rows: list[dict[str, Any]], out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def report(r: LovResult) -> str:
    out = []
    by_type: dict[str, list[str]] = {}
    for row in r.rows:
        by_type.setdefault(row["LOOKUP_TYPE"], []).append(row["LOOKUP_CODE"])
    for t, codes in sorted(by_type.items()):
        out.append(f"{t}: {len(codes)} code(s): {', '.join(codes)}")
    if not by_type:
        out.append("no invoice type, line type or source list found")
    if r.unconfirmed:
        out.append("not a seeded code or meaning, kept in upper case (confirm with the functional team):")
        out += [f"  {u}" for u in r.unconfirmed]
    if r.other_lists:
        out.append("other lists, not loaded as lookups: " + "; ".join(r.other_lists))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.clientdata.lov", description=__doc__.split("\n")[0])
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--out", type=Path, default=None,
                    help="a delivery folder or a .csv file (default: fnd_lookup_values.csv next to the workbook)")
    ap.add_argument("--dry-run", action="store_true", help="only print the report")
    a = ap.parse_args(argv)
    r = convert(a.workbook)
    print(report(r))
    if a.dry_run or not r.rows:
        return 0 if r.rows else 1
    out = a.out or a.workbook.with_name("fnd_lookup_values.csv")
    if out.suffix.lower() != ".csv":
        out = out / "fnd_lookup_values.csv"
    write_csv(r.rows, out)
    print(f"wrote {len(r.rows)} row(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
