"""Baseline schema for the aap database (plan section 4).

Revision ID: 0001
Revises:
Create Date: 2026-09-28

The baseline is created from the model metadata as it stood at this revision.
Every later schema change must be its own revision (alembic revision --autogenerate);
never edit models without adding one, or a fresh database and an upgraded one diverge.
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from aap.db.models import Base

    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)
    # R-05 / ARCHITECTURE 8.2: the application role may only add audit rows.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aap_app') THEN
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM aap_app;
            GRANT SELECT, INSERT ON audit_event TO aap_app;
          END IF;
        END $$""")


def downgrade() -> None:
    from aap.db.models import Base

    Base.metadata.drop_all(bind=op.get_bind())
