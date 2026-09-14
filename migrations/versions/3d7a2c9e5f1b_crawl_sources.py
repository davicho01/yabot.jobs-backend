"""add crawl_sources table

Revision ID: 3d7a2c9e5f1b
Revises: 8f1c6a0d3b2e
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3d7a2c9e5f1b'
down_revision: Union[str, Sequence[str], None] = '8f1c6a0d3b2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'crawl_sources',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('ats_type', sa.String(length=20), nullable=False),
        sa.Column('board_token', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('last_crawled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_job_count', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_crawl_sources')),
        sa.UniqueConstraint('ats_type', 'board_token', name='uq_crawl_sources_ats_type_board_token'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('crawl_sources')
