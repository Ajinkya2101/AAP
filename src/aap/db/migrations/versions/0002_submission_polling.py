"""Phase 9: submission keeps the interface row id and polling state.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28

IF NOT EXISTS: 0001 builds from the live model metadata, so on a fresh database
these columns already exist when this revision runs.
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE submission ADD COLUMN IF NOT EXISTS interface_invoice_id bigint")
    op.execute("ALTER TABLE submission ADD COLUMN IF NOT EXISTS oracle_holds jsonb")
    op.execute("ALTER TABLE submission ADD COLUMN IF NOT EXISTS polls integer NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE submission DROP COLUMN IF EXISTS polls")
    op.execute("ALTER TABLE submission DROP COLUMN IF EXISTS oracle_holds")
    op.execute("ALTER TABLE submission DROP COLUMN IF EXISTS interface_invoice_id")
