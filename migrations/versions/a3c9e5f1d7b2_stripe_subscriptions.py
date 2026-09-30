"""stripe subscriptions

Revision ID: a3c9e5f1d7b2
Revises: f4a8d2c6b1e3
Create Date: 2026-10-01 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c9e5f1d7b2'
down_revision: Union[str, Sequence[str], None] = 'f4a8d2c6b1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('stripe_customer_id', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('stripe_subscription_id', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('subscription_status', sa.String(length=30), nullable=True))
    op.add_column('users', sa.Column('subscription_current_period_end', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'users',
        sa.Column('subscription_cancel_at_period_end', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column('users', sa.Column('subscription_usage_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('subscription_usage_period_end', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_users_stripe_customer_id'), 'users', ['stripe_customer_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_users_stripe_customer_id'), table_name='users')
    op.drop_column('users', 'subscription_usage_period_end')
    op.drop_column('users', 'subscription_usage_count')
    op.drop_column('users', 'subscription_cancel_at_period_end')
    op.drop_column('users', 'subscription_current_period_end')
    op.drop_column('users', 'subscription_status')
    op.drop_column('users', 'stripe_subscription_id')
    op.drop_column('users', 'stripe_customer_id')
