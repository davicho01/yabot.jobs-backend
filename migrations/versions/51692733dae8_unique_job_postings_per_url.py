"""unique job postings per url

Revision ID: 51692733dae8
Revises: 78e15e41c63d
Create Date: 2026-09-12 20:34:55.887844

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51692733dae8'
down_revision: Union[str, Sequence[str], None] = '78e15e41c63d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(op.f('ix_job_postings_url_id'), table_name='job_postings')
    op.create_index(op.f('ix_job_postings_url_id'), 'job_postings', ['url_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_job_postings_url_id'), table_name='job_postings')
    op.create_index(op.f('ix_job_postings_url_id'), 'job_postings', ['url_id'], unique=False)
