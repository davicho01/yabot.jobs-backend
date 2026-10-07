"""job posting first_scanned_at

Revision ID: b2e6f3a9c8d4
Revises: c1d9e4a7b2f6
Create Date: 2026-10-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2e6f3a9c8d4'
down_revision: Union[str, Sequence[str], None] = 'c1d9e4a7b2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('job_postings', sa.Column('first_scanned_at', sa.DateTime(timezone=True), nullable=True))
    # Existing rows have no history of when they were first scanned, so the
    # closest honest value is the scan time already on record.
    op.execute('UPDATE job_postings SET first_scanned_at = scanned_at WHERE scanned_at IS NOT NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_postings', 'first_scanned_at')
