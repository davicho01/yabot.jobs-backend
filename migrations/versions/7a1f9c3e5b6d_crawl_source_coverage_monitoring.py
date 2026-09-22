"""crawl source coverage monitoring

Revision ID: 7a1f9c3e5b6d
Revises: 246f67966ca3
Create Date: 2026-09-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1f9c3e5b6d'
down_revision: Union[str, Sequence[str], None] = '246f67966ca3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('crawl_sources', sa.Column('coverage_last_count', sa.Integer(), nullable=True))
    op.add_column('crawl_sources', sa.Column('coverage_baseline', sa.Float(), nullable=True))
    op.add_column('crawl_sources', sa.Column('coverage_sample_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('crawl_sources', sa.Column('coverage_low_streak', sa.Integer(), server_default='0', nullable=False))
    op.add_column('crawl_sources', sa.Column('coverage_flagged_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('crawl_sources', 'coverage_flagged_at')
    op.drop_column('crawl_sources', 'coverage_low_streak')
    op.drop_column('crawl_sources', 'coverage_sample_count')
    op.drop_column('crawl_sources', 'coverage_baseline')
    op.drop_column('crawl_sources', 'coverage_last_count')
