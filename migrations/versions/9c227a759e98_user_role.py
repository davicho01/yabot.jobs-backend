"""user role

Revision ID: 9c227a759e98
Revises: f3a1b8c6d2e4
Create Date: 2026-09-15 18:51:07.847931

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c227a759e98'
down_revision: Union[str, Sequence[str], None] = 'f3a1b8c6d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('role', sa.String(length=20), nullable=False, server_default='user'),
    )
    op.alter_column('users', 'role', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'role')
