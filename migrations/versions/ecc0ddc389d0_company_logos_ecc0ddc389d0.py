"""Logos for companies missing one

Revision ID: ecc0ddc389d0
Revises: ab2dedddd350
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

revision: str = 'ecc0ddc389d0'
down_revision: Union[str, None] = 'ab2dedddd350'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "2f5e0f8b-f87d-4908-b146-8b32cfa0f516": ("logos/fair-haven-community-health-care-1f3e2acf.png", "https://irp.cdn-website.com/97eee729/dms3rep/multi/FHCHC-Favicon--281-29.png"),
    "62ff3ab6-c0bb-4283-8d7e-bf0a0d684812": ("logos/frost-bank-158701b3.png", "https://www.frostbank.com/web-app-manifest-512x512.png"),
    "8628ad64-84cf-4c77-a407-2e42854ed172": ("logos/first-student-management-llc-a5556f36.png", "https://firststudentinc.com/wp-content/uploads/cropped-android-chrome-256x256-1-180x180.png"),
    "b3bafdfa-d7e7-4e60-99de-2606be2ce5df": ("logos/flatiron-health-983ff990.png", "rendered inline SVG on https://flatiron.com"),
    "b8651f69-7a10-4dbe-a2dd-1d57d8a34395": ("logos/expedia-group-ffb09c2f.png", "https://www.expediagroup.com/content/experience-fragments/expediagroup/live-copies/en-us/header/master/_jcr_content/root/header/logo.coreimg.svg/1779134972973/expedia-group-logo-24.svg"),
    "dda72c91-d537-4868-a32f-54528b8d0eb9": ("logos/generate-6fbf8cd5.png", "https://generatecapital.com/wp-content/uploads/2026/08/cropped-favicon2-180x180.png"),
    "c306ce4a-31f7-43ae-87b7-cec76c8ba16d": ("logos/geneva-rock-products-8d10382c.png", "https://www.clydeinc.com/wp-content/smush-webp/cropped-favicon-180x180.png.webp"),
    "ae000a59-2db6-456e-942a-6b11d83f9e2c": ("logos/givecampus-caa48663.png", "https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://givecampus.com&size=256"),
    "97649f71-53b9-4ce2-ac44-d5dac8c0a495": ("logos/glean-f31b6025.png", "https://cdn.prod.website-files.com/6204d0de7d994c00ea687da6/62dad22d43c2768536fe0cae_WebclipImage.png"),
    "6a9080c8-5d18-4e61-952a-a6a6fd89be75": ("logos/gm-financial-united-states-5be0ba99.png", "https://www.gmfinancial.com/content/dam/gmf/icons/favicon.ico"),
    "46484f5f-448a-4242-a6c2-c76940a949f6": ("logos/golden-pet-brands-49ffbe56.png", "https://goldenpetbrands.com/assets/apple-touch-icon.png"),
    "a91dd507-a765-4394-88d3-86f7b42f2121": ("logos/govwell-8e57154a.png", "https://cdn.prod.website-files.com/697732d0a9e7ed6e5a43811f/698f56cdef10bc5513ab554a_Webclip.png"),
    "c448538a-1614-4175-8e18-38ecd04e72a4": ("logos/hayes-locums-44de636c.png", "https://www.hayeslocums.com/wp-content/uploads/2024/12/HayesLocums_300x300_Logo.png"),
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
