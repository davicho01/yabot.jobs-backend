"""Uncapped crawl sources

Revision ID: b7e2c4a91f05
Revises: 4d0aae2449ad
Create Date: 2026-10-22 00:00:00.000000

Per-board switch for crawling the whole board (CrawlSource.uncapped), and
when its first uncapped crawl set the import baseline. Off for every board.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7e2c4a91f05'
down_revision: Union[str, None] = '4d0aae2449ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('crawl_sources', sa.Column('uncapped', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('crawl_sources', sa.Column('uncapped_baseline_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('crawl_sources', 'uncapped_baseline_at')
    op.drop_column('crawl_sources', 'uncapped')
