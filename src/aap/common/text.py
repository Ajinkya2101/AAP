"""Text keys used for exact and fuzzy matching."""

from __future__ import annotations

import re
import unicodedata

LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh",
    "ag", "bv", "nv", "sa", "sas", "srl", "pvt", "private", "llp", "lp", "pte", "kg", "the",
}


def fold(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def name_key(name: str | None) -> str:
    """'NORTHWIND IT SUPPLIES INC.' -> 'northwind it supplies'."""
    if not name:
        return ""
    words = re.sub(r"[^a-z0-9& ]+", " ", fold(name).replace("&", " & ")).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def text_key(text: str | None) -> str:
    """Generic key for value maps: folded, punctuation removed."""
    if not text:
        return ""
    return " ".join(re.sub(r"[^a-z0-9%./ ]+", " ", fold(text)).split())


def email_domain(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    return address.rsplit("@", 1)[1].strip().strip(">").lower() or None
