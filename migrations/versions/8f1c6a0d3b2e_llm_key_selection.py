"""add model/base_url/is_default to user_api_keys

Revision ID: 8f1c6a0d3b2e
Revises: 944c6551d131
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8f1c6a0d3b2e'
down_revision: Union[str, Sequence[str], None] = '944c6551d131'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('user_api_keys', sa.Column('model', sa.String(length=100), nullable=True))
    op.add_column('user_api_keys', sa.Column('base_url', sa.String(length=255), nullable=True))
    op.add_column(
        'user_api_keys',
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('user_api_keys', 'is_default', server_default=None)
    op.create_index(
        'uq_user_api_keys_one_default_per_user',
        'user_api_keys',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_user_api_keys_one_default_per_user', table_name='user_api_keys')
    op.drop_column('user_api_keys', 'is_default')
    op.drop_column('user_api_keys', 'base_url')
    op.drop_column('user_api_keys', 'model')
