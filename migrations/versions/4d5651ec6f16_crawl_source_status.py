"""crawl source status + pending (undetected) boards

Revision ID: 4d5651ec6f16
Revises: 3d7a2c9e5f1b
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4d5651ec6f16'
down_revision: Union[str, Sequence[str], None] = '3d7a2c9e5f1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('uq_crawl_sources_ats_type_board_token', 'crawl_sources', type_='unique')

    op.alter_column('crawl_sources', 'ats_type', existing_type=sa.String(length=20), nullable=True)
    op.alter_column('crawl_sources', 'board_token', existing_type=sa.String(length=255), nullable=True)

    op.add_column('crawl_sources', sa.Column('detected_domain', sa.String(length=255), nullable=True))
    op.add_column(
        'crawl_sources',
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
    )

    op.create_index(
        'uq_crawl_sources_ats_type_board_token',
        'crawl_sources',
        ['ats_type', 'board_token'],
        unique=True,
        postgresql_where=sa.text('ats_type IS NOT NULL'),
    )
    op.create_index(
        'uq_crawl_sources_detected_domain',
        'crawl_sources',
        ['detected_domain'],
        unique=True,
        postgresql_where=sa.text('ats_type IS NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_crawl_sources_detected_domain', table_name='crawl_sources')
    op.drop_index('uq_crawl_sources_ats_type_board_token', table_name='crawl_sources')

    op.drop_column('crawl_sources', 'status')
    op.drop_column('crawl_sources', 'detected_domain')

    op.alter_column('crawl_sources', 'board_token', existing_type=sa.String(length=255), nullable=False)
    op.alter_column('crawl_sources', 'ats_type', existing_type=sa.String(length=20), nullable=False)

    op.create_unique_constraint(
        'uq_crawl_sources_ats_type_board_token', 'crawl_sources', ['ats_type', 'board_token']
    )
