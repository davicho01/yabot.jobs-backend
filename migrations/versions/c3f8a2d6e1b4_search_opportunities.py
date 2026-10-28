"""Search opportunities: Google searches grouped by what they were after

Revision ID: c3f8a2d6e1b4
Revises: b7e3c9a1f2d5
Create Date: 2026-10-28 00:00:00.000000

One new table, nothing else touched (see app.models.search_opportunity).
Replaced nightly by collection_maintenance.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c3f8a2d6e1b4'
down_revision: Union[str, Sequence[str], None] = 'b7e3c9a1f2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'search_opportunities',
        sa.Column('key', sa.String(length=500), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('label', sa.String(length=500), nullable=False),
        sa.Column('collection_key', sa.String(length=300), nullable=True),
        sa.Column('open_jobs', sa.Integer(), nullable=True),
        sa.Column('clicks', sa.Integer(), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=False),
        sa.Column('position', sa.Float(), nullable=False),
        sa.Column('queries', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('pages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_on', sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('search_opportunities')
