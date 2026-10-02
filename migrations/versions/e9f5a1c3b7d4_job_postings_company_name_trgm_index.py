"""job_postings company_name trigram index

Revision ID: e9f5a1c3b7d4
Revises: d8e4f9b2a6c3
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e9f5a1c3b7d4'
down_revision: Union[str, Sequence[str], None] = 'd8e4f9b2a6c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Serves GET /jobs' company filter (build_job_search_statement's
    # `company_name ILIKE '%acme%'`): a company with few postings becomes an
    # index lookup instead of a scan of every posting. pg_trgm was created in
    # c7d3e8a1f5b2; IF NOT EXISTS keeps this migration standalone.
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    # CONCURRENTLY so the crawler's writes to job_postings aren't blocked while a
    # large table is indexed; it can't run inside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_postings_company_name_trgm '
            'ON job_postings USING gin (company_name gin_trgm_ops)'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_job_postings_company_name_trgm')
