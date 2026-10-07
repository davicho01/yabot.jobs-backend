"""Logos for companies missing one

Revision ID: 3d74d958ceaf
Revises: ecc0ddc389d0
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

revision: str = '3d74d958ceaf'
down_revision: Union[str, None] = 'ecc0ddc389d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DXC = "https://upload.wikimedia.org/wikipedia/commons/7/73/DXC_Tech.png"

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "d7d20b65-646d-472e-a281-0755527e28ee": ("logos/juicebox-dcaac43e.png", "https://framerusercontent.com/images/5F0RNyA1458EzA6e5GLvfY7hTS8.png"),
    "4004ec22-8ad5-4700-add6-430b7ba7602b": ("logos/intuit-5c2df79b.png", "https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://intuit.com&size=256"),
    "8ea87c87-2945-44af-a23e-80091cd4db5c": ("logos/highgate-hotels-corporate-office-ny-aafa68f0.png", "https://www.highgate.com/app/themes/highgate-corporate/dist/images/favicon/apple-touch-icon.png"),
    "9fcf2d3a-7816-4867-8ce2-7a82f9984fa5": ("logos/hyperbolic-labs-6456c73c.png", "https://www.hyperbolic.ai/favicon-light.ico"),
    "1217348b-76fe-4542-a8c9-ff07c7bcf7cc": ("logos/i00m05-wipro-ge-healthcare-private-limited-1b90fad4.png", "https://www.gehealthcare.com/etc.clientlibs/gehealthcare/clientlibs/clientlib-site/resources/favicons/favicon-512x512.png"),
    "b6e2796b-6c2e-44cb-9eee-18b71cbe6b24": ("logos/immersive-9d6bf72f.png", "https://cdn.prod.website-files.com/6735fba9a631272fb4513263/67445c8ae1bfcd6cb4106bdc_Immersive%20Webclip.png"),
    "288fe384-5784-40e0-986a-55542ac46411": ("logos/impact-workforce-solutions-098f936b.png", "https://www.impactws.com/wp-content/uploads/2023/04/cropped-IM@2x-180x180.png"),
    "a61d81c7-6fbe-42ee-89c8-b89131ac28dc": ("logos/insurify-7e9e5ff9.png", "https://insurify.com/android-chrome-512x512.png"),
    "7363dab7-2a4c-4b75-b71e-431907ac83eb": ("logos/key-west-collection-35d08a58.png", "https://www.davidsonhospitality.com/content/themes/base/assets/img/favicon/apple-icon-180x180.png"),
    "c0d883e9-fc1a-4261-a583-279aafc8d3b1": ("logos/hu00-dxc-technology-magyarorsz-g-kft-e5a28ace.png", DXC),
    "0c4d1c7d-d945-4517-b166-ff360a8b173b": ("logos/iees-global-entserv-solutions-galway-limited-e5a28ace.png", DXC),
    "7e95c5ea-0971-4b5c-acc7-509df48259fb": ("logos/ina7-eit-services-india-p-ltd-formerly-hewlett-packard-global-soft-india-p-ltd-e5a28ace.png", DXC),
    "fd26d06d-9a78-417a-903b-070fa8a67f37": ("logos/ines-eit-services-india-p-ltd-formerly-hewlett-packard-global-soft-india-p-ltd-e5a28ace.png", DXC),
    "d06b7a93-4d60-446a-b342-6a2baa1c2853": ("logos/inet-eit-services-india-p-ltd-formerly-hewlett-packard-global-soft-india-p-ltd-e5a28ace.png", DXC),
    "ca7d495d-9caa-479b-b65e-98cb8e01b921": ("logos/it40-enterprise-services-italia-s-r-l-e5a28ace.png", DXC),
    "755aa977-715f-497c-a819-4e31e2d071a1": ("logos/it43-enterprise-tech-partners-italia-s-r-l-e5a28ace.png", DXC),
    "e1d7f2b9-a0fa-4450-8969-15239bbc1676": ("logos/jpes-dxc-technology-japan-ltd-e5a28ace.png", DXC),
    "9316cf13-4667-4b0d-ad0d-7d95e418907f": ("logos/hollywood-casino-morgantown-e0b469e1.png", "https://upload.wikimedia.org/wikipedia/commons/e/ec/Penn_Entertainment_logo.svg"),
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
