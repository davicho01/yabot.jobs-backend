"""personal access tokens

Revision ID: f3a1b8c6d2e4
Revises: d1e4a9c7f2b8
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f3a1b8c6d2e4'
down_revision: Union[str, Sequence[str], None] = 'd1e4a9c7f2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'personal_access_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('label', sa.String(length=60), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_personal_access_tokens_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_personal_access_tokens')),
    )
    op.create_index(op.f('ix_personal_access_tokens_user_id'), 'personal_access_tokens', ['user_id'], unique=False)
    op.create_index(op.f('ix_personal_access_tokens_token_hash'), 'personal_access_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_personal_access_tokens_token_hash'), table_name='personal_access_tokens')
    op.drop_index(op.f('ix_personal_access_tokens_user_id'), table_name='personal_access_tokens')
    op.drop_table('personal_access_tokens')
