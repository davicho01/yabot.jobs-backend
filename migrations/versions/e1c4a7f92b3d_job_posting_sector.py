"""job posting sector

Revision ID: e1c4a7f92b3d
Revises: 0ccd11568212
Create Date: 2026-09-26 21:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1c4a7f92b3d'
down_revision: Union[str, Sequence[str], None] = '0ccd11568212'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'job_postings',
        sa.Column('sector', sa.String(length=30), server_default='unknown', nullable=False),
    )
    op.create_index(op.f('ix_job_postings_sector'), 'job_postings', ['sector'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_job_postings_sector'), table_name='job_postings')
    op.drop_column('job_postings', 'sector')
