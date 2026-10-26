"""Job collections: the collection registry and its status history

Revision ID: d4d22d3b871c
Revises: e5b2a8c4d1f7
Create Date: 2026-10-26 00:00:00.000000

Two new tables, nothing else touched (see app.models.job_collection).
Nothing reads them until settings.job_collections_enabled is on.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4d22d3b871c'
down_revision: Union[str, Sequence[str], None] = 'e5b2a8c4d1f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job_collections',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('collection_type', sa.String(length=30), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=True),
        sa.Column('role', sa.String(length=80), nullable=True),
        sa.Column('location', sa.String(length=80), nullable=True),
        sa.Column('salary', sa.String(length=20), nullable=True),
        sa.Column('freshness', sa.String(length=30), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('h1', sa.String(length=200), nullable=True),
        sa.Column('meta_description', sa.String(length=320), nullable=True),
        sa.Column('qualification_score', sa.Float(), nullable=True),
        sa.Column('qualification', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index('ix_job_collections_collection_type', 'job_collections', ['collection_type'])
    op.create_index('ix_job_collections_status', 'job_collections', ['status'])

    op.create_table(
        'job_collection_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('collection_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('previous_status', sa.String(length=20), nullable=True),
        sa.Column('new_status', sa.String(length=20), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('actor_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['collection_id'], ['job_collections.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_job_collection_events_collection_id_created_at', 'job_collection_events', ['collection_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_job_collection_events_collection_id_created_at', table_name='job_collection_events')
    op.drop_table('job_collection_events')
    op.drop_index('ix_job_collections_status', table_name='job_collections')
    op.drop_index('ix_job_collections_collection_type', table_name='job_collections')
    op.drop_table('job_collections')
