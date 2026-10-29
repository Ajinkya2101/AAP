"""MockEbsAdapter read side and the reference sync (plan Phase 2 'done when')."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from aap.adapters.ebs_mock import MockEbsAdapter
from aap.db.models import GlHistoryLine, RefCcid, RefSupplier, RefSupplierSite, RefTerms
from aap.db.session import session_scope

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def adapter(seeded_ebs, env):
    a = MockEbsAdapter(env["EBS_RO_DSN"])
    yield a
    a.close()


def test_get_po_and_receipts(adapter):
    po = adapter.get_po("4500123")
    assert po is not None and po.vendor_id == 30418 and po.open_for_invoice
    line1 = po.lines[0]
    assert (line1.ordered, line1.received, line1.unit_price) == (Decimal(10), Decimal(8), Decimal("1150.00"))
    receipts = adapter.get_receipts([s.line_location_id for s in line1.shipments])
    assert {r.transaction_type for r in receipts} == {"RECEIVE", "DELIVER"}
    closed = adapter.get_po("4500171")
    assert closed is not None and not closed.open_for_invoice


def test_search_open_pos_is_ambiguous_for_s3(adapter):
    hits = adapter.search_open_pos(30418, "USD", Decimal("3600.00"), [Decimal("250.00"), Decimal("110.00")])
    numbers = {h.po_number for h in hits}
    assert {"4500187", "4500188"} <= numbers


def test_duplicate_ccid_rate_contacts(adapter, aap_db):
    with session_scope() as s:
        contoso = s.scalar(select(RefSupplier.vendor_id).where(RefSupplier.vendor_name == "Contoso Office Products LLC"))
    assert contoso is not None
    hits = adapter.find_duplicate(contoso, "ct-88412")
    assert hits and hits[0].reason == "same_number"
    assert adapter.ccid_valid("01-510-6420-0000", date(2026, 9, 1)).valid
    assert not adapter.ccid_valid("99-999-9999-0000").exists
    assert adapter.get_exchange_rate("EUR", "USD", date(2026, 9, 17)) is not None
    site = adapter.get_po("4500123").vendor_site_id
    contacts = adapter.get_supplier_contacts(site)
    assert contacts is not None and "billing@northwind.example" in contacts.emails


def test_reference_sync_fills_ref_tables(aap_db):
    with session_scope() as s:
        nw = s.get(RefSupplier, 30418)
        assert nw is not None and nw.tax_id == "84-1234567" and nw.po_required
        assert s.scalar(select(func.count()).select_from(RefSupplierSite)
                        .where(RefSupplierSite.vendor_id == 30418)) == 3
        assert s.scalar(select(func.count()).select_from(RefTerms)) == 12
        assert s.scalar(select(func.count()).select_from(RefCcid)) > 300
        assert s.scalar(select(func.count()).select_from(GlHistoryLine)) > 100
        site = s.scalar(select(RefSupplierSite).where(RefSupplierSite.vendor_id == 30418,
                                                      RefSupplierSite.vendor_site_code == "DALLAS-HQ"))
        assert "northwind.example" in site.email_domains


def _blanket_number(adapter) -> str:
    rows = adapter._all("""SELECT h.segment1 FROM po_headers_all h
                           JOIN po_releases_all r ON r.po_header_id = h.po_header_id
                           WHERE h.type_lookup_code = 'BLANKET' AND h.org_id = 204
                           ORDER BY h.segment1 LIMIT 1""")
    assert rows, "the seed has blanket agreements with releases"
    return rows[0]["segment1"]


def test_get_po_reads_blanket_releases(adapter):
    po = adapter.get_po(_blanket_number(adapter))
    assert po is not None and po.blanket and po.releases
    rel = po.releases[0]
    view = po.for_release(rel.release_num)
    assert view.lines and all(s.release_num == rel.release_num for ln in view.lines for s in ln.shipments)
    assert all(s.ccid_concatenated for ln in view.lines for s in ln.shipments)
