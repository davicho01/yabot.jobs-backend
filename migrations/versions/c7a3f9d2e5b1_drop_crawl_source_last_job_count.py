"""drop crawl_sources.last_job_count (superseded by per-day scan counts)

Revision ID: c7a3f9d2e5b1
Revises: b2f6e1a8c3d4
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7a3f9d2e5b1'
down_revision: Union[str, Sequence[str], None] = 'b2f6e1a8c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('crawl_sources', 'last_job_count')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('crawl_sources', sa.Column('last_job_count', sa.Integer(), nullable=True))
