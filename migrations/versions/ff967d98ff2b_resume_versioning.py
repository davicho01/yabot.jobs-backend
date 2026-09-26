"""resume versioning

Revision ID: ff967d98ff2b
Revises: 239a8ae619af
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ff967d98ff2b'
down_revision: Union[str, Sequence[str], None] = '239a8ae619af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('resumes', sa.Column('root_resume_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('resumes', sa.Column('version_number', sa.Integer(), nullable=True))

    # Every pre-existing resume becomes the root of its own singleton
    # family at version 1.
    op.execute("UPDATE resumes SET root_resume_id = id, version_number = 1 WHERE root_resume_id IS NULL")

    op.alter_column('resumes', 'root_resume_id', nullable=False)
    op.alter_column('resumes', 'version_number', nullable=False)

    op.create_foreign_key(
        op.f('fk_resumes_root_resume_id_resumes'), 'resumes', 'resumes', ['root_resume_id'], ['id'],
    )
    op.create_index(op.f('ix_resumes_root_resume_id'), 'resumes', ['root_resume_id'], unique=False)
    op.create_unique_constraint(
        'uq_resumes_root_resume_id_version_number', 'resumes', ['root_resume_id', 'version_number'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_resumes_root_resume_id_version_number', 'resumes', type_='unique')
    op.drop_index(op.f('ix_resumes_root_resume_id'), table_name='resumes')
    op.drop_constraint(op.f('fk_resumes_root_resume_id_resumes'), 'resumes', type_='foreignkey')
    op.drop_column('resumes', 'version_number')
    op.drop_column('resumes', 'root_resume_id')
