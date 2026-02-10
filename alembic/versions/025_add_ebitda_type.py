"""Add ebitda_type column to distinguish EBITDA vs PPNR for banks

Revision ID: 025_add_ebitda_type
Revises: 024_add_bank_financials_support
Create Date: 2026-02-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '025_add_ebitda_type'
down_revision = '024_add_bank_financials_support'
branch_labels = None
depends_on = None


def upgrade():
    # Add ebitda_type column to distinguish EBITDA from PPNR (for banks)
    # Values: "ebitda" (traditional), "ppnr" (Pre-Provision Net Revenue for banks)
    op.add_column('company_financials', sa.Column(
        'ebitda_type',
        sa.String(20),
        nullable=True
    ))

    # Set existing records: banks get "ppnr", others get "ebitda"
    op.execute("""
        UPDATE company_financials cf
        SET ebitda_type = CASE
            WHEN c.is_financial_institution = true THEN 'ppnr'
            ELSE 'ebitda'
        END
        FROM companies c
        WHERE cf.company_id = c.id
        AND cf.ebitda IS NOT NULL
    """)


def downgrade():
    op.drop_column('company_financials', 'ebitda_type')
