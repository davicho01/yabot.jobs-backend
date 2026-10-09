"""job posting url scanned_via_browser

Revision ID: e7a2c4b9d1f3
Revises: d5e8f1a3c7b9
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a2c4b9d1f3'
down_revision: Union[str, Sequence[str], None] = 'd5e8f1a3c7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no backfill: existing rows stay NULL ("unknown") until their
    # next scan sets it.
    op.add_column('job_posting_urls', sa.Column('scanned_via_browser', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_posting_urls', 'scanned_via_browser')
