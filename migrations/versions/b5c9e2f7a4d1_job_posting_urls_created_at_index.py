"""job_posting_urls created_at index

Revision ID: b5c9e2f7a4d1
Revises: e7f1a9c3b2d6
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b5c9e2f7a4d1'
down_revision: Union[str, Sequence[str], None] = 'e7f1a9c3b2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Serves GET /jobs' newest-first ordering. CONCURRENTLY so the crawler's
    # writes aren't blocked while a large table is indexed; it can't run
    # inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_posting_urls_created_at '
            'ON job_posting_urls (created_at)'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_posting_urls_created_at')
