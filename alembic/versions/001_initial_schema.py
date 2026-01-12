"""Initial schema - core tables and denormalized cache tables

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-01-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================================================
    # CORE TABLES
    # ==========================================================================

    # companies
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("cik", sa.String(20), nullable=True),
        sa.Column("lei", sa.String(20), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker"),
    )
    op.create_index("idx_companies_ticker", "companies", ["ticker"])
    op.create_index("idx_companies_cik", "companies", ["cik"])
    op.create_index("idx_companies_sector", "companies", ["sector"])

    # entities
    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(255), nullable=True),
        sa.Column("legal_name", sa.String(500), nullable=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("jurisdiction", sa.String(100), nullable=True),
        sa.Column("formation_type", sa.String(50), nullable=True),
        sa.Column("formation_date", sa.Date(), nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ownership_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("structure_tier", sa.Integer(), nullable=True),
        sa.Column("is_guarantor", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_borrower", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_restricted", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_unrestricted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_material", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_domestic", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_dormant", sa.Boolean(), nullable=False, server_default="false"),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "slug", name="uq_entities_company_slug"),
    )
    op.create_index("idx_entities_company", "entities", ["company_id"])
    op.create_index("idx_entities_parent", "entities", ["parent_id"])
    op.create_index("idx_entities_type", "entities", ["company_id", "entity_type"])
    op.create_index(
        "idx_entities_guarantor",
        "entities",
        ["company_id", "is_guarantor"],
        postgresql_where=sa.text("is_guarantor = true"),
    )
    op.create_index(
        "idx_entities_unrestricted",
        "entities",
        ["company_id", "is_unrestricted"],
        postgresql_where=sa.text("is_unrestricted = true"),
    )

    # debt_instruments
    op.create_table(
        "debt_instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(255), nullable=True),
        sa.Column("cusip", sa.String(9), nullable=True),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("instrument_type", sa.String(50), nullable=False),
        sa.Column("seniority", sa.String(50), nullable=False),
        sa.Column("security_type", sa.String(50), nullable=True),
        sa.Column("commitment", sa.BigInteger(), nullable=True),
        sa.Column("principal", sa.BigInteger(), nullable=True),
        sa.Column("outstanding", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("rate_type", sa.String(20), nullable=True),
        sa.Column("interest_rate", sa.Integer(), nullable=True),
        sa.Column("spread_bps", sa.Integer(), nullable=True),
        sa.Column("benchmark", sa.String(20), nullable=True),
        sa.Column("floor_bps", sa.Integer(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("is_drawn", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["issuer_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "slug", name="uq_debt_company_slug"),
    )
    op.create_index("idx_debt_company", "debt_instruments", ["company_id"])
    op.create_index("idx_debt_issuer", "debt_instruments", ["issuer_id"])
    op.create_index("idx_debt_maturity", "debt_instruments", ["maturity_date"])
    op.create_index("idx_debt_type", "debt_instruments", ["instrument_type"])
    op.create_index("idx_debt_seniority", "debt_instruments", ["company_id", "seniority"])
    op.create_index(
        "idx_debt_active",
        "debt_instruments",
        ["company_id", "is_active"],
        postgresql_where=sa.text("is_active = true"),
    )

    # guarantees
    op.create_table(
        "guarantees",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("debt_instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guarantor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "guarantee_type", sa.String(50), nullable=False, server_default="full"
        ),
        sa.Column("limitation_amount", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["debt_instrument_id"], ["debt_instruments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["guarantor_id"], ["entities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "debt_instrument_id", "guarantor_id", name="uq_guarantees_debt_guarantor"
        ),
    )
    op.create_index("idx_guarantees_debt", "guarantees", ["debt_instrument_id"])
    op.create_index("idx_guarantees_guarantor", "guarantees", ["guarantor_id"])

    # ==========================================================================
    # DENORMALIZED TABLES (Read Path)
    # ==========================================================================

    # company_cache
    op.create_table(
        "company_cache",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("response_company", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_structure", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_debt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_subordination", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_guarantors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_waterfall", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("etag", sa.String(32), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_filing_date", sa.Date(), nullable=True),
        sa.Column("total_debt", sa.BigInteger(), nullable=True),
        sa.Column("entity_count", sa.Integer(), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("company_id"),
        sa.UniqueConstraint("ticker"),
    )
    op.create_index("idx_cache_ticker", "company_cache", ["ticker"])

    # company_metrics
    op.create_table(
        "company_metrics",
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("market_cap_bucket", sa.String(20), nullable=True),
        sa.Column("total_debt", sa.BigInteger(), nullable=True),
        sa.Column("secured_debt", sa.BigInteger(), nullable=True),
        sa.Column("unsecured_debt", sa.BigInteger(), nullable=True),
        sa.Column("net_debt", sa.BigInteger(), nullable=True),
        sa.Column("leverage_ratio", sa.Numeric(6, 2), nullable=True),
        sa.Column("net_leverage_ratio", sa.Numeric(6, 2), nullable=True),
        sa.Column("interest_coverage", sa.Numeric(6, 2), nullable=True),
        sa.Column("secured_leverage", sa.Numeric(6, 2), nullable=True),
        sa.Column("debt_due_1yr", sa.BigInteger(), nullable=True),
        sa.Column("debt_due_2yr", sa.BigInteger(), nullable=True),
        sa.Column("debt_due_3yr", sa.BigInteger(), nullable=True),
        sa.Column("nearest_maturity", sa.Date(), nullable=True),
        sa.Column("weighted_avg_maturity", sa.Numeric(4, 1), nullable=True),
        sa.Column("entity_count", sa.Integer(), nullable=True),
        sa.Column("guarantor_count", sa.Integer(), nullable=True),
        sa.Column("subordination_risk", sa.String(20), nullable=True),
        sa.Column("subordination_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("maturity_wall_risk", sa.String(20), nullable=True),
        sa.Column("has_holdco_debt", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_opco_debt", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_structural_sub", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_unrestricted_subs", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_intercreditor", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_foreign_carveout", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_covenant_lite", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_floating_rate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_pik", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_leveraged_loan", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_near_term_maturity", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sp_rating", sa.String(10), nullable=True),
        sa.Column("moodys_rating", sa.String(10), nullable=True),
        sa.Column("rating_bucket", sa.String(20), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ticker"),
    )
    op.create_index("idx_metrics_sector", "company_metrics", ["sector"])
    op.create_index("idx_metrics_leverage", "company_metrics", ["leverage_ratio"])
    op.create_index("idx_metrics_subordination", "company_metrics", ["subordination_risk"])
    op.create_index("idx_metrics_maturity", "company_metrics", ["nearest_maturity"])
    op.create_index("idx_metrics_rating", "company_metrics", ["rating_bucket"])
    op.create_index(
        "idx_metrics_sector_leverage", "company_metrics", ["sector", "leverage_ratio"]
    )
    op.create_index(
        "idx_metrics_sector_subordination",
        "company_metrics",
        ["sector", "subordination_risk"],
    )
    op.create_index(
        "idx_metrics_rating_leverage",
        "company_metrics",
        ["rating_bucket", "leverage_ratio"],
    )
    op.create_index(
        "idx_metrics_risk_flags",
        "company_metrics",
        ["subordination_risk", "has_structural_sub", "has_unrestricted_subs"],
    )


def downgrade() -> None:
    # Drop denormalized tables
    op.drop_table("company_metrics")
    op.drop_table("company_cache")

    # Drop core tables (in reverse order of dependencies)
    op.drop_table("guarantees")
    op.drop_table("debt_instruments")
    op.drop_table("entities")
    op.drop_table("companies")
