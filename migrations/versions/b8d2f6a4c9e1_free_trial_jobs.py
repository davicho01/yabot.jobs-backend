"""free trial unlocks per job

Revision ID: b8d2f6a4c9e1
Revises: a3c9e5f1d7b2
Create Date: 2026-10-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b8d2f6a4c9e1'
down_revision: Union[str, Sequence[str], None] = 'a3c9e5f1d7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'free_trial_jobs',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_posting_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'job_posting_id', name='uq_free_trial_jobs_user_job'),
    )
    op.create_index(op.f('ix_free_trial_jobs_user_id'), 'free_trial_jobs', ['user_id'], unique=False)
    # Scores made on the free trial before unlocks were per job were tagged
    # raw_response.free_trial — those jobs count as unlocked.
    op.execute(
        """
        INSERT INTO free_trial_jobs (id, user_id, job_posting_id)
        SELECT gen_random_uuid(), user_id, job_posting_id
        FROM resume_scores
        WHERE raw_response ->> 'free_trial' = 'true'
        GROUP BY user_id, job_posting_id
        ON CONFLICT DO NOTHING
        """
    )
    # The count now means "jobs unlocked". Under the old scoring-only rule a
    # re-score of the same job could spend a second evaluation; give those
    # back.
    op.execute(
        """
        UPDATE users
        SET free_evaluations_used = unlocked.n
        FROM (
            SELECT u.id, count(f.id) AS n
            FROM users u LEFT JOIN free_trial_jobs f ON f.user_id = u.id
            GROUP BY u.id
        ) AS unlocked
        WHERE users.id = unlocked.id AND users.free_evaluations_used > unlocked.n
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_free_trial_jobs_user_id'), table_name='free_trial_jobs')
    op.drop_table('free_trial_jobs')
