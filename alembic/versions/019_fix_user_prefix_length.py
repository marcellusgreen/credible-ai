"""Fix api_key_prefix column length

The api_key_prefix column was VARCHAR(8) but the code generates prefixes
like "ds_390f1eb6" which are 11 characters. Increase to VARCHAR(16).

Revision ID: 019
Revises: 018
Create Date: 2026-01-29
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '019'
down_revision = '018'
branch_labels = None
depends_on = None


def upgrade():
    # Increase api_key_prefix column size from VARCHAR(8) to VARCHAR(16)
    op.alter_column(
        'users',
        'api_key_prefix',
        type_=sa.String(16),
        existing_type=sa.String(8),
        existing_nullable=False
    )


def downgrade():
    # Revert to VARCHAR(8) - may truncate existing data
    op.alter_column(
        'users',
        'api_key_prefix',
        type_=sa.String(8),
        existing_type=sa.String(16),
        existing_nullable=False
    )
