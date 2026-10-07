"""Logos for companies missing one

Revision ID: 330c05dc9e1a
Revises: c9df7e17c4e3
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

revision: str = '330c05dc9e1a'
down_revision: Union[str, None] = 'c9df7e17c4e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "21fd9470-136f-47a9-b1b6-d88b5178ffda": ("logos/perficient-80d51354.png", "https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://perficient.com&size=256"),
    "08b3b975-c575-4e57-80b6-ede1d77b785a": ("logos/pol-aptiv-services-poland-s-a-787428ec.png", "https://www.aptiv.com/web-app-manifest-512x512.png"),
    "91efb432-8837-484f-96e8-acaf84f0e52a": ("logos/principal-9bd80ceb.png", "https://www.principal.com/modules/custom/pfg_core/assets/principal_full_bynder.png"),
    "da988470-f5fd-4406-8d6b-a1be73cbcfc5": ("logos/radiant-146d11da.png", "https://cdn.prod.website-files.com/690a89e276b0d65618a2b915/691e531f3f4b7270eb2017ca_webclip.png"),
    "916175d1-93fd-47b8-aa2b-c961735ceb9f": ("logos/rc-willey-f9868b4a.png", "https://www.rcwilley.com/favicons/apple-touch-icon-180x180.png"),
    "33d773ce-9016-4d65-b131-254baeba0f39": ("logos/rc-willey-home-furnishings-f9868b4a.png", "https://www.rcwilley.com/favicons/apple-touch-icon-180x180.png"),
    "a7a4a56d-3b95-4f43-884a-d1da601eea30": ("logos/resend-d5d6925a.png", "https://resend.com/static/favicons/favicon-marketing@180x180.png?v=1"),
    "2597227d-879e-4e74-9d38-e4d73d14e060": ("logos/residence-inn-salem-aafa68f0.png", "https://www.highgate.com/app/themes/highgate-corporate/dist/images/favicon/apple-touch-icon.png"),
    "612079a8-aa70-478b-8f1c-4a96529832ae": ("logos/r-r-cassidy-inc-d3036b15.png", "https://www.rrcassidy.com/wp-content/uploads/2025/03/cropped-RRC-Favicon-20201030_Option-6-300x300.png"),
    "c7990b44-ddc2-4424-adde-8509d404c823": ("logos/sailpoint-92f87b15.png", "https://www.sailpoint.com/icon.png?icon.2hpqfovpzbyty.png"),
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
