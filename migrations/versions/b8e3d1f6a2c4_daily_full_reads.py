"""Daily full reads for recent-only boards

Revision ID: b8e3d1f6a2c4
Revises: c4d8e2a6f1b9
Create Date: 2026-10-24 00:00:00.000000

Switches on the uncapped crawl (a daily full read, see
crawl_schedule.full_read_due) for every Workday, Oracle Fusion, Amazon,
Apple and TalentReef board: their capped crawls list only recent postings,
so none of their jobs could ever close. Adds when each board was last read in
full, and an index for record_board_presence's per-source count of jobs
closed in the last day (its daily closing limit).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8e3d1f6a2c4'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2a6f1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Keep in step with app.services.ats_adapters.UNCAPPED_BY_DEFAULT_ATS_TYPES.
_ATS_TYPES = ('workday', 'oracle_fusion', 'amazon', 'apple', 'talentreef')


def upgrade() -> None:
    op.add_column('crawl_sources', sa.Column('uncapped_crawled_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text('UPDATE crawl_sources SET uncapped = true WHERE ats_type IN :types').bindparams(
            sa.bindparam('types', value=_ATS_TYPES, expanding=True)
        )
    )
    # Only closed rows, so it stays small. CONCURRENTLY so the crawler's
    # writes to job_posting_urls aren't blocked while it builds; it can't run
    # inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_posting_urls_source_closed_at "
            "ON job_posting_urls (crawl_source_id, closed_at) WHERE closed_at IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_posting_urls_source_closed_at')
    op.execute(
        sa.text('UPDATE crawl_sources SET uncapped = false WHERE ats_type IN :types').bindparams(
            sa.bindparam('types', value=_ATS_TYPES, expanding=True)
        )
    )
    op.drop_column('crawl_sources', 'uncapped_crawled_at')
