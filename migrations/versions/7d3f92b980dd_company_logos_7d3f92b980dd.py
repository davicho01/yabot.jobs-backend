"""Logos for companies missing one

Revision ID: 7d3f92b980dd
Revises: fa638094af78
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

revision: str = '7d3f92b980dd'
down_revision: Union[str, None] = 'fa638094af78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MNTN = "https://mountain.com/wp-content/themes/mountain/img/favicons/apple-touch-icon.png"
_MEDLINE = "https://static.captcha-delivery.com/captcha/assets/set/b22762c8240618fb720db4661212cb8ed09ea44d/logo.png?update_cache=645033690244757058"
_MOOG = "https://www.moog.com/etc.clientlibs/moog/clientlibs/moog.all-components/resources/images/favicon.ico"

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "d2d246e0-f85a-44b6-99c8-49cd4179d4b9": ("logos/mntn-4f7b8cc9.png", _MNTN),
    "dd4b5384-574a-42dc-bd93-641de0cabca5": ("logos/medline-international-iberia-slu-a804fb67.png", _MEDLINE),
    "bd8f1d7f-f112-46fb-b88f-25596fd328da": ("logos/medline-international-two-australia-pty-ltd-a804fb67.png", _MEDLINE),
    "94e694b6-667a-45a0-9b60-dda16a514167": ("logos/medline-operations-germany-gmbh-a804fb67.png", _MEDLINE),
    "a68aa5ed-1b6d-4cc5-8fe6-436b05876886": ("logos/medline-vietnam-trading-company-limited-a804fb67.png", _MEDLINE),
    "0f98e9eb-cdcd-4014-a0f9-a57441de5557": ("logos/msm-174-medline-soluciones-medicas-s-de-r-l-de-c-v-a804fb67.png", _MEDLINE),
    "a67ebdb2-a0b5-4ab4-9ece-069e6c277d66": ("logos/mxc-686-productos-medline-mexicali-s-de-r-l-de-c-v-a804fb67.png", _MEDLINE),
    "2c4a35fc-d1af-43f9-8ddc-0688ded767ff": ("logos/moog-inc-36adb0df.png", _MOOG),
    "b93898a4-8b70-47b8-8e60-0176abead4ec": ("logos/moog-australia-pty-ltd-36adb0df.png", _MOOG),
    "9805b3ba-d03a-4c6c-a082-9b3b1b4bafe4": ("logos/moog-controls-india-pvt-ltd-36adb0df.png", _MOOG),
    "e86a14c0-2392-4a3a-a6d1-b6402ff9c2da": ("logos/moog-controls-ltd-36adb0df.png", _MOOG),
    "e291275d-94b9-4f78-8fb9-54dde48504b4": ("logos/moog-india-technology-center-pvt-ltd-36adb0df.png", _MOOG),
    "7e7bfdbd-5be1-4442-a0dc-03913f34babd": ("logos/moog-japan-ltd-36adb0df.png", _MOOG),
    "99c2d87b-2d96-43ef-b068-2c2a1156fbf6": ("logos/moog-mdg-srl-36adb0df.png", _MOOG),
    "f58ad757-577c-4905-b2e0-4d5fc4ffd037": ("logos/moog-military-aircraft-llc-36adb0df.png", _MOOG),
    "8f248b66-5969-4e2c-be83-f08626e41423": ("logos/moog-wolverhampton-limited-36adb0df.png", _MOOG),
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
