"""job_posting_urls missing_since

Revision ID: c4d8e2a6f1b9
Revises: b7e2c4a91f05
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d8e2a6f1b9'
down_revision: Union[str, Sequence[str], None] = 'b7e2c4a91f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no default, so Postgres adds it without rewriting the
    # table. Every open URL starts with no miss: nothing closes until two
    # healthy crawls in a row have gone without listing it.
    op.add_column('job_posting_urls', sa.Column('missing_since', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_posting_urls', 'missing_since')
