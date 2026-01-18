"""Add document_sections table for full-text search across SEC filings.

Enables searching across extracted sections from 10-K, 10-Q, and 8-K filings
with PostgreSQL native full-text search using GIN indexes.

Revision ID: 009_add_document_sections
Revises: 008_add_issue_date_estimated
Create Date: 2026-01-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "009_add_document_sections"
down_revision: Union[str, None] = "008_add_issue_date_estimated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create document_sections table
    op.create_table(
        "document_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Document metadata
        sa.Column("doc_type", sa.String(50), nullable=False),  # '10-K', '10-Q', '8-K'
        sa.Column("filing_date", sa.Date, nullable=False),
        sa.Column(
            "section_type", sa.String(50), nullable=False
        ),  # 'debt_footnote', 'exhibit_21', etc.
        sa.Column("section_title", sa.String(255)),  # Extracted title
        # Content
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_length", sa.Integer, nullable=False),
        # Full-text search vector (auto-computed via trigger)
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR,
        ),
        # Source reference
        sa.Column("sec_filing_url", sa.String(500)),
        # Timestamps
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

    # GIN index on search_vector - critical for FTS performance
    op.create_index(
        "idx_document_sections_search_vector",
        "document_sections",
        ["search_vector"],
        postgresql_using="gin",
    )

    # B-tree indexes for common filter columns
    op.create_index(
        "idx_document_sections_company",
        "document_sections",
        ["company_id"],
    )
    op.create_index(
        "idx_document_sections_doc_type",
        "document_sections",
        ["doc_type"],
    )
    op.create_index(
        "idx_document_sections_section_type",
        "document_sections",
        ["section_type"],
    )
    op.create_index(
        "idx_document_sections_filing_date",
        "document_sections",
        ["filing_date"],
    )

    # Composite index for common query patterns (company + doc_type + section_type)
    op.create_index(
        "idx_document_sections_company_doc_section",
        "document_sections",
        ["company_id", "doc_type", "section_type"],
    )

    # Create trigger function to auto-compute search_vector on INSERT/UPDATE
    op.execute("""
        CREATE OR REPLACE FUNCTION document_sections_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', COALESCE(NEW.section_title, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(NEW.content, '')), 'B');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Create trigger to call the function
    op.execute("""
        CREATE TRIGGER document_sections_search_vector_trigger
        BEFORE INSERT OR UPDATE ON document_sections
        FOR EACH ROW
        EXECUTE FUNCTION document_sections_search_vector_update();
    """)


def downgrade() -> None:
    # Drop trigger first
    op.execute("DROP TRIGGER IF EXISTS document_sections_search_vector_trigger ON document_sections")
    op.execute("DROP FUNCTION IF EXISTS document_sections_search_vector_update()")

    # Drop indexes
    op.drop_index("idx_document_sections_company_doc_section", table_name="document_sections")
    op.drop_index("idx_document_sections_filing_date", table_name="document_sections")
    op.drop_index("idx_document_sections_section_type", table_name="document_sections")
    op.drop_index("idx_document_sections_doc_type", table_name="document_sections")
    op.drop_index("idx_document_sections_company", table_name="document_sections")
    op.drop_index("idx_document_sections_search_vector", table_name="document_sections")

    # Drop table
    op.drop_table("document_sections")
