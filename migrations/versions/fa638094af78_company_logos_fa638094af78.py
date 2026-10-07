"""Logos for companies missing one

Revision ID: fa638094af78
Revises: 3d74d958ceaf
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

revision: str = 'fa638094af78'
down_revision: Union[str, None] = '3d74d958ceaf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LINE = "https://vos.line-scdn.net/landpress-content-v2-3ub8nanc40829phmlme9ov4o/1708056773171.png"
_MEDLINE = "https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://medline.com&size=256"

# companies.id -> (logo_key, logo_source_url)
LOGOS = {
    "721a5fb4-2cbc-4085-8642-83c927b767fc": ("logos/ladgov-corporation-479a2986.png", "https://t3.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=http://ladgov.com&size=256"),
    "a4edb866-1c04-432d-af2e-0e01a3030974": ("logos/leap-7254bada.png", "https://www.leaphealth.com/web-app-manifest-512x512.png"),
    "e87c7c6c-b2db-4fa6-b4a9-9a0eb703d6ea": ("logos/line-company-thailand-f5d771a3.png", _LINE),
    "8d8ec3c6-5f4c-4671-bce9-19cd6d72a735": ("logos/line-pay-plus-f5d771a3.png", _LINE),
    "36c97bbc-882e-4bc1-ab91-ec4d7279a783": ("logos/line-plus-f5d771a3.png", _LINE),
    "f719f11b-4e89-4995-82b9-b3d0b63f931f": ("logos/line-taiwan-f5d771a3.png", _LINE),
    "18471aaa-c036-4137-a184-1034ecb8ff6b": ("logos/livekit-7ce97fcc.png", "https://livekit.com/images/livekit-apple-touch.png"),
    "5ad615f2-d3f8-462e-ab61-d693de8a2cd2": ("logos/london-hire-group-073893e8.png", "https://www.londonhireltd.com/custom-assets/img/london-hire-logo.png"),
    "c3dac724-5c85-4328-bbb8-3a628468171b": ("logos/lyka-44288fbf.png", "https://www.d2c.lyka.com.au/_astro/logo-192x192.DM5EhQBv.png"),
    "d8d1f271-a20f-49b1-abaa-bea9b256f4a5": ("logos/medline-ce45eb9a.png", _MEDLINE),
    "76d0ecc4-979e-45c0-b03a-6001a827d138": ("logos/medline-assembly-slovakia-ce45eb9a.png", _MEDLINE),
    "76188afa-9573-4416-a85b-4707f4396129": ("logos/medline-austria-gmbh-ce45eb9a.png", _MEDLINE),
    "7d418f3f-b150-4e73-841d-9da72f257523": ("logos/medline-healthcare-industries-pvt-ltd-ce45eb9a.png", _MEDLINE),
    "2b8c34e8-b952-498b-9282-f0856f55248c": ("logos/medline-industries-india-pvt-ltd-ce45eb9a.png", _MEDLINE),
    "89c3cb21-0687-4555-8b53-a6777c64b9b0": ("logos/medline-industries-lp-ce45eb9a.png", _MEDLINE),
    "cc6488d6-99ab-4da1-9b41-28f15cf379da": ("logos/medline-international-belgium-bv-ce45eb9a.png", _MEDLINE),
    "4514a695-b093-4bf4-9aa1-9069834c1bba": ("logos/medline-international-bv-ce45eb9a.png", _MEDLINE),
    "c0108d60-3508-4c9f-bb37-90f7bd6b06d7": ("logos/medline-international-france-chateaubriant-ce45eb9a.png", _MEDLINE),
    "6d432fca-d2d6-4b32-9e8e-bf467ae80dae": ("logos/medline-international-france-sqy-ce45eb9a.png", _MEDLINE),
    "1b3b6d79-b3b1-48a8-9712-d1151cff5f6b": ("logos/medline-international-germany-gmbh-ce45eb9a.png", _MEDLINE),
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
