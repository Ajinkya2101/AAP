"""ERP adapters. Use get_read_adapter() / get_write_adapter(); never import an implementation directly."""

from __future__ import annotations

from functools import lru_cache

from aap.config import get_settings

from .base import ErpReadPort, ErpWritePort


@lru_cache
def get_read_adapter() -> ErpReadPort:
    settings = get_settings()
    if settings.adapter == "ebs_mock":
        from .ebs_mock import MockEbsAdapter

        return MockEbsAdapter(settings.ebs_ro_dsn, settings.ebs_intf_dsn)
    raise ValueError(f"unknown ADAPTER {settings.adapter}")


def get_write_adapter() -> ErpWritePort:
    return get_read_adapter()  # type: ignore[return-value]
