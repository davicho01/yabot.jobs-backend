"""Logos for companies missing one

Revision ID: 7b64bcf382cf
Revises: 8e2a5f1c9d47
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

revision: str = '7b64bcf382cf'
down_revision: Union[str, None] = '8e2a5f1c9d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "fe844b42-426f-4f5f-8f10-d265b4dd9853": ("logos/thrive-market-78c386dc.png", "https://assets.thrivemarket.com/favicon-194x194.png?v=pgegAroPo5"),
    "a537d5f9-1a33-4292-8604-5817234bc227": ("logos/tines-d71832fb.png", "https://www.tines.com/v5-assets/favicon.png"),
    "7181e17d-43f0-419a-8c41-1d51e9051285": ("logos/trainline-f443ce59.png", "https://trainlinegroup.com/media/gpfosrnq/logo-mint-2-1.svg"),
    "168af648-7a3d-49e4-89e8-1d1e02d8033e": ("logos/truist-cig-llc-1b1805b6.png", "https://www.truist.com/favicon.ico"),
    "1dd65132-5931-4c19-a236-c9ce83c16891": ("logos/truist-community-capital-llc-1b1805b6.png", "https://www.truist.com/favicon.ico"),
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
