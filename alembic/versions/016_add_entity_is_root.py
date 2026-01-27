"""Add is_root column to entities table

Revision ID: 016
Revises: 015
Create Date: 2026-01-25

Distinguishes root entities (ultimate parent company) from orphan entities
(where parent is unknown). Both have parent_id = NULL, but:
- is_root = true: This is the ultimate parent company
- is_root = false/null + parent_id = NULL: Parent is unknown (orphan)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_root column
    op.add_column('entities', sa.Column('is_root', sa.Boolean(), nullable=True))

    # Set is_root = true for entities with structure_tier = 1 and parent_id is NULL
    op.execute("""
        UPDATE entities
        SET is_root = true
        WHERE structure_tier = 1 AND parent_id IS NULL
    """)

    # Set is_root = false for all other entities
    op.execute("""
        UPDATE entities
        SET is_root = false
        WHERE is_root IS NULL
    """)

    # Create index for quick root lookups
    op.create_index('ix_entities_is_root', 'entities', ['is_root'], postgresql_where=sa.text('is_root = true'))


def downgrade():
    op.drop_index('ix_entities_is_root', table_name='entities')
    op.drop_column('entities', 'is_root')
