"""File type from content, not from the extension (ARCHITECTURE 6.2, TECH-STACK 7)."""

from __future__ import annotations

import csv
import io
import re
import zipfile

import filetype

ACCEPTED = {"pdf", "png", "jpg", "tiff", "xlsx", "xls", "csv", "eml", "msg", "txt", "html"}
CONTAINERS = {"eml", "msg"}

MIME = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "tiff": "image/tiff",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel", "csv": "text/csv", "eml": "message/rfc822",
    "msg": "application/vnd.ms-outlook", "txt": "text/plain", "html": "text/html", "zip": "application/zip",
}

_EMAIL_HEADER = re.compile(rb"^(received|return-path|from|to|subject|date|message-id|mime-version|delivered-to"
                           rb"|x-[a-z-]+|content-type|reply-to|auto-submitted):", re.I | re.M)
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect(data: bytes, filename: str | None = None) -> str:
    """Returns one of ACCEPTED, 'zip', or 'unknown'."""
    head = data[:8192]
    if head.startswith(b"%PDF-") or b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return _ole_kind(data)
    kind = filetype.guess(head)
    if kind is not None:
        ext = kind.extension
        if ext == "png":
            return "png"
        if ext in ("jpg", "jpeg"):
            return "jpg"
        if ext in ("tif", "tiff"):
            return "tiff"
        if ext == "zip" or ext == "xlsx" or kind.mime.startswith("application/vnd.openxmlformats"):
            return _zip_kind(data)
        if ext == "xls":
            return "xls"
        return "unknown"
    return _text_kind(head)


def _zip_kind(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return "unknown"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return "zip"


def _ole_kind(data: bytes) -> str:
    # Compound File Binary: Outlook .msg streams are named __substg1.0_*; Excel has "Workbook".
    if b"_\x00_\x00s\x00u\x00b\x00s\x00t\x00g\x001\x00" in data[:200000] or b"__substg1.0_" in data[:200000]:
        return "msg"
    if b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k" in data[:200000] or b"B\x00o\x00o\x00k" in data[:200000]:
        return "xls"
    return "unknown"


def _text_kind(head: bytes) -> str:
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        text = head.decode("latin-1", errors="replace")
    stripped = text.lstrip()
    if len(_EMAIL_HEADER.findall(head[:4096])) >= 3:
        return "eml"
    low = stripped[:500].lower()
    if low.startswith("<!doctype html") or low.startswith("<html") or "<body" in low:
        return "html"
    if "\x00" in text:
        return "unknown"
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if len(lines) >= 2:
        try:
            dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=",;\t|")
            counts = {len(row) for row in csv.reader(lines, dialect)}
            if len(counts) <= 2 and max(counts) >= 2:
                return "csv"
        except csv.Error:
            pass
    return "txt"
