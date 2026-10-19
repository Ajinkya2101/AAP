"""XLSX / XLS / CSV: each sheet becomes one page holding a Markdown table.
Macros are never run (openpyxl/xlrd only read cell values)."""

from __future__ import annotations

import io

import pandas as pd
from charset_normalizer import from_bytes

from .common import ParsedPage, ParseError

MAX_ROWS = 500


def _to_markdown(df: pd.DataFrame) -> str:
    df = df.dropna(how="all").dropna(axis=1, how="all").head(MAX_ROWS).fillna("")
    return df.to_markdown(index=False)


def parse_sheet(data: bytes, file_type: str) -> list[ParsedPage]:
    try:
        if file_type == "csv":
            best = from_bytes(data).best()
            text = str(best) if best is not None else data.decode("utf-8", errors="replace")
            frames = {"csv": pd.read_csv(io.StringIO(text), sep=None, engine="python", header=None, dtype=str)}
        else:
            engine = "openpyxl" if file_type == "xlsx" else "xlrd"
            frames = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str, engine=engine)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"cannot read {file_type}: {exc}") from exc
    pages = []
    for i, (name, df) in enumerate(frames.items(), start=1):
        md = _to_markdown(df)
        if not md.strip():
            continue
        pages.append(ParsedPage(page_no=len(pages) + 1, kind="sheet", text=f"Sheet: {name}\n\n{md}", tables=[md],
                                meta={"sheet": str(name), "sheet_index": i}))
    if not pages:
        raise ParseError("spreadsheet has no data")
    return pages
