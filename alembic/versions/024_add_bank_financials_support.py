"""Add bank/financial institution support

Adds:
- is_financial_institution flag on companies table
- net_interest_income, non_interest_income, provision_for_credit_losses on company_financials

Revision ID: 024_add_bank_financials_support
Revises: 023_add_treasury_yield_history
Create Date: 2026-02-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '024_add_bank_financials_support'
down_revision = '023_add_treasury_yield_history'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_financial_institution flag to companies table
    op.add_column('companies', sa.Column(
        'is_financial_institution',
        sa.Boolean(),
        server_default='false',
        nullable=False
    ))

    # Add bank-specific income statement fields to company_financials
    # Net Interest Income = Interest Income - Interest Expense (for banks, this IS their "revenue")
    op.add_column('company_financials', sa.Column(
        'net_interest_income',
        sa.BigInteger(),
        nullable=True
    ))

    # Non-Interest Income = Fees, trading gains, etc. (secondary revenue for banks)
    op.add_column('company_financials', sa.Column(
        'non_interest_income',
        sa.BigInteger(),
        nullable=True
    ))

    # Non-Interest Expense = Operating costs (salaries, occupancy, etc.)
    op.add_column('company_financials', sa.Column(
        'non_interest_expense',
        sa.BigInteger(),
        nullable=True
    ))

    # Provision for Credit Losses = Bank's expense for expected loan defaults
    op.add_column('company_financials', sa.Column(
        'provision_for_credit_losses',
        sa.BigInteger(),
        nullable=True
    ))

    # Create index for financial institution lookup
    op.create_index(
        'idx_companies_is_financial_institution',
        'companies',
        ['is_financial_institution'],
        postgresql_where=sa.text('is_financial_institution = true')
    )


def downgrade():
    op.drop_index('idx_companies_is_financial_institution', table_name='companies')
    op.drop_column('company_financials', 'provision_for_credit_losses')
    op.drop_column('company_financials', 'non_interest_expense')
    op.drop_column('company_financials', 'non_interest_income')
    op.drop_column('company_financials', 'net_interest_income')
    op.drop_column('companies', 'is_financial_institution')
