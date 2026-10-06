"""crawl source owns the company name

Revision ID: b8e2d4f6a1c3
Revises: d7a3f1c9e5b2
Create Date: 2026-10-06 00:00:00.000000

The official company site (its crawl source) decides the company name every
one of its jobs shows — see app.services.company_names. Adds:

- crawl_sources.name_source: where `name` came from — "placeholder" (an
  unconfirmed slug/hostname label), "auto" (set from evidence) or "manual"
  (set by an admin; never touched automatically). Existing rows -> "auto".
- crawl_sources.sub_brands: brands a source's jobs may show instead of its
  name when the page names one (TJX -> HomeGoods, Marshalls).
- crawl_sources.is_official: the company's own careers site (true) vs a job
  board/aggregator (false).
- job_postings.page_company_name: the raw company name the scanned page gave,
  filled in by scans from now on. Existing rows are deliberately left NULL —
  the new naming applies to jobs scanned going forward, and copying 600k+
  rows here would rewrite the whole table under one long lock. Readers treat
  NULL as "the current company_name is the page's".

Every change here is metadata-only on Postgres 11+ (new columns with constant
defaults, or nullable without one), so it runs in seconds.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8e2d4f6a1c3'
down_revision: Union[str, Sequence[str], None] = 'd7a3f1c9e5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('crawl_sources', sa.Column('name_source', sa.String(length=12), server_default='auto', nullable=False))
    op.add_column(
        'crawl_sources',
        sa.Column('sub_brands', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column('crawl_sources', sa.Column('is_official', sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column('job_postings', sa.Column('page_company_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_postings', 'page_company_name')
    op.drop_column('crawl_sources', 'is_official')
    op.drop_column('crawl_sources', 'sub_brands')
    op.drop_column('crawl_sources', 'name_source')
