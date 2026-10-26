"""Field[T]: a value plus its provenance (ARCHITECTURE 5, R-03)."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField

T = TypeVar("T")


class Method(StrEnum):
    extracted = "extracted"
    normalized = "normalized"
    value_map = "value_map"
    exact = "exact"            # exact match against the reference copy
    fuzzy = "fuzzy"            # single clear fuzzy candidate
    fuzzy_llm = "fuzzy_llm"    # LLM chose among fuzzy candidates
    derived = "derived"        # computed from other fields
    default = "default"
    user = "user"
    vendor_reply = "vendor_reply"


class Flag:
    """Well-known flag names (the review screen shows these)."""

    not_found_in_source = "not_found_in_source"
    two_date_formats = "two_date_formats"
    low_match_score = "low_match_score"
    check_map = "check_map"
    no_map = "no_map"
    multi_request = "multi_request"
    unverified_image_only = "unverified_image_only"
    unknown_sender = "unknown_sender"
    multiple_invoices = "multiple_invoices"
    amount_parse_error = "amount_parse_error"
    date_parse_error = "date_parse_error"
    currency_ambiguous = "currency_ambiguous"
    currency_from_vendor_default = "currency_from_vendor_default"
    missing = "missing"
    terms_due_date_conflict = "terms_due_date_conflict"
    sign_from_document_type = "sign_from_document_type"   # credit memo printed with positive amounts
    price_basis = "price_basis"                           # unit price printed per 100 / per 1000 units
    implausible_value = "implausible_value"               # read, but cannot be real (e.g. bank account 000)


class Field(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    source_value: str | None = None
    page: int | None = None
    bbox: list[float] | None = None
    method: Method = Method.extracted
    flags: list[str] = PydField(default_factory=list)
    confidence: float | None = None
    note: str | None = None

    def with_flag(self, *flags: str) -> Field[T]:
        for f in flags:
            if f not in self.flags:
                self.flags.append(f)
        return self

    @property
    def present(self) -> bool:
        return self.value is not None and self.value != ""
