"""reviewed crawl source names and sub-brands

Revision ID: c4f1a9e7d2b6
Revises: b8e2d4f6a1c3
Create Date: 2026-10-06 00:00:00.000000

Sets the reviewed company names (and a few sub-brands) on prod's crawl
sources, from migrations/data/source_names_2026_10_06.json — one entry per
source, by id:

- "auto": the curation report's fixes (a "(…)" admin note dropped, a slug or
  hostname replaced by the name its pages and board address agree on).
- "manual": names decided by hand from the review list (jobs.l3harris.com ->
  "L3Harris Technologies"); nothing renames these automatically again.
- "sub_brands": brands its jobs may show instead of the name (TJX -> HomeGoods).

Only crawl_sources rows change; jobs pick the names up when next scanned.
A row is only changed while it still has the reviewed old name and an admin
hasn't named it by hand since, so a database without these sources (local
dev) is left alone. Downgrade restores the old names and empty sub-brands.
"""
import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c4f1a9e7d2b6'
down_revision: Union[str, None] = 'b8e2d4f6a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DATA = Path(__file__).resolve().parents[1] / "data" / "source_names_2026_10_06.json"

crawl_sources = sa.table(
    "crawl_sources",
    sa.column("id", postgresql.UUID(as_uuid=False)),
    sa.column("name", sa.String),
    sa.column("name_source", sa.String),
    sa.column("sub_brands", postgresql.JSONB),
)


def _entries() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def upgrade() -> None:
    conn = op.get_bind()
    for entry in _entries():
        values = {}
        if "new" in entry:
            values.update(name=entry["new"][:255], name_source=entry["name_source"])
        if "sub_brands" in entry:
            values["sub_brands"] = entry["sub_brands"]
        conn.execute(
            crawl_sources.update()
            .where(
                crawl_sources.c.id == entry["id"],
                crawl_sources.c.name == entry["old"],
                crawl_sources.c.name_source != "manual",
            )
            .values(**values)
        )


def downgrade() -> None:
    conn = op.get_bind()
    for entry in _entries():
        values = {}
        if "new" in entry:
            values.update(name=entry["old"], name_source="auto")
        if "sub_brands" in entry:
            values["sub_brands"] = []
        conn.execute(
            crawl_sources.update()
            .where(crawl_sources.c.id == entry["id"], crawl_sources.c.name == entry.get("new", entry["old"]))
            .values(**values)
        )
