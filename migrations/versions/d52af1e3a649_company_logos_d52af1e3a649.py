"""Logos for companies missing one

Revision ID: d52af1e3a649
Revises: af8977503589
Create Date: 2026-10-07 00:00:00.000000

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

revision: str = 'd52af1e3a649'
down_revision: Union[str, None] = 'af8977503589'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "c37a3e25-5c69-456c-a85a-f285e5ac25f7": ("logos/bny-cec5b1f0.png", "https://www.bny.com/content/dam/bnymellon/web/favicons/favicon-152.png"),
    "92a5452e-9ca3-479f-a55b-55597202bf42": ("logos/burlington-coat-factory-of-tx-inc-f94f2c59.png", "https://www.burlington.com/apple-icon.png?apple-icon.42q8ww2fgirae.png"),
    "be65ab08-eb82-4b62-a5c0-d1b89309ea6b": ("logos/canacre-usa-llc-dedcf6e1.png", "https://images.squarespace-cdn.com/content/v1/5f3bcbcd66c85c78d1206ecc/1597760954725-IHOMUWUPZCA5R2WB1S26/favicon.ico?format=100w"),
    "8926e936-b932-45ab-9548-1cc6004621c6": ("logos/canyons-school-district-9f014423.png", "https://www.canyonsdistrict.org/wp-content/uploads/2020/05/cropped-canyons-district-logo-icon-180x180.png"),
    "991be0db-177a-4e63-96f0-57a4c5cf8460": ("logos/cba-nz-holdings-limited-dde7d3de.png", "https://www.commbank.com.au/content/dam/commbank/commBank-logo.svg"),
    "cb337649-7ff0-47de-add0-5ccde4d7af7a": ("logos/cba-services-private-limited-dde7d3de.png", "https://www.commbank.com.au/content/dam/commbank/commBank-logo.svg"),
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
