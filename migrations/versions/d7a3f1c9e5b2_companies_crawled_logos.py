"""companies: self-hosted logos

Revision ID: d7a3f1c9e5b2
Revises: c4e8b2d6f1a9
Create Date: 2026-10-03 00:00:01.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd7a3f1c9e5b2'
down_revision: Union[str, Sequence[str], None] = 'c4e8b2d6f1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Filled in by sync_company_logos (app.services.company_logos), which
    # runs at the start of every generate_static_job_pages.py run, and by
    # the admin's manual logo endpoints.
    op.add_column('companies', sa.Column('logo_key', sa.String(length=300), nullable=True))
    op.add_column('companies', sa.Column('logo_domain', sa.String(length=255), nullable=True))
    op.add_column('companies', sa.Column('logo_source_url', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('logo_status', sa.String(length=20), nullable=True))
    op.add_column('companies', sa.Column('logo_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('companies', sa.Column('logo_origin', sa.String(length=20), nullable=True))
    op.add_column('companies', sa.Column('logo_etag', sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for column in ('logo_etag', 'logo_origin', 'logo_checked_at', 'logo_status', 'logo_source_url', 'logo_domain', 'logo_key'):
        op.drop_column('companies', column)
