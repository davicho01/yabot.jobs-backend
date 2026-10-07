"""Tenet Healthcare sub-brands

Revision ID: e6b3c9d4f2a7
Revises: d5a2b8c3e9f1
Create Date: 2026-10-06 00:00:00.000000

Tenet's two boards (its Oracle site and jobs.tenethealth.com) list every
hospital it runs, so since the source owns the name, all of them showed
"Tenet Healthcare". These sub-brands (migrations/data/
source_sub_brands_2026_10_06_c.json — its systems and hospitals with 20+
jobs, specific names first since the first match wins) let a job whose page
names one show it instead ("Abrazo West Campus" -> "Abrazo"). Set by id,
only while the source still has the reviewed name and no sub-brands;
downgrade clears what this set.
"""
import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e6b3c9d4f2a7'
down_revision: Union[str, None] = 'd5a2b8c3e9f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DATA = Path(__file__).resolve().parents[1] / "data" / "source_sub_brands_2026_10_06_c.json"

crawl_sources = sa.table(
    "crawl_sources",
    sa.column("id", postgresql.UUID(as_uuid=False)),
    sa.column("name", sa.String),
    sa.column("sub_brands", postgresql.JSONB),
)


def _entries() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def upgrade() -> None:
    conn = op.get_bind()
    for entry in _entries():
        conn.execute(
            crawl_sources.update()
            .where(
                crawl_sources.c.id == entry["id"],
                crawl_sources.c.name == entry["old"],
                crawl_sources.c.sub_brands == sa.cast([], postgresql.JSONB),
            )
            .values(sub_brands=entry["sub_brands"])
        )


def downgrade() -> None:
    conn = op.get_bind()
    for entry in _entries():
        conn.execute(
            crawl_sources.update()
            .where(
                crawl_sources.c.id == entry["id"],
                crawl_sources.c.sub_brands == sa.cast(entry["sub_brands"], postgresql.JSONB),
            )
            .values(sub_brands=[])
        )
