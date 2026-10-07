"""Logos for companies missing one

Revision ID: d4b994ff42e0
Revises: a8d5e2f9b4c1
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

revision: str = 'd4b994ff42e0'
down_revision: Union[str, None] = 'a8d5e2f9b4c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "818c0f58-78f5-4f31-b877-c26ff4f80ea2": ("logos/21-marvell-asia-pte-ltd-3b97f934.png", "https://www.marvell.com/etc.clientlibs/marvell-com/clientlibs/clientlib-site/resources/icon-192x192.png"),
    "cd2fb42f-1583-425f-ac76-a410e81f0391": ("logos/34-columbia-gas-of-ohio-inc-8b2b66ad.png", "https://www.columbiagasohio.com/columbiagas.ico"),
    "2c50058d-3b30-42a1-8765-a0fc3be3a259": ("logos/38-columbia-gas-of-virginia-inc-8b2b66ad.png", "https://www.columbiagasva.com/columbiagas.ico"),
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
