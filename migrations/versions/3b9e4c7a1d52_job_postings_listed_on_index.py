"""job_postings listed-on index

Revision ID: 3b9e4c7a1d52
Revises: d4f8a2c6e1b3
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3b9e4c7a1d52'
down_revision: Union[str, Sequence[str], None] = 'd4f8a2c6e1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Serves GET /jobs' newest-posted-first order (build_job_search_statement):
    # a broad search ("United States") reads the newest postings off this
    # index and stops at the page, instead of sorting every match. The
    # expression must stay exactly what that order renders or the planner
    # won't use it; the WHERE matches the search's base filters.
    # CONCURRENTLY so the crawler's writes to job_postings aren't blocked while a
    # large table is indexed; it can't run inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_postings_listed_on ON job_postings "
            "((coalesce(posted_at, (first_scanned_at AT TIME ZONE 'UTC')::date)) DESC, first_scanned_at DESC) "
            "WHERE extraction_status = 'success' AND primary_posting_id IS NULL AND title IS NOT NULL"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_postings_listed_on')
