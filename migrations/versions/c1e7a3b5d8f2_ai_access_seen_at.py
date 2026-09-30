"""ai access seen at

Revision ID: c1e7a3b5d8f2
Revises: b8d2f6a4c9e1
Create Date: 2026-10-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1e7a3b5d8f2'
down_revision: Union[str, Sequence[str], None] = 'b8d2f6a4c9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('ai_access_seen_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'ai_access_seen_at')
