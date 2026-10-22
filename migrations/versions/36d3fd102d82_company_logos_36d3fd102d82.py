"""Logos for companies missing one

Revision ID: 36d3fd102d82
Revises: 7b64bcf382cf
Create Date: 2026-10-22 00:00:00.000000

Each company's own logo, found by the logo agent for rows of
migrations/data/companies_missing_logos_with_domain.csv (whose logo_key
and logo_source_url columns record the same values). The files are already
in the static-pages bucket. Pinned as origin "url", like a logo an admin
set from a URL, so the logo.dev sync leaves them alone. Only fills
companies still without a logo; downgrade clears just the ones this set.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '36d3fd102d82'
down_revision: Union[str, None] = '7b64bcf382cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "4f7767b9-2dea-47ce-a4a2-88ff40684816": ("logos/baltimore-gas-and-electric-company-795fafa5.png", "https://www.bge.com/assets/svg/brand-logos/logo-bge.svg"),
    "b6c8e65f-ded1-4986-8cb0-7f830ad6d875": ("logos/bellagio-llc-8bba739f.png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://bellagio.com&size=256"),
}

companies = sa.table(
    "companies",
    sa.column("id", postgresql.UUID(as_uuid=False)),
    sa.column("logo_key", sa.String),
    sa.column("logo_origin", sa.String),
    sa.column("logo_source_url", sa.Text),
)


def upgrade() -> None:
    conn = op.get_bind()
    for company_id, (logo_key, source_url) in LOGOS.items():
        conn.execute(
            companies.update()
            .where(companies.c.id == company_id, companies.c.logo_key.is_(None))
            .values(logo_key=logo_key, logo_origin="url", logo_source_url=source_url)
        )


def downgrade() -> None:
    conn = op.get_bind()
    for company_id, (logo_key, _) in LOGOS.items():
        conn.execute(
            companies.update()
            .where(companies.c.id == company_id, companies.c.logo_key == logo_key)
            .values(logo_key=None, logo_origin=None, logo_source_url=None)
        )
