"""application follow up reminders

Revision ID: 00ba02dcdf61
Revises: a357e3c4fc90
Create Date: 2026-09-22 14:40:43.979331

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '00ba02dcdf61'
down_revision: Union[str, Sequence[str], None] = 'a357e3c4fc90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('user_job_applications', sa.Column('follow_up_at', sa.Date(), nullable=True))
    op.add_column('user_job_applications', sa.Column('follow_up_reminded_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user_job_applications', 'follow_up_reminded_at')
    op.drop_column('user_job_applications', 'follow_up_at')
