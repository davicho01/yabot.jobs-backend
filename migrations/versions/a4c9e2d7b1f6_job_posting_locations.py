"""job posting locations list

Revision ID: a4c9e2d7b1f6
Revises: b7c1d4e8a2f3
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4c9e2d7b1f6'
down_revision: Union[str, Sequence[str], None] = 'b7c1d4e8a2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen SQL copy of app.services.job_locations.split_locations as of this
# migration (migrations shouldn't import app code that will keep evolving):
# split on ";" and "|", trim, drop empties, bare workplace tags ("Remote",
# "Hybrid (Travel-Required)") and "..." truncation stubs, then dedupe
# case-insensitively keeping first-seen order.
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
          AND s.v !~* '^(remote|hybrid|on-?site|in-?office|remote-friendly)( \(.*\))?$'
          AND s.v !~ '\.\.\.$'
        ORDER BY lower(s.v), s.n
    ) AS d
), '[]'::jsonb)
WHERE p.location IS NOT NULL
"""


def upgrade() -> None:
    """Upgrade schema."""
    # server_default only so existing rows satisfy NOT NULL while the column
    # is added; the model supplies the value on insert, so it's dropped again.
    op.add_column(
        'job_postings',
        sa.Column('locations', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )
    op.execute(_BACKFILL_SQL)
    op.alter_column('job_postings', 'locations', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_postings', 'locations')
