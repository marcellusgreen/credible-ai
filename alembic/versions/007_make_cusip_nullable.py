"""Make cusip nullable in bond_pricing table.

Allow estimated pricing for bonds without CUSIPs.

Revision ID: 007_make_cusip_nullable
Revises: 006_add_bond_pricing
Create Date: 2026-01-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '007_make_cusip_nullable'
down_revision: Union[str, None] = '006_add_bond_pricing'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make cusip nullable to support estimated pricing without CUSIPs
    op.alter_column('bond_pricing', 'cusip',
                    existing_type=sa.String(9),
                    nullable=True)


def downgrade() -> None:
    # Make cusip required again (this will fail if there are NULL values)
    op.alter_column('bond_pricing', 'cusip',
                    existing_type=sa.String(9),
                    nullable=False)
