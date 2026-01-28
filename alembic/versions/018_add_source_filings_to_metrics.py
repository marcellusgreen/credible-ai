"""Add source_filings column to company_metrics table

Revision ID: 018
Revises: 017
Create Date: 2026-01-28

Tracks provenance of computed metrics - which SEC filings were used for TTM calculations.
Format: {
    "debt_source": "balance_sheet|instruments",
    "debt_filing": "https://sec.gov/...",
    "ttm_quarters": ["2025Q1", "2025Q2", "2025Q3", "2024Q4"],
    "ttm_filings": ["https://...", ...],
    "computed_at": "2026-01-28T12:00:00Z"
}
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = '018'
down_revision = '017'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('company_metrics', sa.Column('source_filings', JSONB, nullable=True))


def downgrade():
    op.drop_column('company_metrics', 'source_filings')
