"""Invoice line <-> PO line pairing (WORKED-EXAMPLE 6b).

score = 0.45 x price match + 0.20 x quantity fits + 0.35 x text similarity.
Best overall assignment with the Hungarian algorithm; a pair is accepted when its
score >= MATCH_AUTO_ACCEPT and it is MATCH_MARGIN ahead of the next option.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from aap.adapters.base import PoLine
from aap.common.text import text_key

W_PRICE, W_QTY, W_TEXT = 0.45, 0.20, 0.35
PRICE_BAND = Decimal("0.10")      # price score falls to 0 at a 10% difference


@dataclass
class InvLine:
    line_number: int
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


@dataclass
class Candidate:
    po_line_num: int
    score: float
    price: float
    qty: float
    text: float
    col: int = 0          # index into the PO lines scored (lines of several POs can share a line number)


def price_score(inv: Decimal | None, po: Decimal) -> float:
    if inv is None or po == 0:
        return 0.0
    rel = abs(Decimal(inv) - po) / po
    return float(max(Decimal(0), 1 - rel / PRICE_BAND))


def qty_fits(inv: Decimal | None, line: PoLine) -> float:
    if inv is None:
        return 0.5
    return 1.0 if Decimal(inv) <= line.ordered - line.billed else 0.0


def text_sim(a: str, b: str) -> float:
    return fuzz.token_set_ratio(text_key(a), text_key(b)) / 100


def score_matrix(inv_lines: list[InvLine], po_lines: list[PoLine]) -> list[list[Candidate]]:
    rows = []
    for il in inv_lines:
        row = []
        for j, pl in enumerate(po_lines):
            p = price_score(il.unit_price, pl.unit_price)
            qf = qty_fits(il.quantity, pl)
            t = text_sim(il.description, pl.item_description or "")
            row.append(Candidate(pl.line_num, round(W_PRICE * p + W_QTY * qf + W_TEXT * t, 3), p, qf, round(t, 3), j))
        rows.append(row)
    return rows


def assign(matrix: list[list[Candidate]]) -> dict[int, int]:
    """Hungarian assignment: invoice row index -> PO column index."""
    if not matrix or not matrix[0]:
        return {}
    cost = -np.array([[c.score for c in row] for row in matrix])
    r, c = linear_sum_assignment(cost)
    return dict(zip(r.tolist(), c.tolist(), strict=True))
