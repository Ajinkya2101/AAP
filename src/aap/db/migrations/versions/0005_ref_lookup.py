"""Payables lookups in the reference data (invoice types, invoice line types, sources).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04

ref_lookup holds the instance's FND_LOOKUP_VALUES for the lookup types the invoice's codes come from
(common/lookups.py); validation rule V-LOV checks every invoice against it. IF NOT EXISTS: 0001 builds
from the live model metadata, so on a fresh database the table already exists. Filled by the next
reference sync.
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ref_lookup (
            lookup_type varchar(30) NOT NULL,
            lookup_code varchar(30) NOT NULL,
            meaning varchar(80),
            description varchar(240),
            enabled boolean NOT NULL,
            PRIMARY KEY (lookup_type, lookup_code)
        )""")    # aap_app gets access through the owner's default privileges (docker/postgres/init)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ref_lookup")
