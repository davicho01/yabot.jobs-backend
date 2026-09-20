"""keep workplace tags as job posting location entries

Revision ID: c8e5b3a91d27
Revises: a4c9e2d7b1f6
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8e5b3a91d27'
down_revision: Union[str, Sequence[str], None] = 'a4c9e2d7b1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# a4c9e2d7b1f6 also dropped chunks that were only a workplace tag ("Remote",
# "Hybrid"), which made postings like "Arizona | Remote" (and ones whose whole
# location is "Remote") unfindable by a location search for "remote" — the old
# substring filter matched them. This re-derives the column keeping those
# chunks as entries. Frozen SQL copy of app.services.job_locations.split_locations
# as of this migration: split on ";" and "|", trim, drop empties and "..."
# truncation stubs, dedupe case-insensitively keeping first-seen order.
_BACKFILL_SQL = r"""
UPDATE job_postings AS p
SET locations = COALESCE((
    SELECT jsonb_agg(d.v ORDER BY d.n)
    FROM (
        SELECT DISTINCT ON (lower(s.v)) s.v, s.n
        FROM (
            SELECT btrim(t.x, E' \t\r\n') AS v, t.n
            FROM regexp_split_to_table(p.location, '[;|]') WITH ORDINALITY AS t(x, n)
        ) AS s
        WHERE s.v <> ''
          AND s.v !~ '\.\.\.$'
        ORDER BY lower(s.v), s.n
    ) AS d
), '[]'::jsonb)
WHERE p.location IS NOT NULL
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    # Data-only re-derivation: the previous revision's lossier rule isn't worth
    # restoring, and the column stays valid either way.
    pass
