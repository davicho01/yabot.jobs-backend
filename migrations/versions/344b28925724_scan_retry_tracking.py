"""scan retry tracking on job posting urls

Revision ID: 344b28925724
Revises: aad12ddfc5a1
Create Date: 2026-09-22 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '344b28925724'
down_revision: Union[str, Sequence[str], None] = 'aad12ddfc5a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'job_posting_urls', sa.Column('scan_attempts', sa.Integer(), server_default='0', nullable=False)
    )
    op.add_column('job_posting_urls', sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f('ix_job_posting_urls_retry_due'),
        'job_posting_urls',
        ['next_retry_at'],
        unique=False,
        postgresql_where=sa.text("scan_status = 'failed'"),
    )
    # Rows already FAILED before this migration have scan_attempts=0 (the new
    # column's default) but next_retry_at is still NULL — and
    # wake_retryable_failed_scans only ever selects FAILED rows where
    # next_retry_at IS NOT NULL. Without this, every pre-existing failure
    # would sit forever, invisible to the retry sweep, until someone noticed
    # and rescanned it by hand. Backdating next_retry_at to now makes them
    # eligible for the very next sweep, same as attempt 1 of a fresh failure.
    op.execute("UPDATE job_posting_urls SET next_retry_at = now() WHERE scan_status = 'failed'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_job_posting_urls_retry_due'),
        table_name='job_posting_urls',
        postgresql_where=sa.text("scan_status = 'failed'"),
    )
    op.drop_column('job_posting_urls', 'next_retry_at')
    op.drop_column('job_posting_urls', 'scan_attempts')
