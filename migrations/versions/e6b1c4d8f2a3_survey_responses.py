"""Survey responses

Revision ID: e6b1c4d8f2a3
Revises: d5a9e3c7b2f1
Create Date: 2026-10-31 00:00:00.000000

A new table (app.models.survey_response); nothing existing changes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e6b1c4d8f2a3'
down_revision: Union[str, Sequence[str], None] = 'd5a9e3c7b2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'survey_responses',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('visitor_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('search_duration', sa.String(length=20), nullable=True),
        sa.Column('industries', sa.JSON(), nullable=True),
        sa.Column('industry_other', sa.String(length=120), nullable=True),
        sa.Column('role_name', sa.String(length=120), nullable=True),
        sa.Column('work_arrangement', sa.JSON(), nullable=True),
        sa.Column('current_tools', sa.JSON(), nullable=True),
        sa.Column('frustration', sa.Text(), nullable=True),
        sa.Column('posted_date_rating', sa.String(length=20), nullable=True),
        sa.Column('first_found_date_rating', sa.String(length=20), nullable=True),
        sa.Column('verified_date_rating', sa.String(length=20), nullable=True),
        sa.Column('dedup_rating', sa.String(length=20), nullable=True),
        sa.Column('liked', sa.JSON(), nullable=True),
        sa.Column('wanted', sa.JSON(), nullable=True),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('contact_email', sa.String(length=320), nullable=True),
        sa.Column('contact_ok', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('completed', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_survey_responses_visitor_id'), 'survey_responses', ['visitor_id'], unique=True)
    op.create_index(op.f('ix_survey_responses_user_id'), 'survey_responses', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_survey_responses_user_id'), table_name='survey_responses')
    op.drop_index(op.f('ix_survey_responses_visitor_id'), table_name='survey_responses')
    op.drop_table('survey_responses')
