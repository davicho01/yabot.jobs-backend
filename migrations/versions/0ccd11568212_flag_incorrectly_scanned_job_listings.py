"""flag incorrectly scanned job listings

Revision ID: 0ccd11568212
Revises: ff967d98ff2b
Create Date: 2026-09-26 15:08:45.486497

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0ccd11568212'
down_revision: Union[str, Sequence[str], None] = 'ff967d98ff2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('job_posting_urls', sa.Column('flagged_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('job_posting_urls', sa.Column('flag_reason', sa.String(length=30), nullable=True))
    op.add_column('job_posting_urls', sa.Column('flag_note', sa.Text(), nullable=True))
    op.add_column('job_posting_urls', sa.Column('flagged_by_user_id', sa.UUID(), nullable=True))
    op.create_index(
        'ix_job_posting_urls_flagged',
        'job_posting_urls',
        ['flagged_at'],
        unique=False,
        postgresql_where=sa.text('flagged_at IS NOT NULL'),
    )
    op.create_foreign_key(
        op.f('fk_job_posting_urls_flagged_by_user_id_users'),
        'job_posting_urls',
        'users',
        ['flagged_by_user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_job_posting_urls_flagged_by_user_id_users'), 'job_posting_urls', type_='foreignkey')
    op.drop_index('ix_job_posting_urls_flagged', table_name='job_posting_urls', postgresql_where=sa.text('flagged_at IS NOT NULL'))
    op.drop_column('job_posting_urls', 'flagged_by_user_id')
    op.drop_column('job_posting_urls', 'flag_note')
    op.drop_column('job_posting_urls', 'flag_reason')
    op.drop_column('job_posting_urls', 'flagged_at')
