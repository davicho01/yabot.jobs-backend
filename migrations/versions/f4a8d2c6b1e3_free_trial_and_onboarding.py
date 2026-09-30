"""free trial evaluations and onboarding checklist state

Revision ID: f4a8d2c6b1e3
Revises: e2b7c4d91a6f
Create Date: 2026-09-30 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a8d2c6b1e3'
down_revision: Union[str, Sequence[str], None] = 'e2b7c4d91a6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('free_evaluations_used', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column('users', sa.Column('onboarding_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('onboarding_dismissed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'onboarding_dismissed_at')
    op.drop_column('users', 'onboarding_completed_at')
    op.drop_column('users', 'free_evaluations_used')
