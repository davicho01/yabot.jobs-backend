"""job_posting_urls last_seen_at / closed_at

Revision ID: a1c5e7f9b3d2
Revises: e9f5a1c3b7d4
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c5e7f9b3d2'
down_revision: Union[str, Sequence[str], None] = 'e9f5a1c3b7d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('job_posting_urls', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('job_posting_urls', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
    # Every crawl-sourced URL counts as seen right now, so nothing is closed
    # until a healthy crawl has actually gone job_closed_after_unseen_hours
    # without listing it.
    op.execute('UPDATE job_posting_urls SET last_seen_at = now() WHERE crawl_source_id IS NOT NULL')
    # CONCURRENTLY so the crawler's writes aren't blocked while it builds.
    with op.get_context().autocommit_block():
        op.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_posting_urls_source_open '
            'ON job_posting_urls (crawl_source_id) WHERE closed_at IS NULL'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_posting_urls_source_open')
    op.drop_column('job_posting_urls', 'closed_at')
    op.drop_column('job_posting_urls', 'last_seen_at')
