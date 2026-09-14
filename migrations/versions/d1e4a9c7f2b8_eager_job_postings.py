"""eager job postings

Revision ID: d1e4a9c7f2b8
Revises: c752b41f99ef
Create Date: 2026-09-13 20:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd1e4a9c7f2b8'
down_revision: Union[str, Sequence[str], None] = 'c752b41f99ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('job_postings', 'scanned_at', existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column(
        'job_postings', 'extraction_status', existing_type=sa.String(length=20), server_default='pending'
    )

    # Backfill: every JobPostingUrl is now expected to have a JobPosting row
    # from the moment it's created (see app.services.jobs.get_or_create_job_posting),
    # not just once scanned. Give any pre-existing URL without one a pending
    # (or status-matching, for URLs whose scan already finished) shell row so
    # that invariant holds for data that existed before this migration.
    conn = op.get_bind()
    orphans = conn.execute(
        sa.text(
            """
            SELECT u.id, u.scan_status, u.last_scanned_at
            FROM job_posting_urls u
            LEFT JOIN job_postings p ON p.url_id = u.id
            WHERE p.id IS NULL
            """
        )
    ).fetchall()

    for url_id, scan_status, last_scanned_at in orphans:
        extraction_status = scan_status if scan_status in ("success", "failed") else "pending"
        scanned_at = last_scanned_at if extraction_status != "pending" else None
        conn.execute(
            sa.text(
                """
                INSERT INTO job_postings
                    (id, url_id, workplace_type, employment_type, scanned_at, extraction_status)
                VALUES
                    (:id, :url_id, 'unknown', 'unknown', :scanned_at, :extraction_status)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "url_id": str(url_id),
                "scanned_at": scanned_at,
                "extraction_status": extraction_status,
            },
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('job_postings', 'extraction_status', existing_type=sa.String(length=20), server_default=None)
    op.alter_column('job_postings', 'scanned_at', existing_type=sa.DateTime(timezone=True), nullable=False)
