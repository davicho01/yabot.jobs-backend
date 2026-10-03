"""companies (logo domains)

Revision ID: c4e8b2d6f1a9
Revises: a1c5e7f9b3d2
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c4e8b2d6f1a9'
down_revision: Union[str, Sequence[str], None] = 'a1c5e7f9b3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Rows (and their domains) are filled in by one_off/backfill_companies.py
    # and then on every scan — see app.services.company_logos.
    op.create_table(
        'companies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('company_key', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=True),
        sa.Column('domain_source', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_companies_company_key'), 'companies', ['company_key'], unique=True)
    op.create_index(op.f('ix_companies_domain'), 'companies', ['domain'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_companies_domain'), table_name='companies')
    op.drop_index(op.f('ix_companies_company_key'), table_name='companies')
    op.drop_table('companies')
