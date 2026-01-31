"""Expand threshold_value precision for large dollar amounts

Some covenants have thresholds in billions of dollars (e.g., $6.125B).
The original Numeric(10,4) only handles up to ~999,999.9999.
Expanding to Numeric(18,4) handles up to ~9 trillion.

Revision ID: 021
Revises: 020
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '021'
down_revision = '020'
branch_labels = None
depends_on = None


def upgrade():
    # Expand threshold_value from Numeric(10,4) to Numeric(18,4)
    op.alter_column(
        'covenants',
        'threshold_value',
        type_=sa.Numeric(18, 4),
        existing_type=sa.Numeric(10, 4),
        existing_nullable=True
    )


def downgrade():
    # Revert to Numeric(10,4) - may fail if data exceeds precision
    op.alter_column(
        'covenants',
        'threshold_value',
        type_=sa.Numeric(10, 4),
        existing_type=sa.Numeric(18, 4),
        existing_nullable=True
    )
