"""per-source scan throttling

Revision ID: b7c1d4e8a2f3
Revises: e945b0004794
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7c1d4e8a2f3'
down_revision: Union[str, Sequence[str], None] = 'e945b0004794'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'crawl_sources',
        sa.Column('max_concurrent_scans', sa.Integer(), server_default='3', nullable=False),
    )
    op.create_check_constraint(
        op.f('ck_crawl_sources_max_concurrent_scans_range'),
        'crawl_sources',
        'max_concurrent_scans BETWEEN 1 AND 5',
    )

    op.add_column('job_posting_urls', sa.Column('scan_claimed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        'ix_job_posting_urls_source_pending',
        'job_posting_urls',
        ['crawl_source_id', 'scan_claimed_at'],
        unique=False,
        postgresql_where=sa.text("scan_status = 'pending'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_job_posting_urls_source_pending', table_name='job_posting_urls', postgresql_where=sa.text("scan_status = 'pending'"))
    op.drop_column('job_posting_urls', 'scan_claimed_at')

    op.drop_constraint(op.f('ck_crawl_sources_max_concurrent_scans_range'), 'crawl_sources', type_='check')
    op.drop_column('crawl_sources', 'max_concurrent_scans')
