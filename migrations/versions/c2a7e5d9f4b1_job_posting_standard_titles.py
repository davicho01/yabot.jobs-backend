"""job posting standard_titles, standard_title_model, standard_title_input_hash

Revision ID: c2a7e5d9f4b1
Revises: b8e3f1a6c2d4
Create Date: 2026-10-13 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c2a7e5d9f4b1'
down_revision: Union[str, Sequence[str], None] = 'b8e3f1a6c2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # All NULL to start: existing Tech postings are tagged by
    # one_off/backfill_standard_titles.py, new ones at scan time.
    op.add_column('job_postings', sa.Column('standard_titles', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('job_postings', sa.Column('standard_title_model', sa.String(length=40), nullable=True))
    op.add_column('job_postings', sa.Column('standard_title_input_hash', sa.String(length=16), nullable=True))
    # CONCURRENTLY so the crawler's writes to job_postings aren't blocked while a
    # large table is indexed; it can't run inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_postings_standard_titles '
            'ON job_postings USING gin (standard_titles jsonb_path_ops)'
        )
        op.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_postings_standard_titles_pending '
            "ON job_postings (id) WHERE sector = 'engineering_tech' AND standard_title_model IS NULL"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_postings_standard_titles_pending')
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_postings_standard_titles')
    op.drop_column('job_postings', 'standard_title_input_hash')
    op.drop_column('job_postings', 'standard_title_model')
    op.drop_column('job_postings', 'standard_titles')
