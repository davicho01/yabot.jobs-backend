"""subscription evaluation cap

Revision ID: e7f1a9c3b2d6
Revises: d4f9b2e6a1c3
Create Date: 2026-10-01 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e7f1a9c3b2d6'
down_revision: Union[str, Sequence[str], None] = 'd4f9b2e6a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('subscription_evaluation_limit', sa.Integer(), server_default='100', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('subscription_evaluations_used', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('subscription_evaluations_period_end', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'subscription_evaluation_jobs',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_posting_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_posting_id'], ['job_postings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'job_posting_id', 'period_end', name='uq_subscription_eval_jobs_user_job_period'
        ),
    )
    op.create_index(
        op.f('ix_subscription_evaluation_jobs_user_id'), 'subscription_evaluation_jobs', ['user_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_subscription_evaluation_jobs_user_id'), table_name='subscription_evaluation_jobs')
    op.drop_table('subscription_evaluation_jobs')
    op.drop_column('users', 'subscription_evaluations_period_end')
    op.drop_column('users', 'subscription_evaluations_used')
    op.drop_column('users', 'subscription_evaluation_limit')
