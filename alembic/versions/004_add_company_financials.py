"""Add company_financials table for quarterly financial data

Revision ID: 004_add_company_financials
Revises: 003_expand_benchmark_column
Create Date: 2026-01-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004_add_company_financials"
down_revision: Union[str, None] = "003_expand_benchmark_column"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create company_financials table for quarterly financial data
    op.create_table(
        "company_financials",
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
        # Income statement (all in cents)
        sa.Column("revenue", sa.BigInteger),
        sa.Column("cost_of_revenue", sa.BigInteger),
        sa.Column("gross_profit", sa.BigInteger),
        sa.Column("operating_income", sa.BigInteger),
        sa.Column("ebitda", sa.BigInteger),
        sa.Column("interest_expense", sa.BigInteger),
        sa.Column("net_income", sa.BigInteger),
        sa.Column("depreciation_amortization", sa.BigInteger),
        # Balance sheet (all in cents)
        sa.Column("cash_and_equivalents", sa.BigInteger),
        sa.Column("total_current_assets", sa.BigInteger),
        sa.Column("total_assets", sa.BigInteger),
        sa.Column("total_current_liabilities", sa.BigInteger),
        sa.Column("total_debt", sa.BigInteger),
        sa.Column("total_liabilities", sa.BigInteger),
        sa.Column("stockholders_equity", sa.BigInteger),
        # Cash flow (all in cents)
        sa.Column("operating_cash_flow", sa.BigInteger),
        sa.Column("investing_cash_flow", sa.BigInteger),
        sa.Column("financing_cash_flow", sa.BigInteger),
        sa.Column("capex", sa.BigInteger),
        # Metadata
        sa.Column("source_filing", sa.String(500)),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Add unique constraint for company + fiscal period
    op.create_unique_constraint(
        "uq_financials_period",
        "company_financials",
        ["company_id", "fiscal_year", "fiscal_quarter"],
    )

    # Add indices for common queries
    op.create_index(
        "idx_financials_company",
        "company_financials",
        ["company_id"],
    )
    op.create_index(
        "idx_financials_period",
        "company_financials",
        ["period_end_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_financials_period", table_name="company_financials")
    op.drop_index("idx_financials_company", table_name="company_financials")
    op.drop_constraint("uq_financials_period", "company_financials", type_="unique")
    op.drop_table("company_financials")
