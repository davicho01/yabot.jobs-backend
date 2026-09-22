"""job posting places

Revision ID: aad12ddfc5a1
Revises: d3f7a1b8c5e2
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'aad12ddfc5a1'
down_revision: Union[str, Sequence[str], None] = 'd3f7a1b8c5e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default only so existing rows satisfy NOT NULL while the column is
    # added; the model supplies the value on insert, so it's dropped again.
    # Existing rows are populated afterwards by one_off/backfill_metros.py (resolving a
    # place to coordinates needs the bundled geography data, which a migration
    # shouldn't import from app code that keeps evolving). No index: a radius
    # search narrows with the `metros` GIN index first and computes distances on
    # what's left.
    op.add_column(
        'job_postings',
        sa.Column('places', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )
    op.alter_column('job_postings', 'places', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_postings', 'places')
