"""Add debt_instrument_documents junction table for linking debt to legal docs

Links debt instruments (bonds, loans) to their governing legal documents
(indentures, credit agreements) stored in document_sections.

Revision ID: 014
Revises: 013
Create Date: 2026-01-23

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create debt_instrument_documents junction table
    op.create_table(
        'debt_instrument_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('debt_instrument_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('debt_instruments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('document_section_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('document_sections.id', ondelete='CASCADE'), nullable=False),

        # Relationship type: governs, supplements, amends, related
        sa.Column('relationship_type', sa.String(30), nullable=False, server_default='governs'),

        # Matching algorithm metadata
        sa.Column('match_confidence', sa.Numeric(4, 3), nullable=True),  # 0.000 - 1.000
        sa.Column('match_method', sa.String(30), nullable=True),  # coupon_maturity, facility_type, full_text, manual
        sa.Column('match_evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),  # Signals that led to match

        # Verification status
        sa.Column('is_verified', sa.Boolean, nullable=False, server_default='false'),

        # Audit fields
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(50), nullable=True),  # 'algorithm', 'user:email@example.com', etc.
    )

    # Unique constraint: each instrument can only be linked to each document once per relationship type
    op.create_unique_constraint(
        'uq_debt_doc_instrument_document_type',
        'debt_instrument_documents',
        ['debt_instrument_id', 'document_section_id', 'relationship_type']
    )

    # Index for finding documents for a debt instrument
    op.create_index(
        'ix_debt_instrument_documents_debt_id',
        'debt_instrument_documents',
        ['debt_instrument_id']
    )

    # Index for finding debt instruments linked to a document
    op.create_index(
        'ix_debt_instrument_documents_doc_id',
        'debt_instrument_documents',
        ['document_section_id']
    )

    # Index for filtering by verification status
    op.create_index(
        'ix_debt_instrument_documents_verified',
        'debt_instrument_documents',
        ['is_verified'],
        postgresql_where=sa.text('is_verified = false')
    )

    # Index for filtering by confidence level
    op.create_index(
        'ix_debt_instrument_documents_confidence',
        'debt_instrument_documents',
        ['match_confidence']
    )

    # Composite index for common query: unverified low-confidence matches
    op.create_index(
        'ix_debt_instrument_documents_review',
        'debt_instrument_documents',
        ['is_verified', 'match_confidence'],
        postgresql_where=sa.text('is_verified = false AND match_confidence < 0.7')
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_debt_instrument_documents_review', table_name='debt_instrument_documents')
    op.drop_index('ix_debt_instrument_documents_confidence', table_name='debt_instrument_documents')
    op.drop_index('ix_debt_instrument_documents_verified', table_name='debt_instrument_documents')
    op.drop_index('ix_debt_instrument_documents_doc_id', table_name='debt_instrument_documents')
    op.drop_index('ix_debt_instrument_documents_debt_id', table_name='debt_instrument_documents')

    # Drop unique constraint
    op.drop_constraint('uq_debt_doc_instrument_document_type', 'debt_instrument_documents', type_='unique')

    # Drop table
    op.drop_table('debt_instrument_documents')
