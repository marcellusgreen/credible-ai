"""Add covenants table for structured covenant data

Creates a new table to store extracted covenant information from
credit agreements and indentures. Includes support for:
- Financial covenants (leverage, coverage ratios with thresholds)
- Negative covenants (liens, debt, payments restrictions)
- Incurrence tests
- Protective covenants (change of control, make-whole)

Revision ID: 020
Revises: 019
Create Date: 2026-01-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers
revision = '020'
down_revision = '019'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'covenants',
        # Primary key
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),

        # Foreign keys
        sa.Column('debt_instrument_id', UUID(as_uuid=True),
                  sa.ForeignKey('debt_instruments.id', ondelete='CASCADE'), nullable=True),
        sa.Column('company_id', UUID(as_uuid=True),
                  sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_document_id', UUID(as_uuid=True),
                  sa.ForeignKey('document_sections.id', ondelete='SET NULL'), nullable=True),

        # Covenant identification
        sa.Column('covenant_type', sa.String(50), nullable=False),  # financial, negative, incurrence, protective
        sa.Column('covenant_name', sa.String(200), nullable=False),  # e.g., 'Maximum Leverage Ratio'

        # Financial covenant specifics
        sa.Column('test_metric', sa.String(50), nullable=True),  # leverage_ratio, interest_coverage, etc.
        sa.Column('threshold_value', sa.Numeric(10, 4), nullable=True),  # e.g., 4.50
        sa.Column('threshold_type', sa.String(20), nullable=True),  # maximum, minimum
        sa.Column('test_frequency', sa.String(20), nullable=True),  # quarterly, annual, incurrence

        # Covenant details
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('has_step_down', sa.Boolean, default=False, nullable=False),
        sa.Column('step_down_schedule', JSONB, nullable=True),
        sa.Column('cure_period_days', sa.Integer, nullable=True),

        # Change of control specifics
        sa.Column('put_price_pct', sa.Numeric(5, 2), nullable=True),  # e.g., 101.00

        # Extraction metadata
        sa.Column('extraction_confidence', sa.Numeric(3, 2), nullable=True),  # 0.00-1.00
        sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_text', sa.Text, nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
    )

    # Create indexes
    op.create_index('idx_covenants_company', 'covenants', ['company_id'])
    op.create_index('idx_covenants_instrument', 'covenants', ['debt_instrument_id'])
    op.create_index('idx_covenants_type', 'covenants', ['covenant_type'])
    op.create_index('idx_covenants_name', 'covenants', ['covenant_name'])
    op.create_index('idx_covenants_metric', 'covenants', ['test_metric'])
    op.create_index('idx_covenants_company_type', 'covenants', ['company_id', 'covenant_type'])


def downgrade():
    op.drop_index('idx_covenants_company_type', 'covenants')
    op.drop_index('idx_covenants_metric', 'covenants')
    op.drop_index('idx_covenants_name', 'covenants')
    op.drop_index('idx_covenants_type', 'covenants')
    op.drop_index('idx_covenants_instrument', 'covenants')
    op.drop_index('idx_covenants_company', 'covenants')
    op.drop_table('covenants')
