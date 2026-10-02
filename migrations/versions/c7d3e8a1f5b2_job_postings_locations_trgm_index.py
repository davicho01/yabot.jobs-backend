"""job_postings locations trigram index

Revision ID: c7d3e8a1f5b2
Revises: b5c9e2f7a4d1
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7d3e8a1f5b2'
down_revision: Union[str, Sequence[str], None] = 'b5c9e2f7a4d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Serves location_matches (app.services.job_locations): `locations::text
    # ILIKE '%Utah%'` becomes an index lookup instead of a scan of every
    # posting, so a state/text location search can BitmapOr it with the
    # metros index. The expression must stay exactly what location_matches
    # renders (CAST(job_postings.locations AS TEXT)) or the planner won't use it.
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    # CONCURRENTLY so the crawler's writes to job_postings aren't blocked while a
    # large table is indexed; it can't run inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_postings_locations_trgm '
            'ON job_postings USING gin ((CAST(locations AS TEXT)) gin_trgm_ops)'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_postings_locations_trgm')
