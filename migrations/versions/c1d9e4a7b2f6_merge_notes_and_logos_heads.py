"""merge crawl-source notes and company-logos heads

Revision ID: c1d9e4a7b2f6
Revises: b3e7f1a9c2d4, 4d9ead72bee1
Create Date: 2026-10-07 00:00:00.000000

The logo-agent migration 4d9ead72bee1 was generated against 330c05dc9e1a
while b3e7f1a9c2d4 (crawl_sources.notes) was landing on the same parent,
leaving two heads — `alembic upgrade head` refuses to run with two. No
schema change; this only joins them.
"""
from typing import Sequence, Union

revision: str = 'c1d9e4a7b2f6'
down_revision: Union[str, Sequence[str], None] = ('b3e7f1a9c2d4', '4d9ead72bee1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
