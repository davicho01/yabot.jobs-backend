"""job posting country

Revision ID: d6ff3635e3ef
Revises: e1c4a7f92b3d
Create Date: 2026-09-27 07:17:00.606413

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6ff3635e3ef'
down_revision: Union[str, Sequence[str], None] = 'e1c4a7f92b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'job_postings',
        sa.Column('country', sa.String(length=2), nullable=True),
    )
    op.create_index(op.f('ix_job_postings_country'), 'job_postings', ['country'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_job_postings_country'), table_name='job_postings')
    op.drop_column('job_postings', 'country')
