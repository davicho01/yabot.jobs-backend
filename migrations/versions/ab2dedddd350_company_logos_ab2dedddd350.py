"""Logos for companies missing one

Revision ID: ab2dedddd350
Revises: edc0c6127da5
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

revision: str = 'ab2dedddd350'
down_revision: Union[str, None] = 'edc0c6127da5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "44205afa-0024-4c0f-bfd6-8af59914bccb": ("logos/deuna-fe8e2f9e.png", "https://cdn.prod.website-files.com/66269d6696cb4969aa25e070/663ac650777d6c76690e337f_WEBCLIP.webp"),
    "d01676ec-9f06-40a5-9614-0c0f050831a5": ("logos/digitalocean-0c982ea3.png", "https://www.digitalocean.com/_do-www-marketing/a8a9a0b5/_next/static/media/apple-touch-icon.9537f22a.png"),
    "0d62ead3-7e99-43c5-a6a4-700fbb507f23": ("logos/e11even-club-hotel-residences-aafa68f0.png", "https://www.highgate.com/app/themes/highgate-corporate/dist/images/favicon/apple-touch-icon.png"),
    "a477fdfc-3936-44a9-901c-9b01a68224eb": ("logos/emergent-holdings-6b8972c2.png", "https://emergentholdingsinc.com/wp-content/uploads/2026/07/EH_clr_mrk.png"),
    "c5cfafb4-acaf-4eac-bb81-dc446096f10e": ("logos/eneba-86d91cd2.png", "https://static.eneba.games/icon_512x512.1116fbe57fb071d8348e986b108fd268.png"),
    "32d65f15-c03b-43ca-b46e-6678346d183e": ("logos/everfield-95728606.png", "https://everfield.com/hubfs/Everfield%20Favicon.png"),
    "957d69d4-8417-4635-95e8-7da7403b6cb4": ("logos/doterra-6afe63a1.png", "https://www.doterra.com/_ui/desktop/common/images/apple-touch-icon-200x200.png"),
    "699d9a01-a649-4b7e-a458-5c5c4fea8043": ("logos/el-costa-rica-s-r-l-c3134fcf.png", "https://assets-us-01.kc-usercontent.com/6239a81e-8f0f-0040-a1df-b4932a10f6ae/261e0bb6-68dd-4f07-8798-cce9b0c42cc3/logo.svg?q=75&w=96&auto=format&fm=webp&lossless=true"),
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
