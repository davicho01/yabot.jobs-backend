"""more reviewed crawl source names

Revision ID: d5a2b8c3e9f1
Revises: c4f1a9e7d2b6
Create Date: 2026-10-06 00:00:00.000000

A second reviewed list (migrations/data/source_names_2026_10_06_b.json), the
names c4f1a9e7d2b6's curation missed: hostnames outside the known ATS
patterns ("www.jobs-ups.com", "signicat.teamtailor.com"), squashed slugs
("Paloaltonetworks"), and a source named for a subsidiary that's really its
parent's whole board ("Baptist Health System" -> Tenet Healthcare). Same
rules as c4f1a9e7d2b6: by id, only while the row still has the old name and
isn't manual; downgrade restores.
"""
import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd5a2b8c3e9f1'
down_revision: Union[str, None] = 'c4f1a9e7d2b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DATA = Path(__file__).resolve().parents[1] / "data" / "source_names_2026_10_06_b.json"

crawl_sources = sa.table(
    "crawl_sources",
    sa.column("id", postgresql.UUID(as_uuid=False)),
    sa.column("name", sa.String),
    sa.column("name_source", sa.String),
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
                crawl_sources.c.name_source != "manual",
            )
            .values(name=entry["new"][:255], name_source=entry["name_source"])
        )


def downgrade() -> None:
    conn = op.get_bind()
    for entry in _entries():
        conn.execute(
            crawl_sources.update()
            .where(
                crawl_sources.c.id == entry["id"],
                crawl_sources.c.name == entry["new"],
                # what upgrade set; a row it skipped (already named so) stays
                crawl_sources.c.name_source == entry["name_source"],
            )
            .values(name=entry["old"], name_source="auto")
        )
