"""Add company_snapshots table for historical tracking and diff/changelog.

Stores point-in-time snapshots of company data for comparison.
Enables the future /v1/companies/{ticker}/changes endpoint.

Revision ID: 011_add_company_snapshots
Revises: 010_add_extraction_metadata
Create Date: 2026-01-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "011_add_company_snapshots"
down_revision: Union[str, None] = "010_add_extraction_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create company_snapshots table
    op.create_table(
        "company_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(20), nullable=False),

        # Snapshot metadata
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("snapshot_type", sa.String(20), nullable=False),  # 'quarterly', 'monthly', 'manual'

        # Denormalized JSON snapshots for fast comparison
        sa.Column("entities_snapshot", postgresql.JSONB),  # All entities at point in time
        sa.Column("debt_snapshot", postgresql.JSONB),  # All debt instruments
        sa.Column("metrics_snapshot", postgresql.JSONB),  # CompanyMetrics at point in time
        sa.Column("financials_snapshot", postgresql.JSONB),  # Latest financials

        # Summary counts for quick comparison
        sa.Column("entity_count", sa.Integer),
        sa.Column("debt_instrument_count", sa.Integer),
        sa.Column("total_debt", sa.BigInteger),  # In cents
        sa.Column("guarantor_count", sa.Integer),

        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Unique constraint: one snapshot per company per date
    op.create_unique_constraint(
        "uq_company_snapshots_company_date",
        "company_snapshots",
        ["company_id", "snapshot_date"],
    )

    # Index for company lookups
    op.create_index(
        "idx_company_snapshots_company",
        "company_snapshots",
        ["company_id"],
    )

    # Index for date-based queries
    op.create_index(
        "idx_company_snapshots_date",
        "company_snapshots",
        ["snapshot_date"],
    )

    # Composite index for company + date range queries
    op.create_index(
        "idx_company_snapshots_company_date",
        "company_snapshots",
        ["company_id", "snapshot_date"],
    )

    # Index for ticker lookups
    op.create_index(
        "idx_company_snapshots_ticker",
        "company_snapshots",
        ["ticker"],
    )


def downgrade() -> None:
    op.drop_index("idx_company_snapshots_ticker", table_name="company_snapshots")
    op.drop_index("idx_company_snapshots_company_date", table_name="company_snapshots")
    op.drop_index("idx_company_snapshots_date", table_name="company_snapshots")
    op.drop_index("idx_company_snapshots_company", table_name="company_snapshots")
    op.drop_constraint("uq_company_snapshots_company_date", "company_snapshots", type_="unique")
    op.drop_table("company_snapshots")
