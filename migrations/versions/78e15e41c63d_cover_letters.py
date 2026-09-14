"""add cover_letters table

Revision ID: 78e15e41c63d
Revises: 55bbd81c14d1
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '78e15e41c63d'
down_revision: Union[str, Sequence[str], None] = '55bbd81c14d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('cover_letters',
    sa.Column('resume_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('job_posting_id', sa.UUID(), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('storage_key', sa.String(length=500), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('raw_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], name=op.f('fk_cover_letters_job_posting_id_job_postings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resume_id'], ['resumes.id'], name=op.f('fk_cover_letters_resume_id_resumes'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_cover_letters_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cover_letters'))
    )
    op.create_index(op.f('ix_cover_letters_job_posting_id'), 'cover_letters', ['job_posting_id'], unique=False)
    op.create_index(op.f('ix_cover_letters_resume_id'), 'cover_letters', ['resume_id'], unique=False)
    op.create_index(op.f('ix_cover_letters_user_id'), 'cover_letters', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_cover_letters_user_id'), table_name='cover_letters')
    op.drop_index(op.f('ix_cover_letters_resume_id'), table_name='cover_letters')
    op.drop_index(op.f('ix_cover_letters_job_posting_id'), table_name='cover_letters')
    op.drop_table('cover_letters')
