"""Chart of accounts layout: how a concatenated account string splits into segments.

The mock uses 4 segments joined by "-" (01-510-6420-0000); Krispy Kreme uses 8 joined by "."
(10.20.00110.61210.00000.000.00.000, made up). Everything that splits, joins or reads a segment of an account
goes through `CoaLayout`, so the layout is one setting (GL_SEGMENT_COUNT, GL_SEGMENT_SEPARATOR,
GL_*_SEGMENT) and not a `split("-")` in every module.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_SEGMENTS = 30          # GL_CODE_COMBINATIONS.SEGMENT1..SEGMENT30


@dataclass(frozen=True)
class CoaLayout:
    segment_count: int = 4
    separator: str = "-"
    company_segment: int = 1
    cost_center_segment: int = 2
    natural_account_segment: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.segment_count <= MAX_SEGMENTS:
            raise ValueError(f"segment count {self.segment_count} is not between 1 and {MAX_SEGMENTS}")
        if len(self.separator) != 1 or self.separator.isalnum():
            raise ValueError(f"segment separator {self.separator!r} must be one non-alphanumeric character")
        for name in ("company_segment", "cost_center_segment", "natural_account_segment"):
            n = getattr(self, name)
            if not 1 <= n <= self.segment_count:
                raise ValueError(f"{name} {n} is not between 1 and {self.segment_count}")

    # ------------------------------------------------------------ parsing
    def split(self, account: str | None) -> list[str] | None:
        """The segments of an account in this layout, or None when it does not have the right shape."""
        if not account:
            return None
        parts = account.strip().split(self.separator)
        if len(parts) != self.segment_count or any(not p.strip() for p in parts):
            return None
        return [p.strip() for p in parts]

    def join(self, segments: list[str]) -> str:
        if len(segments) != self.segment_count:
            raise ValueError(f"{len(segments)} segments given, the layout has {self.segment_count}")
        return self.separator.join(segments)

    def valid_shape(self, account: str | None) -> bool:
        return self.split(account) is not None

    # ------------------------------------------------------------ segments
    def segment(self, account: str | None, n: int) -> str | None:
        parts = self.split(account)
        return parts[n - 1] if parts and 1 <= n <= len(parts) else None

    def natural_account(self, account: str | None) -> str | None:
        return self.segment(account, self.natural_account_segment)

    def cost_center(self, account: str | None) -> str | None:
        return self.segment(account, self.cost_center_segment)

    def company(self, account: str | None) -> str | None:
        return self.segment(account, self.company_segment)

    def replace(self, account: str | None, n: int, value: str) -> str | None:
        """The account with segment n replaced; None when the account does not have the layout's shape."""
        parts = self.split(account)
        if parts is None or not 1 <= n <= len(parts):
            return None
        parts[n - 1] = value
        return self.join(parts)

    def with_natural_account(self, account: str | None, natural_account: str) -> str | None:
        return self.replace(account, self.natural_account_segment, natural_account)

    def with_cost_center(self, account: str | None, cost_center: str) -> str | None:
        return self.replace(account, self.cost_center_segment, cost_center)

    def columns(self) -> list[str]:
        """GL_CODE_COMBINATIONS column names of the layout's segments."""
        return [f"segment{i}" for i in range(1, self.segment_count + 1)]


def layout_from_settings() -> CoaLayout:
    from aap.config import get_settings

    s = get_settings()
    return CoaLayout(segment_count=s.gl_segment_count, separator=s.gl_segment_separator,
                     company_segment=s.gl_company_segment, cost_center_segment=s.gl_cost_center_segment,
                     natural_account_segment=s.gl_natural_account_segment)
