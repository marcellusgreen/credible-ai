"""Add authentication tables: users, user_credits, usage_log

Revision ID: 013
Revises: 012
Create Date: 2026-01-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('email', sa.String(255), unique=True, nullable=False),
        sa.Column('api_key_hash', sa.String(64), nullable=False),  # SHA-256 hash of API key
        sa.Column('api_key_prefix', sa.String(8), nullable=False),  # First 8 chars for display (ds_xxxx...)
        sa.Column('tier', sa.String(20), server_default='free', nullable=False),  # free, starter, growth, scale, enterprise
        sa.Column('stripe_customer_id', sa.String(255), nullable=True),  # For Stripe billing
        sa.Column('stripe_subscription_id', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Create indexes for users
    op.create_index('ix_users_email', 'users', ['email'])
    op.create_index('ix_users_api_key_hash', 'users', ['api_key_hash'])
    op.create_index('ix_users_stripe_customer_id', 'users', ['stripe_customer_id'])

    # Create user_credits table
    op.create_table(
        'user_credits',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('credits_remaining', sa.Numeric(12, 2), server_default='1000', nullable=False),
        sa.Column('credits_monthly_limit', sa.Integer, server_default='1000', nullable=False),
        sa.Column('overage_credits_used', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('billing_cycle_start', sa.Date, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Create usage_log table
    op.create_table(
        'usage_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('endpoint', sa.String(100), nullable=False),
        sa.Column('method', sa.String(10), nullable=False),  # GET, POST, etc.
        sa.Column('credits_used', sa.Numeric(10, 2), nullable=False),
        sa.Column('response_status', sa.Integer, nullable=True),
        sa.Column('response_time_ms', sa.Integer, nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),  # IPv6 can be up to 45 chars
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Create indexes for usage_log
    op.create_index('ix_usage_log_user_id', 'usage_log', ['user_id'])
    op.create_index('ix_usage_log_user_date', 'usage_log', ['user_id', 'created_at'])
    op.create_index('ix_usage_log_created_at', 'usage_log', ['created_at'])
    op.create_index('ix_usage_log_endpoint', 'usage_log', ['endpoint'])


def downgrade() -> None:
    # Drop usage_log
    op.drop_index('ix_usage_log_endpoint', table_name='usage_log')
    op.drop_index('ix_usage_log_created_at', table_name='usage_log')
    op.drop_index('ix_usage_log_user_date', table_name='usage_log')
    op.drop_index('ix_usage_log_user_id', table_name='usage_log')
    op.drop_table('usage_log')

    # Drop user_credits
    op.drop_table('user_credits')

    # Drop users
    op.drop_index('ix_users_stripe_customer_id', table_name='users')
    op.drop_index('ix_users_api_key_hash', table_name='users')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')
