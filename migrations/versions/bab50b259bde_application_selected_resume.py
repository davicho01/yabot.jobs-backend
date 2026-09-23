"""application selected resume

Revision ID: bab50b259bde
Revises: d6bd605f3a81
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'bab50b259bde'
down_revision: Union[str, Sequence[str], None] = 'd6bd605f3a81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'user_job_applications', sa.Column('selected_resume_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        op.f('fk_user_job_applications_selected_resume_id_resumes'),
        'user_job_applications',
        'resumes',
        ['selected_resume_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('fk_user_job_applications_selected_resume_id_resumes'), 'user_job_applications', type_='foreignkey'
    )
    op.drop_column('user_job_applications', 'selected_resume_id')
