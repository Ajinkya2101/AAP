"""Graph state. JSON-friendly values only (ids as str, money as str) so the
Postgres checkpointer can store every step."""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    document_id: str
    version_id: str            # invoice_version the agents work on
    run_id: str                # agent_run.id
    route: str                 # po | non_po | review
    reasons: list[str]         # reason codes for the review task
    notes: list[str]           # human-readable findings (shown with the review task)
    suggested_action: str | None
    po_number: str | None
    po_source: str | None      # invoice | search
    pairs: list[dict[str, Any]]
    unmatched: list[int]
    variances: list[dict[str, Any]]
    gl_lines: list[dict[str, Any]]
    outcome: str | None


# Reason codes (review screen, plan section 8).
class Reason:
    BANK_CHANGE = "BANK_CHANGE"
    UNKNOWN_SENDER = "UNKNOWN_SENDER"
    DUPLICATE = "DUPLICATE"
    SUPPLIER_INACTIVE = "SUPPLIER_INACTIVE"
    PO_NOT_OPEN = "PO_NOT_OPEN"
    INVOICE_MATH = "INVOICE_MATH"
    DATA_MISSING = "DATA_MISSING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    DATE_AMBIGUOUS = "DATE_AMBIGUOUS"
    CHECK_MAPPING = "CHECK_MAPPING"
    PO_MISSING = "PO_MISSING"
    PO_SUGGESTED = "PO_SUGGESTED"
    LINE_NOT_ON_PO = "LINE_NOT_ON_PO"
    LINE_PAIRED_BY_MODEL = "LINE_PAIRED_BY_MODEL"
    QTY_VARIANCE = "QTY_VARIANCE"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    GL_REVIEW = "GL_REVIEW"
