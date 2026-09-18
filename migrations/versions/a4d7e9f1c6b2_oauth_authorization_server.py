"""oauth authorization server

Revision ID: a4d7e9f1c6b2
Revises: c7a3f9d2e5b1
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a4d7e9f1c6b2'
down_revision: Union[str, Sequence[str], None] = 'c7a3f9d2e5b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'oauth_clients',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('client_name', sa.String(length=120), nullable=False),
        sa.Column('redirect_uris', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_clients')),
    )
    op.create_index(op.f('ix_oauth_clients_client_id'), 'oauth_clients', ['client_id'], unique=True)

    op.create_table(
        'oauth_authorization_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('redirect_uri', sa.String(length=2048), nullable=False),
        sa.Column('code_challenge', sa.String(length=128), nullable=False),
        sa.Column('code_challenge_method', sa.String(length=16), nullable=False),
        sa.Column('scopes', sa.String(length=255), nullable=True),
        sa.Column('resource', sa.String(length=2048), nullable=True),
        sa.Column('state', sa.String(length=512), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('code_hash', sa.String(length=64), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['client_id'], ['oauth_clients.client_id'],
            name=op.f('fk_oauth_authorization_requests_client_id_oauth_clients'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_oauth_authorization_requests_user_id_users'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_authorization_requests')),
    )
    op.create_index(
        op.f('ix_oauth_authorization_requests_client_id'), 'oauth_authorization_requests', ['client_id'], unique=False
    )
    op.create_index(
        op.f('ix_oauth_authorization_requests_user_id'), 'oauth_authorization_requests', ['user_id'], unique=False
    )
    op.create_index(
        op.f('ix_oauth_authorization_requests_code_hash'), 'oauth_authorization_requests', ['code_hash'], unique=True
    )

    op.create_table(
        'oauth_refresh_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('client_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('access_token_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['client_id'], ['oauth_clients.client_id'],
            name=op.f('fk_oauth_refresh_tokens_client_id_oauth_clients'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_oauth_refresh_tokens_user_id_users'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['access_token_id'], ['personal_access_tokens.id'],
            name=op.f('fk_oauth_refresh_tokens_access_token_id_personal_access_tokens'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_oauth_refresh_tokens')),
    )
    op.create_index(op.f('ix_oauth_refresh_tokens_token_hash'), 'oauth_refresh_tokens', ['token_hash'], unique=True)
    op.create_index(op.f('ix_oauth_refresh_tokens_client_id'), 'oauth_refresh_tokens', ['client_id'], unique=False)
    op.create_index(op.f('ix_oauth_refresh_tokens_user_id'), 'oauth_refresh_tokens', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_oauth_refresh_tokens_user_id'), table_name='oauth_refresh_tokens')
    op.drop_index(op.f('ix_oauth_refresh_tokens_client_id'), table_name='oauth_refresh_tokens')
    op.drop_index(op.f('ix_oauth_refresh_tokens_token_hash'), table_name='oauth_refresh_tokens')
    op.drop_table('oauth_refresh_tokens')

    op.drop_index(op.f('ix_oauth_authorization_requests_code_hash'), table_name='oauth_authorization_requests')
    op.drop_index(op.f('ix_oauth_authorization_requests_user_id'), table_name='oauth_authorization_requests')
    op.drop_index(op.f('ix_oauth_authorization_requests_client_id'), table_name='oauth_authorization_requests')
    op.drop_table('oauth_authorization_requests')

    op.drop_index(op.f('ix_oauth_clients_client_id'), table_name='oauth_clients')
    op.drop_table('oauth_clients')
