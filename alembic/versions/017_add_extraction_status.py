"""Add extraction_status column to company_cache table

Revision ID: 017
Revises: 016
Create Date: 2026-01-26

Tracks extraction step status for idempotent re-runs.
Format: {"step_name": {"status": "success|no_data|error", "attempted_at": "ISO timestamp", "details": "..."}}
Steps: core, document_sections, financials, hierarchy, guarantees, collateral
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('company_cache', sa.Column('extraction_status', JSONB, nullable=True))


def downgrade():
    op.drop_column('company_cache', 'extraction_status')
