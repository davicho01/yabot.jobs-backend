"""crawl source adaptive schedule

Revision ID: a4c9e2f7b1d6
Revises: e7a2c4b9d1f3
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a4c9e2f7b1d6'
down_revision: Union[str, Sequence[str], None] = 'e7a2c4b9d1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing rows start at 720 minutes with next_crawl_at NULL (due now);
    # crawl_dispatcher's per-run cap spreads that first pass over several runs,
    # and each source's interval adapts from its own crawls after that.
    op.add_column(
        'crawl_sources',
        sa.Column('crawl_interval_minutes', sa.Integer(), server_default='720', nullable=False),
    )
    op.add_column('crawl_sources', sa.Column('next_crawl_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        'crawl_interval_minutes_range', 'crawl_sources', 'crawl_interval_minutes BETWEEN 5 AND 4320'
    )
    op.create_index('ix_crawl_sources_status_next_crawl_at', 'crawl_sources', ['status', 'next_crawl_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_crawl_sources_status_next_crawl_at', table_name='crawl_sources')
    op.drop_constraint('crawl_interval_minutes_range', 'crawl_sources', type_='check')
    op.drop_column('crawl_sources', 'next_crawl_at')
    op.drop_column('crawl_sources', 'crawl_interval_minutes')
