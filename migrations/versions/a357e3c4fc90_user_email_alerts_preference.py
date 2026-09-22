"""user email alerts preference

Revision ID: a357e3c4fc90
Revises: 7a1f9c3e5b6d
Create Date: 2026-09-22 12:39:10.526046

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a357e3c4fc90'
down_revision: Union[str, Sequence[str], None] = '7a1f9c3e5b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default (autogenerate doesn't pick up the model's plain
    # default=True — that's Python-side only) so this doesn't fail against
    # the existing, non-empty users table: every already-registered user
    # opts in, same as the model's default for anyone new after this.
    op.add_column('users', sa.Column('email_alerts_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'email_alerts_enabled')
