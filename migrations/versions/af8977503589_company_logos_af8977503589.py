"""Logos for companies missing one

Revision ID: af8977503589
Revises: d4b994ff42e0
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

revision: str = 'af8977503589'
down_revision: Union[str, None] = 'd4b994ff42e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "46606ff3-2ada-47df-b8af-48608cded3bc": ("logos/60-insperity-services-l-p-750ccb81.png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://insperity.com&size=256"),
    "9f9dc031-59d1-46e7-8528-d7531f3ada40": ("logos/65-insperity-support-services-l-p-750ccb81.png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://insperity.com&size=256"),
    "656b8f24-52bd-4bd5-b3a7-59ea4001bee2": ("logos/a01-nationwide-mutual-insurance-co-ea9a7355.png", "https://media.nationwide.com/bolt/versions/7.6.0/bolt-logo-nw-horizontal-full.svg"),
    "4eb00ce0-7c59-419e-8acf-8658a9e0bf2c": ("logos/access-education-services-llc-0afac001.png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://wgu.edu&size=256"),
    "4347c525-6338-4688-a0d5-5c0113b59f2c": ("logos/afp-cuprum-9bd80ceb.png", "https://www.principal.com/modules/custom/pfg_core/assets/principal_full_bynder.png"),
    "327f2447-a70e-4f91-899a-099691a1ff66": ("logos/amerilife-us-llc-c9e6d854.png", "https://amerilife.com/apple-icon.png?apple-icon.5189ac28.png"),
    "d6173be7-96a7-4e27-8980-7835dc7eaecb": ("logos/arthouse-hotel-new-york-city-aafa68f0.png", "https://www.highgate.com/app/themes/highgate-corporate/dist/images/favicon/apple-touch-icon.png"),
    "a8a761d8-d94f-4546-bb53-534d866cb1c6": ("logos/arthur-j-gallagher-and-co-844dd25d.png", "https://www.ajg.com/assetbundles/GSC.Site.Gallagher.2/img/favicon/apple-touch-icon.png"),
    "1cc8fe95-d400-482c-b448-024be1f4775c": ("logos/astranis-e960d81a.png", "https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://astranis.com&size=256"),
    "43f105b2-cf17-4123-912c-17d551f8ab96": ("logos/attentive-936534b5.png", "https://cdn.prod.website-files.com/684306b795a2c402456e92ba/6a0b489cbf561cc597df3337_favicon.png"),
    "4946e85c-112c-4c70-a086-ffcbfebb3100": ("logos/blacksky-92930b70.png", "https://blacksky.com/wp-content/uploads/2025/01/cropped-blacksky-favicon-1-180x180.png"),
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
