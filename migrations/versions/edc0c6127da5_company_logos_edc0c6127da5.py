"""Logos for companies missing one

Revision ID: edc0c6127da5
Revises: d52af1e3a649
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

revision: str = 'edc0c6127da5'
down_revision: Union[str, None] = 'd52af1e3a649'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "f8fd0d37-060a-495d-9be6-93064e93c8e1": ("logos/cencosud-brasil-d310032e.png", "https://www.cencosud.com/favicon.ico"),
    "53350253-959a-456b-8fe8-aca6b946d0c8": ("logos/chai-discovery-b5583a5d.png", "https://framerusercontent.com/images/GJGIRhiMoAIv0hl9KDGSIa5FCEw.png"),
    "edb8e236-47a5-419a-b0f3-7ede2352dca1": ("logos/chubb-external-bbf85e51.png", "https://www.chubb.com/content/dam/aem-chubb-global/logo/favicon.ico"),
    "0843d829-611b-4d2b-87fa-fac838c6607e": ("logos/clearlink-technologies-llc-5eac8574.png", "https://www.clearlink.com/icons/icon-512x512.png?v=18d5b9f9b3d10b161dd99c133367ab95"),
    "b58c6285-d6c5-491c-b2f2-3fb6b824fd8a": ("logos/coda-5c10e9a9.png", "https://cdn.coda.io/icons/png/color/coda-512.png"),
    "77861740-87f1-4c76-848d-4c923d35722d": ("logos/cognizant-5f31975a.png", "https://www.cognizant.com/content/dam/cognizant-dot-com/favicon/default-favicon/180x180.png"),
    "ca44c31d-e358-4f2e-b16f-86ed8fb2c4ff": ("logos/commonwealth-bank-of-australia-d04a5cf8.png", "https://www.commbank.com.au/etc/designs/commbank/favicon.ico"),
    "838450cf-17d7-4485-ba35-c93bb0aa08fc": ("logos/commonwealth-bank-of-australia-americas-d04a5cf8.png", "https://www.commbank.com.au/etc/designs/commbank/favicon.ico"),
    "040c8291-b1b7-4437-9eb5-37d4aefee723": ("logos/corporate-j-crew-group-llc-2c931e3f.png", "https://www.jcrew.com/next-static/images/apple-touch-icon-jcrew.png"),
    "6124869f-5534-4bbc-bb03-43fa538b3c05": ("logos/cowboy-space-corp-ef75c461.png", "https://framerusercontent.com/images/sWnMj5M5KoHlX2ldG4opPYLWE4.png"),
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
