"""job-board crawl sources are not official

Revision ID: f7c4d1e8a3b9
Revises: e6b3c9d4f2a7
Create Date: 2026-10-06 00:00:00.000000

A source on a job board (linkedin.com, indeed.com, ...) re-lists other
companies' jobs: it's never a company's official site (see
app.services.company_names.is_official_source). New ones are created that way
(app.services.crawl_sources._upsert); this marks the existing ones — on
2026-10-06 just the rejected www.linkedin.com row. The domains are copied
from app.services.company_logos.JOB_BOARD_DOMAINS as of this migration, so
it doesn't change if that list does.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f7c4d1e8a3b9'
down_revision: Union[str, None] = 'e6b3c9d4f2a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JOB_BOARD_DOMAINS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
    "simplyhired.com", "wellfound.com", "builtin.com", "dice.com", "careerbuilder.com",
    "ycombinator.com", "workatastartup.com", "otta.com", "welcometothejungle.com",
    "usajobs.gov", "nlx.org", "dejobs.org", "jobsyn.org", "echojobs.io", "remoteok.com",
    "weworkremotely.com", "handshake.com", "joinhandshake.com",
)

# The board_url's host is the domain or a subdomain of it.
_HOST_MATCH = (
    r"^https?://([^/]*\.)?(" + "|".join(d.replace(".", r"\.") for d in JOB_BOARD_DOMAINS) + r")(:\d+)?(/|$)"
)


def upgrade() -> None:
    op.execute(
        f"UPDATE crawl_sources SET is_official = false WHERE is_official AND board_url ~* '{_HOST_MATCH}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE crawl_sources SET is_official = true WHERE NOT is_official AND board_url ~* '{_HOST_MATCH}'"
    )
