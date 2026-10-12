"""saved search sector

Revision ID: b8e3f1a6c2d4
Revises: a4c9e2f7b1d6
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8e3f1a6c2d4'
down_revision: Union[str, Sequence[str], None] = 'a4c9e2f7b1d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # No backfill: existing saved searches stay NULL (every sector), so their
    # alerts don't silently narrow to the board's new tech default.
    op.add_column('saved_searches', sa.Column('sector', sa.String(length=30), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('saved_searches', 'sector')
