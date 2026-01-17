"""Add issue_date_estimated flag to debt_instruments.

Indicates whether issue_date was extracted from filing or estimated
based on maturity date and typical instrument tenors.

Revision ID: 008_add_issue_date_estimated
Revises: 007_make_cusip_nullable
Create Date: 2026-01-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '008_add_issue_date_estimated'
down_revision: Union[str, None] = '007_make_cusip_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add issue_date_estimated column with default False
    op.add_column('debt_instruments',
                  sa.Column('issue_date_estimated', sa.Boolean(),
                           nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('debt_instruments', 'issue_date_estimated')
