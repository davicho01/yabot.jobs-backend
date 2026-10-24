"""Sign-in codes: count wrong tries

Revision ID: d3a7c9e1b5f2
Revises: b8e3d1f6a2c4
Create Date: 2026-10-24 00:00:00.000000

Emailed six-digit sign-in codes (POST /auth/request-code) reuse
magic_link_tokens; failed_attempts caps the guesses one code gets (see
app.services.auth.verify_login_code).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd3a7c9e1b5f2'
down_revision: Union[str, Sequence[str], None] = 'b8e3d1f6a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'magic_link_tokens',
        sa.Column('failed_attempts', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('magic_link_tokens', 'failed_attempts')
