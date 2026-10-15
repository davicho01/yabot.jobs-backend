"""auto_score_runs, user_job_applications.ready_to_apply_at

Revision ID: d4f8a2c6e1b3
Revises: c2a7e5d9f4b1
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4f8a2c6e1b3'
down_revision: Union[str, Sequence[str], None] = 'c2a7e5d9f4b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'auto_score_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_posting_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('job_postings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('resume_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('resumes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('resume_score_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('resume_scores.id', ondelete='SET NULL'), nullable=True),
        sa.Column('tailored_resume_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tailored_resumes.id', ondelete='SET NULL'), nullable=True),
        sa.Column('tailored_score_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tailored_resume_scores.id', ondelete='SET NULL'), nullable=True),
        sa.Column('resume_score', sa.Integer(), nullable=True),
        sa.Column('tailored_score', sa.Integer(), nullable=True),
        sa.Column('best_score', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index(
        'ix_auto_score_runs_user_job_created', 'auto_score_runs', ['user_id', 'job_posting_id', 'created_at']
    )
    op.add_column('user_job_applications', sa.Column('ready_to_apply_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user_job_applications', 'ready_to_apply_at')
    op.drop_index('ix_auto_score_runs_user_job_created', table_name='auto_score_runs')
    op.drop_table('auto_score_runs')
