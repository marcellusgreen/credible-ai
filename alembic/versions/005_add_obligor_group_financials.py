"""Add obligor_group_financials table for SEC Rule 13-01 data

Revision ID: 005_add_obligor_group_financials
Revises: 004_add_company_financials
Create Date: 2026-01-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005_add_obligor_group_financials"
down_revision: Union[str, None] = "004_add_company_financials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create obligor_group_financials table for SEC Rule 13-01 data
    op.create_table(
        "obligor_group_financials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Period info
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("fiscal_quarter", sa.Integer, nullable=False),
        sa.Column("period_end_date", sa.Date, nullable=False),
        sa.Column("filing_type", sa.String(10)),
        # Disclosure metadata
        sa.Column("disclosure_note_number", sa.String(50)),
        sa.Column("debt_description", sa.Text),
        sa.Column("related_debt_ids", postgresql.JSONB),
        # Obligor Group Balance Sheet (all in cents)
        sa.Column("og_total_assets", sa.BigInteger),
        sa.Column("og_total_liabilities", sa.BigInteger),
        sa.Column("og_stockholders_equity", sa.BigInteger),
        sa.Column("og_intercompany_receivables", sa.BigInteger),
        # Obligor Group Income Statement (all in cents)
        sa.Column("og_revenue", sa.BigInteger),
        sa.Column("og_operating_income", sa.BigInteger),
        sa.Column("og_ebitda", sa.BigInteger),
        sa.Column("og_net_income", sa.BigInteger),
        # Consolidated totals (for leakage calculation)
        sa.Column("consolidated_total_assets", sa.BigInteger),
        sa.Column("consolidated_revenue", sa.BigInteger),
        sa.Column("consolidated_ebitda", sa.BigInteger),
        # Non-guarantor subsidiaries (if disclosed)
        sa.Column("non_guarantor_assets", sa.BigInteger),
        sa.Column("non_guarantor_revenue", sa.BigInteger),
        # Computed leakage metrics
        sa.Column("asset_leakage_pct", sa.Numeric(5, 2)),
        sa.Column("revenue_leakage_pct", sa.Numeric(5, 2)),
        sa.Column("ebitda_leakage_pct", sa.Numeric(5, 2)),
        # Metadata
        sa.Column("source_filing", sa.String(500)),
        sa.Column("uncertainties", postgresql.JSONB),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Add unique constraint for company + fiscal period
    op.create_unique_constraint(
        "uq_obligor_group_period",
        "obligor_group_financials",
        ["company_id", "fiscal_year", "fiscal_quarter"],
    )

    # Add indices for common queries
    op.create_index(
        "idx_obligor_group_company",
        "obligor_group_financials",
        ["company_id"],
    )
    op.create_index(
        "idx_obligor_group_leakage",
        "obligor_group_financials",
        ["asset_leakage_pct"],
    )


def downgrade() -> None:
    op.drop_index("idx_obligor_group_leakage", table_name="obligor_group_financials")
    op.drop_index("idx_obligor_group_company", table_name="obligor_group_financials")
    op.drop_constraint("uq_obligor_group_period", "obligor_group_financials", type_="unique")
    op.drop_table("obligor_group_financials")
