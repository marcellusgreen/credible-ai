"""add guarantee_data_confidence to debt_instruments

Revision ID: ff16d034683e
Revises: 011_add_company_snapshots
Create Date: 2026-01-21 14:56:03.104588

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ff16d034683e'
down_revision: Union[str, None] = '011_add_company_snapshots'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add guarantee_data_confidence column
    op.add_column(
        'debt_instruments',
        sa.Column('guarantee_data_confidence', sa.String(20), nullable=True, server_default='unknown')
    )


def downgrade() -> None:
    op.drop_column('debt_instruments', 'guarantee_data_confidence')
