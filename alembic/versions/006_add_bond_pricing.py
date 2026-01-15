"""Add bond_pricing table for FINRA TRACE pricing data

Revision ID: 006_add_bond_pricing
Revises: 005_add_obligor_group_financials
Create Date: 2026-01-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "006_add_bond_pricing"
down_revision: Union[str, None] = "005_add_obligor_group_financials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create bond_pricing table for FINRA TRACE data
    op.create_table(
        "bond_pricing",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "debt_instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("debt_instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cusip", sa.String(9), nullable=False),
        # Pricing
        sa.Column("last_price", sa.Numeric(8, 4)),  # Clean price as % of par
        sa.Column("last_trade_date", sa.DateTime(timezone=True)),
        sa.Column("last_trade_volume", sa.BigInteger),  # Face value in cents
        # Yields (in basis points)
        sa.Column("ytm_bps", sa.Integer),  # Yield to maturity
        sa.Column("spread_to_treasury_bps", sa.Integer),  # Spread over benchmark
        sa.Column("treasury_benchmark", sa.String(10)),  # "2Y", "5Y", "10Y", "30Y"
        # Quality indicators
        sa.Column("price_source", sa.String(20), server_default="TRACE"),
        sa.Column("staleness_days", sa.Integer),
        # Timestamps
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("calculated_at", sa.DateTime(timezone=True)),
    )

    # Add indices for common queries
    op.create_index(
        "idx_bond_pricing_debt",
        "bond_pricing",
        ["debt_instrument_id"],
    )
    op.create_index(
        "idx_bond_pricing_cusip",
        "bond_pricing",
        ["cusip"],
    )
    op.create_index(
        "idx_bond_pricing_staleness",
        "bond_pricing",
        ["staleness_days"],
    )


def downgrade() -> None:
    op.drop_index("idx_bond_pricing_staleness", table_name="bond_pricing")
    op.drop_index("idx_bond_pricing_cusip", table_name="bond_pricing")
    op.drop_index("idx_bond_pricing_debt", table_name="bond_pricing")
    op.drop_table("bond_pricing")
