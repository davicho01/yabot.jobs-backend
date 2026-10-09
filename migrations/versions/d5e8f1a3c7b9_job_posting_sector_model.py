"""job posting sector_model, sector_confidence, sector_input_hash

Revision ID: d5e8f1a3c7b9
Revises: b2e6f3a9c8d4
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e8f1a3c7b9'
down_revision: Union[str, Sequence[str], None] = 'b2e6f3a9c8d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('job_postings', sa.Column('sector_model', sa.String(length=40), nullable=True))
    op.add_column('job_postings', sa.Column('sector_confidence', sa.Float(), nullable=True))
    op.add_column('job_postings', sa.Column('sector_input_hash', sa.String(length=16), nullable=True))
    # Existing rows were classified by the old keyword lists. Marking them as
    # such keeps them out of the pending sweep (sector_model IS NULL), so they
    # are only reclassified by an explicit one_off/backfill_sector.py run.
    op.execute("UPDATE job_postings SET sector_model = 'keyword-matcher'")
    op.create_index(
        'ix_job_postings_sector_pending', 'job_postings', ['id'], postgresql_where=sa.text('sector_model IS NULL')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_job_postings_sector_pending', table_name='job_postings')
    op.drop_column('job_postings', 'sector_input_hash')
    op.drop_column('job_postings', 'sector_confidence')
    op.drop_column('job_postings', 'sector_model')
