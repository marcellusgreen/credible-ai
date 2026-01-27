"""Add covenant relationship data tables and columns

Adds:
1. conditions JSONB column to guarantees table (release/add triggers)
2. non_guarantor_disclosure JSONB column to company_metrics table
3. cross_default_links table for inter-debt relationships

Revision ID: 015
Revises: 014
Create Date: 2026-01-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '015'
down_revision = '014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add conditions JSONB to guarantees table (release/add triggers for guarantees)
    op.add_column(
        'guarantees',
        sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=True)
    )

    # 2. Add non_guarantor_disclosure JSONB to company_metrics table
    # Stores "Non-guarantor subs own X% of EBITDA" type disclosures
    op.add_column(
        'company_metrics',
        sa.Column('non_guarantor_disclosure', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )

    # 3. Create cross_default_links table for inter-debt relationships
    op.create_table(
        'cross_default_links',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_debt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('debt_instruments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('target_debt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('debt_instruments.id', ondelete='CASCADE'), nullable=True),

        # Relationship type: cross_default, cross_acceleration, pari_passu
        sa.Column('relationship_type', sa.String(30), nullable=False),

        # Threshold details
        sa.Column('threshold_amount', sa.BigInteger, nullable=True),  # in cents
        sa.Column('threshold_description', sa.Text, nullable=True),

        # Flags
        sa.Column('is_bilateral', sa.Boolean, server_default='false', nullable=False),

        # Confidence and evidence
        sa.Column('confidence', sa.Numeric(4, 3), nullable=True),  # 0.000 - 1.000
        sa.Column('source_document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('document_sections.id'), nullable=True),
        sa.Column('evidence', sa.Text, nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Unique constraint: each source/target/type combination should be unique
    op.create_unique_constraint(
        'uq_cross_default_source_target_type',
        'cross_default_links',
        ['source_debt_id', 'target_debt_id', 'relationship_type']
    )

    # Index for finding links from a debt instrument
    op.create_index(
        'ix_cross_default_links_source',
        'cross_default_links',
        ['source_debt_id']
    )

    # Index for finding links to a debt instrument
    op.create_index(
        'ix_cross_default_links_target',
        'cross_default_links',
        ['target_debt_id']
    )

    # Index for filtering by relationship type
    op.create_index(
        'ix_cross_default_links_type',
        'cross_default_links',
        ['relationship_type']
    )

    # Composite index for common query: find all cross-default links for a debt
    op.create_index(
        'ix_cross_default_links_source_type',
        'cross_default_links',
        ['source_debt_id', 'relationship_type']
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_cross_default_links_source_type', table_name='cross_default_links')
    op.drop_index('ix_cross_default_links_type', table_name='cross_default_links')
    op.drop_index('ix_cross_default_links_target', table_name='cross_default_links')
    op.drop_index('ix_cross_default_links_source', table_name='cross_default_links')

    # Drop unique constraint
    op.drop_constraint('uq_cross_default_source_target_type', 'cross_default_links', type_='unique')

    # Drop cross_default_links table
    op.drop_table('cross_default_links')

    # Remove non_guarantor_disclosure from company_metrics
    op.drop_column('company_metrics', 'non_guarantor_disclosure')

    # Remove conditions from guarantees
    op.drop_column('guarantees', 'conditions')
