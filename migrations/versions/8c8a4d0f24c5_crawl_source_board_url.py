"""crawl source board url

Replaces CrawlSource.ats_type/board_token/detected_domain's decomposed,
opaque identifier with a single board_url column — every platform's
token is fully recoverable from its real board URL (see
app.services.ats_adapters.detect_ats_source/canonical_board_url), so
there's no need to persist it separately. The templates below are a
frozen, migration-local copy of canonical_board_url's forward mapping
(and its inverse, for downgrade) — deliberately not importing app code
into a migration, so this keeps working exactly as written even if
ats_adapters.py changes later.

Revision ID: 8c8a4d0f24c5
Revises: db1c5cb8a5cb
Create Date: 2026-09-16 00:17:34.141847

"""
from typing import Sequence, Union
from urllib.parse import parse_qs, urlsplit

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c8a4d0f24c5'
down_revision: Union[str, Sequence[str], None] = 'db1c5cb8a5cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SIMPLE_TEMPLATES = {
    'greenhouse': 'https://boards.greenhouse.io/{token}',
    'lever': 'https://jobs.lever.co/{token}',
    'ashby': 'https://jobs.ashbyhq.com/{token}',
    'bamboohr': 'https://{token}.bamboohr.com/careers',
    'personio': 'https://{token}.jobs.personio.de/',
    'jazzhr': 'https://{token}.applytojob.com/apply/jobs',
    'recruitee': 'https://{token}.recruitee.com/',
    'breezyhr': 'https://{token}.breezy.hr/',
    'workable': 'https://apply.workable.com/{token}/',
    'amazon': 'https://www.amazon.jobs',
    'google': 'https://www.google.com/about/careers',
    'apple': 'https://jobs.apple.com',
}
# Platforms whose stored board_url has the token embedded as the first
# subdomain label (https://{token}.example.com/...) rather than a path
# segment — used by both directions to extract/rebuild consistently.
_SUBDOMAIN_ATS_TYPES = {'bamboohr', 'personio', 'jazzhr', 'recruitee', 'breezyhr'}


def _board_url_for(ats_type: str | None, board_token: str | None, detected_domain: str | None) -> str:
    if ats_type is None:
        domain = detected_domain or 'unknown.invalid'
        return f'https://{domain}'
    if ats_type == 'workday':
        company, instance, site = board_token.split('/')
        return f'https://{company}.{instance}.myworkdayjobs.com/{site}'
    if ats_type == 'adp':
        cid, cc_id = board_token.split('/')
        return (
            'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html'
            f'?cid={cid}&ccId={cc_id}'
        )
    if ats_type == 'eightfold':
        host, _, _domain = board_token.partition('/')
        return f'https://{host}'
    template = _SIMPLE_TEMPLATES.get(ats_type)
    if template is None:
        raise ValueError(f'No board_url template for ats_type={ats_type!r}')
    return template.format(token=board_token)


def _board_token_for(ats_type: str | None, board_url: str) -> str | None:
    if ats_type is None:
        return None
    if ats_type == 'workday':
        netloc = urlsplit(board_url).netloc
        company, instance, _rest = netloc.split('.', 2)
        site = urlsplit(board_url).path.strip('/')
        return f'{company}/{instance}/{site}'
    if ats_type == 'adp':
        query = parse_qs(urlsplit(board_url).query)
        cid = query.get('cid', [''])[0]
        cc_id = query.get('ccId', [''])[0]
        return f'{cid}/{cc_id}'
    if ats_type == 'eightfold':
        host = urlsplit(board_url).netloc
        # Best-effort only: the original "host/domain" token's domain half
        # (Eightfold's resolved tenant identifier) isn't recoverable from
        # board_url alone when it differs from the host itself (e.g.
        # explore.jobs.netflix.net -> domain netflix.com) — this is the one
        # documented lossy spot in this downgrade, matching the accepted
        # per-crawl re-resolution tradeoff this migration's upgrade() makes.
        return f'{host}/{host}'
    if ats_type in _SUBDOMAIN_ATS_TYPES:
        return urlsplit(board_url).netloc.split('.')[0]
    if ats_type == 'workable':
        return urlsplit(board_url).path.strip('/')
    if ats_type in ('greenhouse', 'lever', 'ashby'):
        return urlsplit(board_url).path.strip('/')
    if ats_type in ('amazon', 'google', 'apple'):
        return ats_type
    raise ValueError(f'No board_token extraction for ats_type={ats_type!r}')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('crawl_sources', sa.Column('board_url', sa.Text(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text('SELECT id, ats_type, board_token, detected_domain FROM crawl_sources')).fetchall()

    # The old schema's two disjoint partial unique indexes (ats_type+
    # board_token when known, detected_domain when not) could both hold a
    # row for the same real board — e.g. a "pending, unsupported platform"
    # placeholder from before a submitted-URL-based adapter got auto-
    # registered for that same host. A single board_url index correctly
    # rejects that as a duplicate, so: compute known (ats_type IS NOT NULL)
    # rows first, then drop any unsupported-platform placeholder whose
    # board_url a known row already claims — the placeholder is genuinely
    # superseded, not a data-loss case.
    known = [r for r in rows if r.ats_type is not None]
    unsupported = [r for r in rows if r.ats_type is None]

    claimed_urls: set[str] = set()
    for row in known:
        board_url = _board_url_for(row.ats_type, row.board_token, row.detected_domain)
        claimed_urls.add(board_url)
        conn.execute(
            sa.text('UPDATE crawl_sources SET board_url = :board_url WHERE id = :id'),
            {'board_url': board_url, 'id': row.id},
        )

    for row in unsupported:
        board_url = _board_url_for(row.ats_type, row.board_token, row.detected_domain)
        if board_url in claimed_urls:
            conn.execute(sa.text('DELETE FROM crawl_sources WHERE id = :id'), {'id': row.id})
            continue
        claimed_urls.add(board_url)
        conn.execute(
            sa.text('UPDATE crawl_sources SET board_url = :board_url WHERE id = :id'),
            {'board_url': board_url, 'id': row.id},
        )

    op.alter_column('crawl_sources', 'board_url', nullable=False)
    op.drop_index(op.f('uq_crawl_sources_ats_type_board_token'), table_name='crawl_sources', postgresql_where='(ats_type IS NOT NULL)')
    op.drop_index(op.f('uq_crawl_sources_detected_domain'), table_name='crawl_sources', postgresql_where='(ats_type IS NULL)')
    op.create_index('uq_crawl_sources_board_url', 'crawl_sources', ['board_url'], unique=True)
    op.drop_column('crawl_sources', 'detected_domain')
    op.drop_column('crawl_sources', 'board_token')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('crawl_sources', sa.Column('board_token', sa.VARCHAR(length=255), autoincrement=False, nullable=True))
    op.add_column('crawl_sources', sa.Column('detected_domain', sa.VARCHAR(length=255), autoincrement=False, nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text('SELECT id, ats_type, board_url FROM crawl_sources')).fetchall()
    for row in rows:
        if row.ats_type is None:
            conn.execute(
                sa.text('UPDATE crawl_sources SET detected_domain = :domain WHERE id = :id'),
                {'domain': urlsplit(row.board_url).netloc, 'id': row.id},
            )
        else:
            conn.execute(
                sa.text('UPDATE crawl_sources SET board_token = :token WHERE id = :id'),
                {'token': _board_token_for(row.ats_type, row.board_url), 'id': row.id},
            )

    op.drop_index('uq_crawl_sources_board_url', table_name='crawl_sources')
    op.create_index(op.f('uq_crawl_sources_detected_domain'), 'crawl_sources', ['detected_domain'], unique=True, postgresql_where='(ats_type IS NULL)')
    op.create_index(op.f('uq_crawl_sources_ats_type_board_token'), 'crawl_sources', ['ats_type', 'board_token'], unique=True, postgresql_where='(ats_type IS NOT NULL)')
    op.drop_column('crawl_sources', 'board_url')
