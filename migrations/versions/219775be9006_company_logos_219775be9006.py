"""Logos for companies missing one

Revision ID: 219775be9006
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

revision: str = '219775be9006'
down_revision: Union[str, None] = 'd4b994ff42e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "46606ff3-2ada-47df-b8af-48608cded3bc": ("logos/60-insperity-services-l-p-750ccb81.png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://insperity.com&size=256"),
    "9f9dc031-59d1-46e7-8528-d7531f3ada40": ("logos/65-insperity-support-services-l-p-750ccb81.png", "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://insperity.com&size=256"),
    "656b8f24-52bd-4bd5-b3a7-59ea4001bee2": ("logos/a01-nationwide-mutual-insurance-co-1d0d8e00.png", "https://media.nationwide.com/bolt/versions/7.6.0/bolt-logo-nw-horizontal-full.svg"),
    "4eb00ce0-7c59-419e-8acf-8658a9e0bf2c": ("logos/access-education-services-llc-27725228.png", "https://www.wgu.edu/etc.clientlibs/wgu/clientlibs/clientlib-site/resources/images/favicons/web-app-manifest-512x512.png"),
    "4347c525-6338-4688-a0d5-5c0113b59f2c": ("logos/afp-cuprum-e48804db.png", "https://cdn.cookielaw.org/logos/0f4a7e60-69a8-4cc0-a742-2263344f328c/01915bf2-8837-7e34-9c9b-8c6fb9b0d2a1/4fde8dc2-2dd5-4e72-9bb3-2d0bf64e0cb3/principal_full_(1).png"),
    "417640a7-3c8e-4e28-b7dc-5fce5ac9ae5d": ("logos/allstate-insurance-company-of-canada-permanent-6f304ac7.png", "https://www.allstate.ca/-/media/project/allstate/allstateca/header/logohands.svg"),
    "327f2447-a70e-4f91-899a-099691a1ff66": ("logos/amerilife-us-llc-c9e6d854.png", "https://amerilife.com/apple-icon.png?apple-icon.5189ac28.png"),
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
