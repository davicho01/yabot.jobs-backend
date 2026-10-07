"""Logos for companies missing one

Revision ID: c9df7e17c4e3
Revises: 7d3f92b980dd
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

revision: str = 'c9df7e17c4e3'
down_revision: Union[str, None] = '7d3f92b980dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DXC_URL = "https://avatars.githubusercontent.com/u/6401996?s=256&v=4"

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "695f3aa8-78e3-49d7-b87a-0362687f9862": ("logos/my20-entserv-malaysia-sdn-bhd-03212097.png", DXC_URL),
    "228d1471-d4bc-44c0-811a-b07e986d163d": ("logos/mysten-labs-fc9ebccb.png", "https://avatars.githubusercontent.com/u/88845815?s=256&v=4"),
    "fb4081aa-5ad8-4999-8c8c-03254471ea0c": ("logos/newsela-06d1cd91.png", "https://cdn.prod.website-files.com/6643998773f47007e008ebae/66f1805da7952f0e6ee76574_Webclip.png"),
    "01b4420a-984c-4eb1-aee8-47400effa9a9": ("logos/nisource-dfbc3621.png", "https://www.nisource.com/favicon.ico"),
    "ded59ed9-f087-4298-b4d7-9d031f3165a2": ("logos/nlex-enterprise-services-international-trade-b-v-saudi-arabian-branch-03212097.png", DXC_URL),
    "b1e5251f-2abb-49bb-83d3-e81648d21b5f": ("logos/north-houston-pole-line-lp-e372af18.png", "https://www.nhplc.com/wp-content/uploads/2022/03/cropped-NHPL600x100-2-540x90-1-180x180.png"),
    "0245b775-d9c1-44a2-8334-2c5ffd616357": ("logos/northstar-energy-solutions-llc-98749567.png", "https://www.nses.com/wp-content/themes/northstar-services/apple-touch-icon.png?v=1.0.90"),
    "46d8e44d-1a5c-4ec1-822d-a691b85a0acc": ("logos/numa-c81b1c47.png", "https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://numastays.com&size=256"),
    "512adcf4-82d4-4ead-8302-80de95a1a4bf": ("logos/nzes-dxc-enterprise-nz-03212097.png", DXC_URL),
    "327cd202-d802-4183-ae54-2aa0831442bd": ("logos/omni-hotels-resorts-4d9e04d6.png", "https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://omnihotels.com&size=256"),
    "3518d7f9-b084-4e0b-ac63-114865ea47af": ("logos/openrouter-e5948990.png", "https://openrouter.ai/icon-pwa-512.png"),
    "cbd826a1-bdd6-461b-9a83-69da8d927ac7": ("logos/pa20-enterprise-services-panama-s-de-r-l-03212097.png", DXC_URL),
    "2d71fd19-5180-40fb-bfe3-142de91a2d04": ("logos/painpoint-health-69ea3fae.png", "https://painpointhealth.com/wp-content/uploads/2021/06/cropped-ppicon-300x300.png"),
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
