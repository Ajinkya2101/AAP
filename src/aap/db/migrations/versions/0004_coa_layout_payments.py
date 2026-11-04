"""Chart of accounts of any layout, payment defaults of supplier sites, payment methods.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04

ref_ccid gets every segment (an 8-segment chart does not fit segment1-4) and the natural-account
value; ref_supplier_site gets the payment defaults the Oracle-ready JSON needs; ref_payment_method
holds IBY_PAYMENT_METHODS. IF NOT EXISTS: 0001 builds from the live model metadata, so on a fresh
database these objects already exist. The ref_* tables are refilled by the next reference sync.
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ref_ccid ADD COLUMN IF NOT EXISTS segments varchar(25)[]")
    op.execute("ALTER TABLE ref_ccid ADD COLUMN IF NOT EXISTS natural_account varchar(25)")
    op.execute("ALTER TABLE ref_ccid ALTER COLUMN segment4 DROP NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ref_ccid_natural_account ON ref_ccid (natural_account)")
    # Rows synced before this revision: the mock's natural account is segment 3.
    op.execute("UPDATE ref_ccid SET natural_account = segment3, "
               "segments = ARRAY[segment1, segment2, segment3, segment4] WHERE natural_account IS NULL")
    for column, kind in (("payment_method", "varchar(30)"), ("pay_group", "varchar(25)"),
                         ("payment_currency_code", "varchar(15)"), ("terms_date_basis", "varchar(25)")):
        op.execute(f"ALTER TABLE ref_supplier_site ADD COLUMN IF NOT EXISTS {column} {kind}")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ref_payment_method (
            payment_method_code varchar(30) PRIMARY KEY,
            name varchar(100),
            description varchar(255),
            enabled boolean NOT NULL
        )""")    # aap_app gets access through the owner's default privileges (docker/postgres/init)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ref_payment_method")
    for column in ("terms_date_basis", "payment_currency_code", "pay_group", "payment_method"):
        op.execute(f"ALTER TABLE ref_supplier_site DROP COLUMN IF EXISTS {column}")
    op.execute("DROP INDEX IF EXISTS ix_ref_ccid_natural_account")
    op.execute("ALTER TABLE ref_ccid DROP COLUMN IF EXISTS natural_account")
    op.execute("ALTER TABLE ref_ccid DROP COLUMN IF EXISTS segments")
