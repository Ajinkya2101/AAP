"""GL history and reference data for the context pack (PLAN-MODEL-JSON §4).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12

gl_history_line gets the site, invoice, GL date, line and distribution set of each distribution, so a
context pack can be built "as of" an invoice's GL date per site. ref_supplier_site gets the site's
default distribution set; ref_distribution_set holds AP_DISTRIBUTION_SETS_ALL with its lines.

Rows synced before this revision keep null in the new columns; the next reference sync fills them
(refsync._fill_history). IF NOT EXISTS: 0001 builds from the live model metadata.
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

HISTORY_COLUMNS = (("vendor_site_id", "bigint"), ("invoice_id", "bigint"), ("invoice_num", "varchar(50)"),
                   ("invoice_type", "varchar(25)"), ("gl_date", "date"), ("line_number", "integer"),
                   ("line_type", "varchar(25)"), ("distribution_set_id", "bigint"))


def upgrade() -> None:
    for column, kind in HISTORY_COLUMNS:
        op.execute(f"ALTER TABLE gl_history_line ADD COLUMN IF NOT EXISTS {column} {kind}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gl_history_line_vendor_gl_date ON gl_history_line (vendor_id, gl_date)")
    op.execute("ALTER TABLE ref_supplier_site ADD COLUMN IF NOT EXISTS distribution_set_id bigint")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ref_distribution_set (
            distribution_set_id bigint PRIMARY KEY,
            name varchar(50),
            description varchar(240),
            org_id bigint,
            enabled boolean NOT NULL,
            lines jsonb
        )""")    # aap_app gets access through the owner's default privileges (docker/postgres/init)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ref_distribution_set")
    op.execute("ALTER TABLE ref_supplier_site DROP COLUMN IF EXISTS distribution_set_id")
    op.execute("DROP INDEX IF EXISTS ix_gl_history_line_vendor_gl_date")
    for column, _ in HISTORY_COLUMNS:
        op.execute(f"ALTER TABLE gl_history_line DROP COLUMN IF EXISTS {column}")
