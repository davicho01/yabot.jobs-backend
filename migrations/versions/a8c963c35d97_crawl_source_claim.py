"""crawl source claim

Revision ID: a8c963c35d97
Revises: d6ff3635e3ef
Create Date: 2026-09-27 15:54:44.639358

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8c963c35d97'
down_revision: Union[str, Sequence[str], None] = 'd6ff3635e3ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'crawl_sources',
        sa.Column('crawl_claimed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('crawl_sources', 'crawl_claimed_at')
