"""Add ownership_links table for complex corporate structures

Revision ID: 002_ownership_links
Revises: 001_initial_schema
Create Date: 2026-01-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002_ownership_links"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ownership_links - for complex ownership structures
    op.create_table(
        "ownership_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ownership_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "ownership_type",
            sa.String(50),
            nullable=True,
        ),  # direct, indirect, economic_only, voting_only
        sa.Column("is_joint_venture", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("jv_partner_name", sa.String(255), nullable=True),
        sa.Column(
            "consolidation_method",
            sa.String(50),
            nullable=True,
        ),  # full, equity_method, proportional, vie
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),  # NULL if current
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_entity_id"], ["entities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["child_entity_id"], ["entities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_entity_id",
            "child_entity_id",
            "effective_from",
            name="uq_ownership_parent_child_date",
        ),
    )
    op.create_index("idx_ownership_parent", "ownership_links", ["parent_entity_id"])
    op.create_index("idx_ownership_child", "ownership_links", ["child_entity_id"])
    op.create_index(
        "idx_ownership_jv",
        "ownership_links",
        ["is_joint_venture"],
        postgresql_where=sa.text("is_joint_venture = true"),
    )
    op.create_index(
        "idx_ownership_active",
        "ownership_links",
        ["child_entity_id"],
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    # Add VIE-related columns to entities
    op.add_column(
        "entities",
        sa.Column("is_vie", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "entities",
        sa.Column("vie_primary_beneficiary", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "entities",
        sa.Column(
            "consolidation_method",
            sa.String(50),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("entities", "consolidation_method")
    op.drop_column("entities", "vie_primary_beneficiary")
    op.drop_column("entities", "is_vie")
    op.drop_table("ownership_links")
