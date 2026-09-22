"""cross-source job posting dedup

Revision ID: b57b5d4b4a59
Revises: 344b28925724
Create Date: 2026-09-22 09:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b57b5d4b4a59'
down_revision: Union[str, Sequence[str], None] = '344b28925724'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('job_postings', sa.Column('company_key', sa.String(length=255), nullable=True))
    op.add_column('job_postings', sa.Column('title_key', sa.String(length=255), nullable=True))
    op.add_column('job_postings', sa.Column('primary_posting_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_job_postings_company_key'), 'job_postings', ['company_key'], unique=False)
    op.create_index(
        op.f('ix_job_postings_primary_posting_id'), 'job_postings', ['primary_posting_id'], unique=False
    )
    op.create_foreign_key(
        op.f('fk_job_postings_primary_posting_id_job_postings'),
        'job_postings',
        'job_postings',
        ['primary_posting_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('fk_job_postings_primary_posting_id_job_postings'), 'job_postings', type_='foreignkey'
    )
    op.drop_index(op.f('ix_job_postings_primary_posting_id'), table_name='job_postings')
    op.drop_index(op.f('ix_job_postings_company_key'), table_name='job_postings')
    op.drop_column('job_postings', 'primary_posting_id')
    op.drop_column('job_postings', 'title_key')
    op.drop_column('job_postings', 'company_key')
