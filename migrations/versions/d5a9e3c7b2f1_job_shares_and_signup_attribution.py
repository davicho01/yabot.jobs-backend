"""Job shares and sign-up attribution

Revision ID: d5a9e3c7b2f1
Revises: c3f8a2d6e1b4
Create Date: 2026-10-28 00:00:00.000000

A new table (app.models.job_share) and nullable columns on users for where
a new user first came from; nothing existing changes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd5a9e3c7b2f1'
down_revision: Union[str, Sequence[str], None] = 'c3f8a2d6e1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job_shares',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('job_url_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_url_id'], ['job_posting_urls.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_job_shares_job_url_id'), 'job_shares', ['job_url_id'], unique=False)
    op.create_index(op.f('ix_job_shares_user_id'), 'job_shares', ['user_id'], unique=False)
    op.add_column('users', sa.Column('signup_share_id', sa.String(length=32), nullable=True))
    op.add_column('users', sa.Column('signup_source', sa.String(length=100), nullable=True))
    op.add_column('users', sa.Column('signup_medium', sa.String(length=100), nullable=True))
    op.add_column('users', sa.Column('signup_campaign', sa.String(length=200), nullable=True))
    op.add_column('users', sa.Column('signup_landing_page', sa.String(length=500), nullable=True))
    op.add_column('users', sa.Column('attributed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_users_signup_share_id'), 'users', ['signup_share_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_signup_share_id'), table_name='users')
    for column in ('attributed_at', 'signup_landing_page', 'signup_campaign', 'signup_medium', 'signup_source',
                   'signup_share_id'):
        op.drop_column('users', column)
    op.drop_index(op.f('ix_job_shares_user_id'), table_name='job_shares')
    op.drop_index(op.f('ix_job_shares_job_url_id'), table_name='job_shares')
    op.drop_table('job_shares')
