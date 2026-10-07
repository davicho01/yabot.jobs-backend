"""company official-site check

Revision ID: a8d5e2f9b4c1
Revises: f7c4d1e8a3b9
Create Date: 2026-10-06 00:00:00.000000

Records the last look for a company's own careers site
(app.services.official_sites, find_official_sites.py): when, and what it
found. Nullable columns without a default — metadata-only on Postgres.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a8d5e2f9b4c1'
down_revision: Union[str, None] = 'f7c4d1e8a3b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('companies', sa.Column('official_site_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('companies', sa.Column('official_site_result', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('companies', 'official_site_result')
    op.drop_column('companies', 'official_site_checked_at')
