"""Add collateral table for tracking debt instrument collateral

Revision ID: 012
Revises: ff16d034683e
Create Date: 2026-01-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '012'
down_revision = 'ff16d034683e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create collateral table
    op.create_table(
        'collateral',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('debt_instrument_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('debt_instruments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('collateral_type', sa.String(50), nullable=False),  # real_estate, equipment, receivables, inventory, securities, vehicles, ip, cash, general_lien
        sa.Column('description', sa.Text, nullable=True),  # Free text description of the collateral
        sa.Column('estimated_value', sa.BigInteger, nullable=True),  # Value in cents if disclosed
        sa.Column('priority', sa.String(20), nullable=True),  # first_lien, second_lien, etc.
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
    )

    # Create indexes
    op.create_index('ix_collateral_debt_instrument_id', 'collateral', ['debt_instrument_id'])
    op.create_index('ix_collateral_collateral_type', 'collateral', ['collateral_type'])

    # Add collateral_data_confidence field to debt_instruments (similar to guarantee_data_confidence)
    op.add_column('debt_instruments', sa.Column('collateral_data_confidence', sa.String(20), nullable=True, server_default='unknown'))


def downgrade() -> None:
    op.drop_column('debt_instruments', 'collateral_data_confidence')
    op.drop_index('ix_collateral_collateral_type', table_name='collateral')
    op.drop_index('ix_collateral_debt_instrument_id', table_name='collateral')
    op.drop_table('collateral')
