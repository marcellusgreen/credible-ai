"""Add treasury_yield_history table

Revision ID: 023_add_treasury_yield_history
Revises: 022_three_tier_pricing
Create Date: 2026-02-02

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '023_add_treasury_yield_history'
down_revision = '022_three_tier_pricing'
branch_labels = None
depends_on = None


def upgrade():
    # Create treasury_yield_history table
    op.create_table(
        'treasury_yield_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('yield_date', sa.Date(), nullable=False),
        sa.Column('benchmark', sa.String(5), nullable=False),
        sa.Column('yield_pct', sa.Numeric(6, 4), nullable=False),
        sa.Column('source', sa.String(20), server_default='treasury.gov', nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('yield_date', 'benchmark', name='uq_treasury_yield_date_benchmark'),
    )

    # Create indexes
    op.create_index('idx_treasury_yield_date', 'treasury_yield_history', ['yield_date'])
    op.create_index('idx_treasury_yield_benchmark', 'treasury_yield_history', ['benchmark'])


def downgrade():
    op.drop_index('idx_treasury_yield_benchmark', table_name='treasury_yield_history')
    op.drop_index('idx_treasury_yield_date', table_name='treasury_yield_history')
    op.drop_table('treasury_yield_history')
