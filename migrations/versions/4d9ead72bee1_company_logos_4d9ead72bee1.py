"""Logos for companies missing one

Revision ID: 4d9ead72bee1
Revises: 330c05dc9e1a
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

revision: str = '4d9ead72bee1'
down_revision: Union[str, None] = '330c05dc9e1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SAILPOINT_URL = "https://www.sailpoint.com/icon.png?icon.2hpqfovpzbyty.png"
MOOG_URL = "https://www.moog.com/etc.clientlibs/moog/clientlibs/moog.all-components/resources/images/favicon.ico"

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "ec710596-e123-45d7-aa70-cd4258be567b": ("logos/sailpoint-technologies-fz-llc-92f87b15.png", SAILPOINT_URL),
    "66bd682d-b632-4d22-81f1-b3c4b45de4cf": ("logos/sailpoint-technologies-japan-92f87b15.png", SAILPOINT_URL),
    "b8a0af22-f45f-408a-9970-34767f71cef6": ("logos/sailpoint-technologies-korea-limited-92f87b15.png", SAILPOINT_URL),
    "ac6da5f1-9463-4486-bb14-faa8fb1856a4": ("logos/sailpoint-technologies-uk-ltd-92f87b15.png", SAILPOINT_URL),
    "e82de8cc-1663-45c7-998e-c60283677332": ("logos/sailpoint-tecnologia-spain-s-l-92f87b15.png", SAILPOINT_URL),
    "b13a740a-5f01-4227-8106-501ffa3b1b52": ("logos/sol-systems-5ff9303f.png", "https://www.solsystems.com/wp-content/uploads/2017/02/favicon.png"),
    "8e54b339-a850-4524-8963-6005e6912c3a": ("logos/strava-e1f9f84f.png", "https://d3nn82uaxijpm6.cloudfront.net/apple-touch-icon-180x180.png?v=dLlWydWlG8"),
    "3f64020e-4aac-448a-9ab6-f0dc8e93c3f3": ("logos/tapestry-inc-f5d027c1.png", "https://www.tapestry.com/wp-content/themes/bootscore-child-main/img/favicon/cropped-tapestry-favicon-180x180.png"),
    "f9ff09fe-fac0-4c51-8c88-9437036b6f51": ("logos/td-synnex-corporation-bc4201ba.png", "https://www.tdsynnex.com/na/us/wp-content/uploads/sites/2/2026/04/TDSYNNEXLogo-clr-@2x.png"),
    "ed748f74-0d5b-4cd2-8f5e-d9c03c78920b": ("logos/teach-for-all-26e16793.png", "https://teachforall.org/themes/custom/tfal/favicons/apple-touch-icon.png"),
    "ff28d977-a10a-4798-9806-e04ea8ec0a7b": ("logos/team-accessories-limited-dba-moog-mro-services-36adb0df.png", MOOG_URL),
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
