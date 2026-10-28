"""Search performance: each page's daily Google results from Search Console

Revision ID: b7e3c9a1f2d5
Revises: d4d22d3b871c
Create Date: 2026-10-27 00:00:00.000000

One new table, nothing else touched (see app.models.search_performance).
Filled nightly by collection_maintenance.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7e3c9a1f2d5'
down_revision: Union[str, Sequence[str], None] = 'd4d22d3b871c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'search_performance_daily',
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('page', sa.String(length=500), nullable=False),
        sa.Column('clicks', sa.Integer(), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=False),
        sa.Column('position', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('day', 'page'),
    )


def downgrade() -> None:
    op.drop_table('search_performance_daily')
