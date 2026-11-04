"""Chart of accounts structure, payment methods and the new interface columns in the mock EBS
(additions.sql, 2026-11-04), through the adapter, the reference sync and the mock import."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row
from sqlalchemy import select

from aap.adapters.ebs_mock import MockEbsAdapter
from aap.db.models import RefCcid, RefPaymentMethod, RefSupplierSite
from aap.db.session import session_scope
from aap.ebs_mock.ddl_loader import refresh_apis
from aap.ebs_mock.import_program import _ccid_from_concat

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def adapter(seeded_ebs, env):
    a = MockEbsAdapter(env["EBS_RO_DSN"])
    yield a
    a.close()


def test_flex_structure_and_account_view(env, seeded_ebs):
    with psycopg.connect(env["EBS_OWNER_DSN"], row_factory=dict_row) as conn:
        st = conn.execute("SELECT * FROM fnd.fnd_id_flex_structures").fetchall()
        assert [s["concatenated_segment_delimiter"] for s in st] == ["-"]
        segs = conn.execute("SELECT application_column_name, segment_name FROM fnd.fnd_id_flex_segments "
                            "ORDER BY segment_num").fetchall()
        assert [s["segment_name"] for s in segs] == ["Company", "Cost Centre", "Account", "Sub-account"]
        kfv = conn.execute("SELECT concatenated_segments FROM apps.gl_code_combinations_kfv LIMIT 1").fetchone()
        assert kfv["concatenated_segments"].count("-") == 3
        coa = conn.execute("SELECT chart_of_accounts_id FROM gl.gl_ledgers LIMIT 1").fetchone()["chart_of_accounts_id"]
        assert _ccid_from_concat(conn, "01-510-6420-0000", coa) is not None
        assert _ccid_from_concat(conn, "01.510.6420.0000", coa) is None
        refresh_apis(conn)                 # idempotent on a seeded database
        assert len(conn.execute("SELECT * FROM fnd.fnd_id_flex_segments").fetchall()) == 4


def test_reference_data_has_segments_payment_methods_and_site_defaults(adapter, aap_db):
    data = adapter.load_reference_data()
    assert {m["payment_method_code"] for m in data.payment_methods} >= {"CHECK", "EFT", "WIRE"}
    ccid = next(c for c in data.ccids if c["concatenated"] == "01-510-6420-0000")
    assert ccid["segments"] == ["01", "510", "6420", "0000"] and ccid["natural_account"] == "6420"
    assert ccid["account_desc"] and ccid["cost_centre_desc"]
    assert adapter.ccid_valid("01-510-6420-0000", date(2026, 9, 1)).valid
    with session_scope() as s:
        row = s.scalar(select(RefCcid).where(RefCcid.concatenated == "01-510-6420-0000"))
        assert row is not None and row.natural_account == "6420" and row.segments == ["01", "510", "6420", "0000"]
        assert s.get(RefPaymentMethod, "EFT") is not None
        assert s.scalar(select(RefSupplierSite).where(RefSupplierSite.payment_method.is_not(None)).limit(1))
