"""The mailbox and vendor emails are out of scope: drop their tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

Documents that waited for a vendor's reply go back to review (AWAITING_VENDOR no longer
exists), with the history row, audit event and review task the audit check expects.
Open "approve the vendor email" review tasks keep their document in IN_REVIEW; only
their wording is out of date.
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO document_status_history (document_id, from_status, to_status, reason)
        SELECT id, 'AWAITING_VENDOR', 'IN_REVIEW', 'vendor email removed' FROM document
        WHERE status = 'AWAITING_VENDOR'""")
    op.execute("""
        INSERT INTO audit_event (document_id, actor, event_type, payload)
        SELECT id, 'migrate', 'document.status',
               '{"from_status": "AWAITING_VENDOR", "to_status": "IN_REVIEW", "reason": "vendor email removed"}'::jsonb
        FROM document WHERE status = 'AWAITING_VENDOR'""")
    op.execute("""
        INSERT INTO review_task (id, document_id, reason_codes, summary, suggested_action, status)
        SELECT gen_random_uuid(), id, '["VENDOR_EMAIL_REMOVED"]'::jsonb,
               'This invoice was waiting for a vendor reply. Vendor emails are no longer part of the '
               'application: contact the vendor yourself, then approve or reject.',
               'decide_without_vendor', 'open'
        FROM document WHERE status = 'AWAITING_VENDOR'""")
    op.execute("UPDATE document SET status = 'IN_REVIEW', updated_at = now() WHERE status = 'AWAITING_VENDOR'")
    op.execute("DROP TABLE IF EXISTS email_outbox")
    op.execute("DROP TABLE IF EXISTS mailbox_state")


def downgrade() -> None:
    # The dropped tables are not restored: the code that used them is gone.
    pass
