"""Add extraction_metadata table for tracking data quality and provenance.

Stores extraction confidence scores, timestamps, and source filing references
at the company level for API transparency.

Revision ID: 010_add_extraction_metadata
Revises: 009_add_document_sections
Create Date: 2026-01-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "010_add_extraction_metadata"
down_revision: Union[str, None] = "009_add_document_sections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create extraction_metadata table
    # Stores per-company extraction quality metrics
    op.create_table(
        "extraction_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,  # One metadata record per company
        ),
        # Extraction quality metrics
        sa.Column("qa_score", sa.Numeric(4, 2)),  # 0.00-1.00, from QA agent
        sa.Column("extraction_method", sa.String(50)),  # 'gemini', 'claude', 'hybrid'
        sa.Column("extraction_attempts", sa.Integer, default=1),  # Number of extraction attempts

        # Field-level confidence (JSONB for flexibility)
        # Example: {"debt_instruments": 0.95, "entities": 0.87, "guarantees": 0.92}
        sa.Column("field_confidence", postgresql.JSONB, default=dict),

        # Source filing info
        sa.Column("source_10k_url", sa.String(500)),
        sa.Column("source_10k_date", sa.Date),
        sa.Column("source_10q_url", sa.String(500)),
        sa.Column("source_10q_date", sa.Date),

        # Timestamps
        sa.Column("structure_extracted_at", sa.DateTime(timezone=True)),  # When entities were extracted
        sa.Column("debt_extracted_at", sa.DateTime(timezone=True)),  # When debt was extracted
        sa.Column("financials_extracted_at", sa.DateTime(timezone=True)),  # When financials were extracted
        sa.Column("pricing_updated_at", sa.DateTime(timezone=True)),  # When pricing was last fetched

        # Data freshness indicators
        sa.Column("data_version", sa.Integer, default=1),  # Increment on re-extraction
        sa.Column("stale_after_days", sa.Integer, default=90),  # When to flag as potentially stale

        # Uncertainties and warnings (array of field names with issues)
        sa.Column("uncertainties", postgresql.JSONB, default=list),  # ["total_debt", "guarantees"]
        sa.Column("warnings", postgresql.JSONB, default=list),  # ["estimated_issue_dates", "missing_cusips"]

        # Metadata
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Index for company lookup
    op.create_index(
        "idx_extraction_metadata_company",
        "extraction_metadata",
        ["company_id"],
    )

    # Index for finding stale data
    op.create_index(
        "idx_extraction_metadata_qa_score",
        "extraction_metadata",
        ["qa_score"],
    )


def downgrade() -> None:
    op.drop_index("idx_extraction_metadata_qa_score", table_name="extraction_metadata")
    op.drop_index("idx_extraction_metadata_company", table_name="extraction_metadata")
    op.drop_table("extraction_metadata")
