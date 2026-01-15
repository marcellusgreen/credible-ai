"""Expand benchmark and rate_type columns

Revision ID: 003_expand_benchmark_column
Revises: 002_ownership_links
Create Date: 2026-01-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003_expand_benchmark_column"
down_revision: Union[str, None] = "002_ownership_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand benchmark column from 20 to 50 characters
    # to handle full names like "Secured Overnight Financing Rate"
    op.alter_column(
        "debt_instruments",
        "benchmark",
        type_=sa.String(50),
        existing_type=sa.String(20),
        existing_nullable=True,
    )

    # Expand rate_type column from 20 to 30 characters
    # to handle values like "unspecified"
    op.alter_column(
        "debt_instruments",
        "rate_type",
        type_=sa.String(30),
        existing_type=sa.String(20),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "debt_instruments",
        "rate_type",
        type_=sa.String(20),
        existing_type=sa.String(30),
        existing_nullable=True,
    )
    op.alter_column(
        "debt_instruments",
        "benchmark",
        type_=sa.String(20),
        existing_type=sa.String(50),
        existing_nullable=True,
    )
