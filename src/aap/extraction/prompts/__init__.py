"""Versioned prompt files: prompts/<version>/<name>.txt (ARCHITECTURE 6.3)."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

CURRENT_VERSION = "v3"   # v2: bank-detail labels, no invented qty/price, close the line list
                         # v3: value not label, payment terms, PO labels, subtotal only when printed


@lru_cache
def load_prompt(name: str, version: str = CURRENT_VERSION) -> str:
    return resources.files("aap.extraction.prompts").joinpath(version, f"{name}.txt").read_text(encoding="utf-8").strip()
