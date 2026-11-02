"""Daily full reads for TalentBrew, iCIMS and Eightfold boards

Revision ID: f7c2a9d4b6e1
Revises: e6b1c4d8f2a3
Create Date: 2026-11-01 00:00:00.000000

Switches on the uncapped crawl (a daily full read, see
crawl_schedule.full_read_due) for every TalentBrew, iCIMS and Eightfold board.
Their capped crawls list only a slice of a board (recent postings, or the
first max_jobs_per_crawl), so a job that came down was never recorded as
missing and never closed: a 2026-11-01 audit found 104,131 open jobs on these
three platforms, 80,154 of them not seen on their board for 3+ days.

Mayo Clinic and Eaton were switched on by hand on 2026-11-01 as a pilot; this
sets the rest and is a no-op for those two. Closing stays throttled by
record_board_presence's daily limit (20% of a source's open jobs per day).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f7c2a9d4b6e1'
down_revision: Union[str, Sequence[str], None] = 'e6b1c4d8f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Keep in step with app.services.ats_adapters.UNCAPPED_BY_DEFAULT_ATS_TYPES.
_ATS_TYPES = ('talentbrew', 'icims', 'eightfold')


def upgrade() -> None:
    op.execute(
        sa.text('UPDATE crawl_sources SET uncapped = true WHERE ats_type IN :types').bindparams(
            sa.bindparam('types', value=_ATS_TYPES, expanding=True)
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text('UPDATE crawl_sources SET uncapped = false WHERE ats_type IN :types').bindparams(
            sa.bindparam('types', value=_ATS_TYPES, expanding=True)
        )
    )
