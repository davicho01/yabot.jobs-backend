"""crawl source notes

Revision ID: b3e7f1a9c2d4
Revises: 330c05dc9e1a
Create Date: 2026-10-06 00:00:00.000000

Free-text reason a crawl source is in its status (why it was rejected or
flagged for deletion), set through PATCH /admin/crawl-sources/{id}. Nullable
column without a default — metadata-only on Postgres.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3e7f1a9c2d4'
down_revision: Union[str, None] = '330c05dc9e1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('crawl_sources', sa.Column('notes', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('crawl_sources', 'notes')
