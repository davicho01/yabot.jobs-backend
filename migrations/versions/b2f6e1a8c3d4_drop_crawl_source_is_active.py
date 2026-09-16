"""drop redundant crawl_sources.is_active (superseded by status)

Revision ID: b2f6e1a8c3d4
Revises: 8c8a4d0f24c5
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2f6e1a8c3d4'
down_revision: Union[str, Sequence[str], None] = '8c8a4d0f24c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('crawl_sources', 'is_active')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'crawl_sources',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
