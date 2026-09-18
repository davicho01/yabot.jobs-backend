"""tailored resume scores

Revision ID: e945b0004794
Revises: a4d7e9f1c6b2
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e945b0004794'
down_revision: Union[str, Sequence[str], None] = 'a4d7e9f1c6b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('tailored_resume_scores',
    sa.Column('tailored_resume_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('job_posting_id', sa.UUID(), nullable=False),
    sa.Column('overall_score', sa.Integer(), nullable=False),
    sa.Column('matched_keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('missing_keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('raw_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], name=op.f('fk_tailored_resume_scores_job_posting_id_job_postings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tailored_resume_id'], ['tailored_resumes.id'], name=op.f('fk_tailored_resume_scores_tailored_resume_id_tailored_resumes'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_tailored_resume_scores_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tailored_resume_scores'))
    )
    op.create_index(op.f('ix_tailored_resume_scores_job_posting_id'), 'tailored_resume_scores', ['job_posting_id'], unique=False)
    op.create_index(op.f('ix_tailored_resume_scores_tailored_resume_id'), 'tailored_resume_scores', ['tailored_resume_id'], unique=False)
    op.create_index(op.f('ix_tailored_resume_scores_user_id'), 'tailored_resume_scores', ['user_id'], unique=False)

    op.add_column('user_job_applications', sa.Column('latest_tailored_resume_score_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f('fk_user_job_applications_latest_tailored_resume_score_id_tailored_resume_scores'),
        'user_job_applications',
        'tailored_resume_scores',
        ['latest_tailored_resume_score_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('fk_user_job_applications_latest_tailored_resume_score_id_tailored_resume_scores'),
        'user_job_applications',
        type_='foreignkey',
    )
    op.drop_column('user_job_applications', 'latest_tailored_resume_score_id')

    op.drop_index(op.f('ix_tailored_resume_scores_user_id'), table_name='tailored_resume_scores')
    op.drop_index(op.f('ix_tailored_resume_scores_tailored_resume_id'), table_name='tailored_resume_scores')
    op.drop_index(op.f('ix_tailored_resume_scores_job_posting_id'), table_name='tailored_resume_scores')
    op.drop_table('tailored_resume_scores')
