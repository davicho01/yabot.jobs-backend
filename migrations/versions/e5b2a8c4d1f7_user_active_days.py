"""User active days: one row per user per day they used the app

Revision ID: e5b2a8c4d1f7
Revises: d3a7c9e1b5f2
Create Date: 2026-10-26 00:00:00.000000

Feeds the admin "Returning users" chart (see
app.services.auth.record_active_day). No backfill — nothing before this
recorded per-day visits.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e5b2a8c4d1f7'
down_revision: Union[str, Sequence[str], None] = 'd3a7c9e1b5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_active_days',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'day'),
    )
    op.create_index('ix_user_active_days_day', 'user_active_days', ['day'])


def downgrade() -> None:
    op.drop_index('ix_user_active_days_day', table_name='user_active_days')
    op.drop_table('user_active_days')
