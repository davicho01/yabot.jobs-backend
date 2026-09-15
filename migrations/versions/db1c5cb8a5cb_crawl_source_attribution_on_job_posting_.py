"""crawl source attribution on job posting urls

Revision ID: db1c5cb8a5cb
Revises: 9c227a759e98
Create Date: 2026-09-15 18:51:27.468429

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'db1c5cb8a5cb'
down_revision: Union[str, Sequence[str], None] = '9c227a759e98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('job_posting_urls', sa.Column('crawl_source_id', sa.UUID(), nullable=True))
    op.create_index(
        op.f('ix_job_posting_urls_crawl_source_id'), 'job_posting_urls', ['crawl_source_id'], unique=False
    )
    op.create_foreign_key(
        op.f('fk_job_posting_urls_crawl_source_id_crawl_sources'),
        'job_posting_urls',
        'crawl_sources',
        ['crawl_source_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('fk_job_posting_urls_crawl_source_id_crawl_sources'), 'job_posting_urls', type_='foreignkey'
    )
    op.drop_index(op.f('ix_job_posting_urls_crawl_source_id'), table_name='job_posting_urls')
    op.drop_column('job_posting_urls', 'crawl_source_id')
