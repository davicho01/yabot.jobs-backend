"""job posting metro areas

Revision ID: d3f7a1b8c5e2
Revises: c8e5b3a91d27
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd3f7a1b8c5e2'
down_revision: Union[str, Sequence[str], None] = 'c8e5b3a91d27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default only so existing rows satisfy NOT NULL while the column
    # is added; the model supplies the value on insert, so it's dropped again.
    # Existing rows are populated afterwards by one_off/backfill_metros.py — resolving a
    # place to its metro needs the bundled geography data, which a migration
    # shouldn't import from app code that keeps evolving.
    op.add_column(
        'job_postings',
        sa.Column('metros', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )
    op.alter_column('job_postings', 'metros', server_default=None)
    # CONCURRENTLY so the crawler's writes to job_postings aren't blocked while a
    # large table is indexed; it can't run inside a transaction.
    with op.get_context().autocommit_block():
        op.execute('CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_postings_metros ON job_postings USING gin (metros jsonb_path_ops)')


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_postings_metros')
    op.drop_column('job_postings', 'metros')
