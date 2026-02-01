"""Three-tier pricing implementation

Implements the new pricing structure:
- Pay-as-You-Go: $0/month, pay per API call ($0.05-$0.15)
- Pro: $199/month, unlimited queries (basic)
- Business: $499/month, full access + advanced features

Schema changes:
1. users: Add rate_limit_per_minute, team_seats, update tier default
2. user_credits: Add credits_purchased, credits_used, last_credit_purchase, last_credit_usage
3. usage_log: Add cost_usd, tier_at_time_of_request
4. New tables: bond_pricing_history, team_members, coverage_requests

Revision ID: 022
Revises: 021
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers
revision = '022'
down_revision = '021'
branch_labels = None
depends_on = None


def upgrade():
    # ==========================================================================
    # 1. USERS TABLE - Add new columns
    # ==========================================================================

    # Add rate_limit_per_minute column
    op.add_column(
        'users',
        sa.Column('rate_limit_per_minute', sa.Integer(), nullable=False, server_default='60')
    )

    # Add team_seats column (for Business tier)
    op.add_column(
        'users',
        sa.Column('team_seats', sa.Integer(), nullable=False, server_default='1')
    )

    # Add index on tier for filtering
    op.create_index('ix_users_tier', 'users', ['tier'])

    # Update default tier from 'free' to 'pay_as_you_go' for new users
    # (existing users keep their current tier)

    # ==========================================================================
    # 2. USER_CREDITS TABLE - Add Pay-as-You-Go tracking columns
    # ==========================================================================

    # Add credits_purchased (total dollars ever purchased)
    op.add_column(
        'user_credits',
        sa.Column('credits_purchased', sa.Numeric(12, 2), nullable=False, server_default='0')
    )

    # Add credits_used (total dollars ever consumed)
    op.add_column(
        'user_credits',
        sa.Column('credits_used', sa.Numeric(12, 2), nullable=False, server_default='0')
    )

    # Add timestamp tracking
    op.add_column(
        'user_credits',
        sa.Column('last_credit_purchase', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'user_credits',
        sa.Column('last_credit_usage', sa.DateTime(timezone=True), nullable=True)
    )

    # ==========================================================================
    # 3. USAGE_LOG TABLE - Add cost tracking
    # ==========================================================================

    # Add cost_usd for Pay-as-You-Go billing
    op.add_column(
        'usage_log',
        sa.Column('cost_usd', sa.Numeric(10, 4), nullable=True)
    )

    # Add tier_at_time_of_request for historical tracking
    op.add_column(
        'usage_log',
        sa.Column('tier_at_time_of_request', sa.String(20), nullable=True)
    )

    # Add index on tier for analytics
    op.create_index('ix_usage_log_tier', 'usage_log', ['tier_at_time_of_request'])

    # ==========================================================================
    # 4. BOND_PRICING_HISTORY TABLE - Business-only historical pricing
    # ==========================================================================

    op.create_table(
        'bond_pricing_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('debt_instrument_id', UUID(as_uuid=True),
                  sa.ForeignKey('debt_instruments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('cusip', sa.String(9), nullable=True),
        sa.Column('price_date', sa.Date(), nullable=False),
        sa.Column('price', sa.Numeric(8, 4), nullable=True),
        sa.Column('ytm_bps', sa.Integer(), nullable=True),
        sa.Column('spread_bps', sa.Integer(), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.Column('price_source', sa.String(20), nullable=False, server_default='TRACE'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('debt_instrument_id', 'price_date', name='uq_bond_pricing_history_instrument_date'),
    )

    op.create_index('idx_bond_pricing_history_instrument', 'bond_pricing_history', ['debt_instrument_id'])
    op.create_index('idx_bond_pricing_history_cusip', 'bond_pricing_history', ['cusip'])
    op.create_index('idx_bond_pricing_history_date', 'bond_pricing_history', ['price_date'])
    op.create_index('idx_bond_pricing_history_cusip_date', 'bond_pricing_history', ['cusip', 'price_date'])

    # ==========================================================================
    # 5. TEAM_MEMBERS TABLE - Business multi-seat feature
    # ==========================================================================

    op.create_table(
        'team_members',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('owner_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('member_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='member'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('invited_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('owner_id', 'member_id', name='uq_team_members_owner_member'),
    )

    op.create_index('idx_team_members_owner', 'team_members', ['owner_id'])
    op.create_index('idx_team_members_member', 'team_members', ['member_id'])

    # ==========================================================================
    # 6. COVERAGE_REQUESTS TABLE - Business custom coverage
    # ==========================================================================

    op.create_table(
        'coverage_requests',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('company_name', sa.String(255), nullable=False),
        sa.Column('ticker', sa.String(20), nullable=True),
        sa.Column('cik', sa.String(20), nullable=True),
        sa.Column('priority', sa.String(20), nullable=False, server_default='normal'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('status_notes', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_company_id', UUID(as_uuid=True),
                  sa.ForeignKey('companies.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index('idx_coverage_requests_user', 'coverage_requests', ['user_id'])
    op.create_index('idx_coverage_requests_status', 'coverage_requests', ['status'])
    op.create_index('idx_coverage_requests_priority', 'coverage_requests', ['priority'])


def downgrade():
    # Drop new tables
    op.drop_table('coverage_requests')
    op.drop_table('team_members')
    op.drop_table('bond_pricing_history')

    # Remove usage_log columns
    op.drop_index('ix_usage_log_tier', 'usage_log')
    op.drop_column('usage_log', 'tier_at_time_of_request')
    op.drop_column('usage_log', 'cost_usd')

    # Remove user_credits columns
    op.drop_column('user_credits', 'last_credit_usage')
    op.drop_column('user_credits', 'last_credit_purchase')
    op.drop_column('user_credits', 'credits_used')
    op.drop_column('user_credits', 'credits_purchased')

    # Remove users columns
    op.drop_index('ix_users_tier', 'users')
    op.drop_column('users', 'team_seats')
    op.drop_column('users', 'rate_limit_per_minute')
